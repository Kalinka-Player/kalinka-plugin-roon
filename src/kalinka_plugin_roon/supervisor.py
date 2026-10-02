import asyncio
import fcntl
import logging

from .client import RoonClient
from .install import install_bridge, install_extension
from .process import Bridge, run_checked
from .service import Service

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, playback, config, directory):
        self.playback, self.config, self.directory = playback, config, directory
        self.service = None
        self.status = "Installing Roon Bridge from Roon Labs"
        self.error = None
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            await self._supervise()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error = str(error) or type(error).__name__
            log.exception("Roon supervisor could not start or clean up")

    async def _supervise(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / "plugin.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.error = "Another Kalinka Roon plugin is using this state directory"
                return
            while True:
                try:
                    self.status = "Preparing Roon Bridge and extension"
                    bridge_dir = await install_bridge(self.directory / "runtime")
                    extension = await install_extension(self.directory)
                    await run_checked([bridge_dir / "check.sh"], cwd=bridge_dir)
                    self.service = Service(
                        self.playback,
                        Bridge(bridge_dir, self.directory / "data"),
                        RoonClient(
                            extension, self.directory / "pairing", self.config.output_id
                        ),
                        self.config.output_id,
                        self.config.alsa_hw_params,
                    )
                    self.error = None
                    await self.service.run()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.error = str(error) or type(error).__name__
                    log.exception("Roon plugin failed; retrying in 10 seconds")
                finally:
                    if self.service:
                        await self.service.close()
                        self.service = None
                await asyncio.sleep(10)

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
