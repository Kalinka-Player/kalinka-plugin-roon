"""Roon's official Node extension API over private child-process pipes."""

import asyncio
import json
import logging
import os

from .process import terminate

log = logging.getLogger(__name__)


class RoonClient:
    def __init__(self, extension, state_dir, output_id):
        self.extension, self.state_dir, self.output_id = extension, state_dir, output_id
        self.events = asyncio.Queue(maxsize=128)
        self.pending = {}
        self.sequence = 0
        self.process = None
        self._tasks = []

    async def start(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process = await asyncio.create_subprocess_exec(
            "node",
            str(self.extension / "main.js"),
            cwd=self.state_dir,
            env={**os.environ, "ROON_OUTPUT_ID": self.output_id},
            start_new_session=True,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=3 * 1024 * 1024,
        )
        self._tasks = [
            asyncio.create_task(self._read()),
            asyncio.create_task(self._logs()),
        ]

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if message.get("error"):
                            future.set_exception(RuntimeError(str(message["error"])))
                        else:
                            future.set_result(message)
                else:
                    # Never block replies behind a stalled event consumer.
                    # Overload tears down Bridge through the supervisor.
                    self.events.put_nowait(message)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Roon extension disconnected"))
            if self.events.full():
                while not self.events.empty():
                    self.events.get_nowait()
            self.events.put_nowait({"event": "closed"})

    async def _logs(self):
        while line := await self.process.stderr.readline():
            log.info("Roon extension: %s", line.decode(errors="replace").rstrip())

    async def request(self, method, *, timeout=1.0, **arguments):
        if not self.process or self.process.returncode is not None:
            raise RuntimeError("Roon extension is not running")
        self.sequence += 1
        ident = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            self.process.stdin.write(
                (
                    json.dumps({"id": ident, "method": method, **arguments}) + "\n"
                ).encode()
            )
            async with asyncio.timeout(timeout):
                await self.process.stdin.drain()
                return await future
        finally:
            self.pending.pop(ident, None)

    async def close(self):
        await terminate(self.process)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
