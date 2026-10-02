"""External playback state machine; Roon owns audio, Kalinka owns arbitration."""

import asyncio
import base64
import io
import logging
from collections import OrderedDict

from kalinka_plugin_sdk.direct_playback import HoldEnded, TransportKind
from kalinka_plugin_sdk.inputmodule import ContentInfo

from .metadata import alsa_output, digest, playback_state

log = logging.getLogger(__name__)


class Service:
    def __init__(self, playback, bridge, client, output_id, hw_params=""):
        self.playback, self.bridge, self.client = playback, bridge, client
        self.output_id, self.hw_params = output_id, hw_params
        self.session = None
        self.core_id = None
        self.outputs = {}
        self.status = "Authorize Kalinka in Roon Settings → Extensions"
        self.suppressed = False
        self.previous_state = None
        self.zone = None
        self.art_keys = OrderedDict()
        self.art_cache = OrderedDict()
        self._stopping = False
        # On startup/reconnect clear any playback from the previous lifetime.
        # A stale playing snapshot must not interrupt a newer Kalinka source.
        self._resync = True
        self.waiting_for_core = False

    async def run(self):
        await self.client.start()
        await self.bridge.start()
        while True:
            event = await asyncio.wait_for(self.client.events.get(), 15)
            kind = event.get("event")
            if kind == "closed":
                raise RuntimeError("Roon extension exited; reconnecting")
            if kind == "paired":
                self.waiting_for_core = False
                self.core_id = event["core_id"]
                self.status = "Connected; select this device's Roon output in Kalinka"
                await self.bridge.start()
            elif kind == "disconnected":
                self.waiting_for_core = True
                self.core_id = None
                self.outputs.clear()
                self.zone = None
                self.previous_state = None
                self._resync = True
                self.status = (
                    "Roon Server disconnected; Bridge stopped until reconnection"
                )
                await self.bridge.stop()
                await self.release()
            elif kind == "zones" and event.get("core_id") == self.core_id:
                await self.update(event.get("zones", []))
            elif (
                kind == "heartbeat"
                and not self.waiting_for_core
                and not self.bridge.running
            ):
                raise RuntimeError(
                    "Roon Bridge exited; check its dependency/startup log"
                )

    async def update(self, zones):
        self.outputs = {
            output["output_id"]: output.get("display_name") or "Unnamed Roon output"
            for zone in zones
            for output in zone.get("outputs", [])
        }
        zone = (
            next(
                (
                    z
                    for z in zones
                    if any(
                        o["output_id"] == self.output_id for o in z.get("outputs", [])
                    )
                ),
                None,
            )
            if self.output_id
            else None
        )
        self.zone = zone
        if zone is None:
            if self.session:
                # The output was disabled/removed; control may already be gone.
                await self.stop_audio()
                await self.release()
            self.previous_state = None
            self.status = (
                "Select this device's output in Kalinka"
                if not self.output_id
                else "Selected Roon output is unavailable"
            )
            return
        state = zone.get("state")
        previous, self.previous_state = self.previous_state, state
        if state not in ("playing", "loading"):
            # Pause can retain ALSA in Roon. Explicit stop releases the device.
            if self.session or (state == "paused" and previous != state):
                await self.stop_audio()
                await self.release()
            if state in ("paused", "stopped"):
                self.suppressed = False
                self._resync = False
            self.status = "Ready as Roon endpoint"
            return
        if self._resync:
            self._resync = False
            self.suppressed = True
            await self.stop_audio()
            return
        if self.suppressed or self._stopping:
            return
        if not self.bridge.running:
            return
        if self.session is None:
            self.session = await self.playback.acquire("Roon endpoint", self)
        if not self.session.active:
            self.session = None
            return
        key = (zone.get("now_playing") or {}).get("image_key")
        art_url = None
        if key:
            asset = digest(f"{self.core_id}\n{key}")
            self.art_keys[asset] = (self.core_id, key)
            self.art_keys.move_to_end(asset)
            while len(self.art_keys) > 32:
                old, _ = self.art_keys.popitem(last=False)
                self.art_cache.pop(old, None)
            art_url = f"/content/roon/{asset}"
        try:
            self.session.report(
                playback_state(zone, art_url, alsa_output(self.hw_params))
            )
        except HoldEnded:
            self.session = None
        self.status = f"Roon endpoint · {zone.get('display_name', 'Roon')} · RAAT"

    async def stop_audio(self):
        self._stopping = True
        try:
            await self.client.request("control", control="stop")
        except (RuntimeError, OSError, asyncio.TimeoutError):
            # There is no API acknowledgement: forcibly close our own RAAT.
            log.warning("Roon stop failed; terminating managed Bridge to free audio")
            await self.bridge.stop()
        finally:
            self._stopping = False

    async def release(self):
        session, self.session = self.session, None
        if session:
            await session.release()

    async def on_revoked(self, reason):
        # Invalidate immediately; late playing/seek events must not take back
        # ownership. Only a quiet -> playing transition can re-arm it.
        self.session = None
        self.suppressed = True
        await self.stop_audio()

    async def on_command(self, request):
        if self.suppressed or self.session is None or not self.session.active:
            return
        if request.kind is TransportKind.SEEK:
            await self.client.request("seek", seconds=(request.position_ms or 0) / 1000)
        else:
            controls = {
                TransportKind.PAUSE: "pause",
                TransportKind.RESUME: "play",
                TransportKind.NEXT: "next",
                TransportKind.PREV: "previous",
            }
            await self.client.request("control", control=controls[request.kind])

    async def content(self, asset):
        pair = self.art_keys.get(asset)
        if pair is None or pair[0] != self.core_id:
            return None
        if asset not in self.art_cache:
            result = await self.client.request("image", key=pair[1], timeout=2)
            if result.get("mime") != "image/jpeg":
                return None
            data = base64.b64decode(result["data"], validate=True)
            if len(data) > 2 * 1024 * 1024:
                return None
            if self.art_keys.get(asset) != pair or self.core_id != pair[0]:
                return None
            self.art_cache[asset] = data
            while len(self.art_cache) > 32:
                self.art_cache.popitem(last=False)
        data = self.art_cache[asset]
        return ContentInfo(
            mime_type="image/jpeg",
            size=len(data),
            reader=lambda: io.BytesIO(data),
            cacheable=True,
        )

    async def close(self):
        # Stop selected Roon zone even when paused, then tear down both children.
        try:
            if self.zone:
                await self.stop_audio()
        finally:
            await self.bridge.stop()
            await self.release()
            await self.client.close()
