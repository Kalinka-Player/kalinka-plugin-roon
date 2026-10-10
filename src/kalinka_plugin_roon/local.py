"""Identify outputs from this managed Bridge's own ALSA settings.

RAAT output IDs currently encode a device GUID in .NET byte order after 1701.
This is an observed Roon convention, not a public API contract: only accept an
ID when the authorized transport subscription advertises that exact value.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from uuid import UUID

log = logging.getLogger(__name__)


def owner_group(status):
    owner = re.search(r"owner_pid\s*:\s*(\d+)", status)
    if owner:
        stat = Path(f"/proc/{owner[1]}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[2])
    return None


def running_on_bridge(hw_params, process_group):
    if not hw_params or process_group is None:
        return None
    try:
        status = Path(hw_params).with_name("status").read_text()
        return "state: RUNNING" in status and owner_group(status) == process_group
    except (OSError, ValueError, IndexError):
        return None


async def wait_released(hw_params, timeout=0.8, process_group=None):
    """The Core acknowledges Stop before RAAT finishes closing ALSA."""
    if not hw_params:
        return False
    path = Path(hw_params)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            if path.read_text().strip() == "closed":
                return True
            if process_group is not None:
                status = path.with_name("status").read_text()
                try:
                    group = owner_group(status)
                except FileNotFoundError:
                    return True
                if group is not None and group != process_group:
                    return True
        except OSError:
            return False
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.02)


class LocalOutputs:
    def __init__(self, data, asound=Path("/proc/asound")):
        self.settings = Path(data) / "RAATServer" / "Settings"
        self.log_path = Path(data) / "RAATServer" / "Logs" / "RAATServer_log.txt"
        self.asound = Path(asound)
        self.names = {}

    def checkpoint(self):
        try:
            stat = self.log_path.stat()
            return stat.st_ino, stat.st_size
        except OSError:
            return None

    def busy_since(self, checkpoint, output_id):
        name = self.names.get(output_id)
        if not name:
            return False
        try:
            with self.log_path.open("rb") as reader:
                stat = self.log_path.stat()
                start = (
                    checkpoint[1]
                    if checkpoint
                    and checkpoint[0] == stat.st_ino
                    and checkpoint[1] <= stat.st_size
                    else 0
                )
                reader.seek(max(start, stat.st_size - 65536))
                return any(
                    f"[RAAT::{name}]" in line
                    and "error in snd_pcm_open: Device or resource busy" in line
                    for line in reader.read(65536).decode(errors="replace").splitlines()
                )
        except OSError:
            return False

    def discover(self):
        outputs = {}
        for path in self.settings.glob("device_*.json"):
            try:
                config = json.loads(path.read_text())
                output = config.get("output", {})
                if output.get("type") != "alsa":
                    continue
                ident = "1701" + UUID(config["unique_id"]).bytes_le.hex()
                self.names[ident] = output.get("name", "")
                device = output.get("device", "")
                match = re.fullmatch(r"hw:CARD=([^,]+),DEV=(\d+)", device)
                hw_params = ""
                if match:
                    card_id, pcm = match.groups()
                    for card in self.asound.glob("card[0-9]*"):
                        if (card / "id").read_text().strip() == card_id:
                            hw_params = str(card / f"pcm{pcm}p/sub0/hw_params")
                            break
                outputs[ident] = hw_params
            except (OSError, ValueError, KeyError, TypeError):
                log.debug("Cannot identify local Roon device from %s", path)
        return outputs
