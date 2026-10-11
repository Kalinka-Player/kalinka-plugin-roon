"""Bounded process teardown, including the RAAT child processes."""

import asyncio
import logging
import os
import signal
import socket
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

log = logging.getLogger(__name__)


class ServerAudio:
    """Observe a local Core's RAAT process without owning its lifecycle."""

    managed = False

    def __init__(self, entry, raat_directory):
        self.entry, self.raat_directory = entry, raat_directory
        stat = entry.joinpath("stat").read_text().rsplit(")", 1)[1].split()
        self._started = stat[19]
        self.process = SimpleNamespace(pid=int(stat[2]))  # ALSA owner group

    @classmethod
    def find(cls, proc=Path("/proc")):
        for entry in proc.glob("[0-9]*"):
            try:
                if entry.stat().st_uid != os.getuid():
                    continue
                if entry.joinpath("exe").resolve(strict=True).stem != "RAATServer":
                    continue
                stat = entry.joinpath("stat").read_text().rsplit(")", 1)[1].split()
                parent = proc / stat[1]
                if parent.joinpath("exe").resolve(strict=True).stem != "RoonAppliance":
                    continue
                for fd in entry.joinpath("fd").iterdir():
                    try:
                        target = fd.resolve(strict=True)
                    except OSError:
                        continue
                    if (
                        target.name == "RAATServer_log.txt"
                        and target.parent.name == "Logs"
                    ):
                        return cls(entry, target.parent.parent)
            except (OSError, ValueError, IndexError):
                continue
        return None

    @property
    def running(self):
        try:
            stat = self.entry.joinpath("stat").read_text().rsplit(")", 1)[1].split()
            return stat[0] != "Z" and stat[19] == self._started
        except (OSError, IndexError):
            return False

    async def start(self):
        if not self.running:
            raise RuntimeError("Local Roon Server audio process exited; reconnecting")

    async def stop(self):
        # Service stops the selected zone through transport. Never kill a Core
        # process group: it also serves the user's library and other zones.
        pass


async def terminate(process, grace=0.5):
    if process is None:
        return
    # The group may outlive its launcher (a crashed helper or start.sh).
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), grace)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def run_checked(args, *, cwd, timeout=30):
    process = await asyncio.create_subprocess_exec(
        *map(str, args),
        cwd=cwd,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise RuntimeError(output.decode(errors="replace")[-4000:])
    finally:
        await terminate(process)


class Bridge:
    managed = True

    def __init__(self, directory: Path, data: Path):
        self.directory, self.data = directory, data
        self.process = None
        self._logs = None
        self._lock = asyncio.Lock()

    @property
    def running(self):
        return self.process is not None and self.process.returncode is None

    async def start(self):
        async with self._lock:
            await self._start()

    async def _start(self):
        if self.running:
            return
        await self._stop()
        await self._stop_orphans()
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Roon's default localhost:9004 RAAT rendezvous is shared with a local
        # Core, regardless of ROON_DATAROOT. Use a private app identity/port so
        # the Bridge actually owns its RAAT child. Keep existing output IDs and
        # diagnostic paths through aliases to the original private state.
        instance = (
            "kalinka-" + sha256(str(self.data.resolve()).encode()).hexdigest()[:12]
        )
        for name in ("RAATServer", "RoonBridge"):
            (self.data / name).mkdir(exist_ok=True, mode=0o700)
            alias = self.data / f"{name}-{instance}"
            if not alias.exists():
                alias.symlink_to(name, target_is_directory=True)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.process = await asyncio.create_subprocess_exec(
            str(self.directory / "start.sh"),
            f"-app={instance}",
            f"-refcountport={port}",
            cwd=self.directory,
            env={**os.environ, "ROON_DATAROOT": str(self.data)},
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._logs = asyncio.create_task(self._drain(self.process))

    def _owned_groups(self):
        """Find only this private installation's processes using our data root.

        A killed server can leave Bridge alive. Roon then connects a new
        launcher to the old RAAT process, outside the new launcher's group.
        The supervisor holds the data-directory lock while this runs.
        """
        groups = set()
        runtime = (self.directory / "Bridge").resolve()
        expected_data = f"ROON_DATAROOT={self.data}".encode()
        for entry in Path("/proc").glob("[0-9]*"):
            try:
                if entry.stat().st_uid != os.getuid():
                    continue
                if not (entry / "exe").resolve(strict=True).is_relative_to(runtime):
                    continue
                if expected_data not in (entry / "environ").read_bytes().split(b"\0"):
                    continue
                group = os.getpgid(int(entry.name))
                if group == os.getpgrp():
                    continue
                leader = Path(f"/proc/{group}")
                if leader.exists():
                    command = (leader / "cmdline").read_bytes().split(b"\0")
                    if str(
                        self.directory / "start.sh"
                    ).encode() not in command and not (leader / "exe").resolve(
                        strict=True
                    ).is_relative_to(runtime):
                        continue
                groups.add(group)
            except (OSError, ValueError):
                continue
        return groups

    async def _stop_orphans(self):
        groups = self._owned_groups()
        if not groups:
            return
        log.warning("Cleaning up stale managed Roon Bridge groups: %s", sorted(groups))
        for group in groups:
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = asyncio.get_running_loop().time() + 0.5
        while groups & self._owned_groups():
            if asyncio.get_running_loop().time() >= deadline:
                for group in groups & self._owned_groups():
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                break
            await asyncio.sleep(0.05)

    async def _drain(self, process):
        while line := await process.stdout.readline():
            log.info("Roon Bridge: %s", line.decode(errors="replace").rstrip())

    async def stop(self):
        # Disconnect and playback revocation may arrive together. Every
        # caller waits for the actual process teardown before handing over.
        async with self._lock:
            await self._stop()

    async def _stop(self):
        process, self.process = self.process, None
        await terminate(process)
        if self._logs:
            self._logs.cancel()
            await asyncio.gather(self._logs, return_exceptions=True)
            self._logs = None
