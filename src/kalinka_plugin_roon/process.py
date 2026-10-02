"""Bounded process teardown, including the RAAT child processes."""

import asyncio
import logging
import os
import signal
from pathlib import Path

log = logging.getLogger(__name__)


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
    def __init__(self, directory: Path, data: Path):
        self.directory, self.data = directory, data
        self.process = None
        self._logs = None

    @property
    def running(self):
        return self.process is not None and self.process.returncode is None

    async def start(self):
        if self.running:
            return
        await self.stop()
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process = await asyncio.create_subprocess_exec(
            str(self.directory / "start.sh"),
            cwd=self.directory,
            env={**os.environ, "ROON_DATAROOT": str(self.data)},
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._logs = asyncio.create_task(self._drain(self.process))

    async def _drain(self, process):
        while line := await process.stdout.readline():
            log.info("Roon Bridge: %s", line.decode(errors="replace").rstrip())

    async def stop(self):
        process, self.process = self.process, None
        await terminate(process)
        if self._logs:
            self._logs.cancel()
            await asyncio.gather(self._logs, return_exceptions=True)
            self._logs = None
