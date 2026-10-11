"""External playback state machine; Roon owns audio, Kalinka owns arbitration."""

import asyncio
import base64
import io
import logging
from collections import OrderedDict

from kalinka_plugin_sdk.direct_playback import HoldEnded, TransportKind
from kalinka_plugin_sdk.inputmodule import ContentInfo

from . import volume
from .local import running_on_bridge, wait_released
from .metadata import alsa_output, digest, playback_state

log = logging.getLogger(__name__)


class Service:
    def __init__(
        self, playback, bridge, client, output_id, hw_params="", local_outputs=None
    ):
        self.playback, self.bridge, self.client = playback, bridge, client
        self.output_id, self.hw_params = output_id, hw_params
        self.configured_output_id = output_id
        self.configured_hw_params = hw_params
        self.local_outputs = local_outputs
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
        self._stop_task = None
        self._ignore_revision = 0
        self._retry_deadline = 0
        self._retried = False
        self._busy_checkpoint = local_outputs.checkpoint() if local_outputs else None
        # On startup/reconnect clear any playback from the previous lifetime.
        # A stale playing snapshot must not interrupt a newer Kalinka source.
        self._resync = True
        self.waiting_for_core = False
        self._volume_supported = getattr(playback, "supports_volume", False) is True

    async def run(self):
        await self.client.start()
        await self.bridge.start()
        while True:
            event = await asyncio.wait_for(self.client.events.get(), 15)
            kind = event.get("event")
            if kind == "closed":
                raise RuntimeError("Roon extension exited; reconnecting")
            if kind == "paired":
                self._ignore_revision = 0
                self.waiting_for_core = False
                self.core_id = event["core_id"]
                self.status = "Connected; identifying this Bridge's local output"
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
                if (
                    event.get("revision", self._ignore_revision + 1)
                    > self._ignore_revision
                ):
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
        if self.local_outputs:
            local = self.local_outputs.discover()
            candidates = self.outputs.keys() & local.keys()
            selected = self.configured_output_id
            if not selected:
                active = {
                    output["output_id"]
                    for zone in zones
                    if zone.get("state") in ("playing", "loading")
                    for output in zone.get("outputs", [])
                    if output["output_id"] in candidates
                }
                # Roon chooses the playback destination. Follow an active local
                # output; retain our choice when several play or all are idle.
                # A sorted fallback is independent of discovery/event ordering.
                preferred = active or candidates
                selected = (
                    self.output_id
                    if self.output_id in preferred
                    else min(preferred, default="")
                )
            if selected != self.output_id:
                if self.session:
                    await self.stop_audio()
                    await self.release()
                await self.client.request("select_output", output_id=selected)
                log.info(
                    "Roon output selected: %s (local candidates: %d)",
                    selected or "none",
                    len(candidates),
                )
                self.output_id = selected
                self.previous_state = None
                self.suppressed = False
                self._retry_deadline = 0
                # Preserve startup/reconnect resync, but adopt a new output
                # chosen by playback during this connection without stopping it.
            self.hw_params = self.configured_hw_params or local.get(self.output_id, "")
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
                "Enable a local output in Roon Settings → Audio (or choose an output in advanced settings)"
                if not self.output_id
                else "Selected Roon output is unavailable"
            )
            return
        state = zone.get("state")
        previous, self.previous_state = self.previous_state, state
        if state != previous:
            log.info(
                "Roon state %s → %s; output=%s suppressed=%s held=%s",
                previous,
                state,
                self.output_id,
                self.suppressed,
                self.session is not None,
            )
        if state not in ("playing", "loading"):
            if await self.recover_busy(zone):
                return
            if (
                state == "paused"
                and self.session
                and self.session.active
                and not self.suppressed
            ):
                # Pause changes transport state, not the selected source. Keep
                # the hold so Kalinka's Play still resumes Roon and the saved
                # queue stays inactive. Only Stop/source takeover releases it.
                self._retry_deadline = 0
            else:
                if self.session:
                    await self.stop_audio()
                    await self.release()
                self.suppressed = False
                self._resync = False
                self.status = "Ready as Roon endpoint"
                if self.local_outputs:
                    self._busy_checkpoint = self.local_outputs.checkpoint()
                return
            if self.local_outputs:
                self._busy_checkpoint = self.local_outputs.checkpoint()
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
            self._retry_deadline = asyncio.get_running_loop().time() + 3
            self._retried = False
            options = {"volume_control": self} if self._volume_supported else {}
            self.session = await self.playback.acquire("Roon endpoint", self, **options)
        if not self.session.active:
            self.session = None
            return
        self.report(zone)

    def report(self, zone):
        group = self.bridge.process.pid if self.bridge.process else None
        audible = running_on_bridge(self.hw_params, group)
        if audible:
            self._retry_deadline = 0
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
                playback_state(
                    # Transport state comes from Roon. Local /proc ownership
                    # only validates output measurements; a reused RAAT process
                    # or an unobservable device must not mean endless buffering.
                    zone,
                    art_url,
                    alsa_output(self.hw_params) if audible else None,
                )
            )
            if self._volume_supported:
                self.session.report_volume(volume.state(self.output_volume()))
        except HoldEnded:
            self.session = None
        self.status = f"Roon endpoint · {zone.get('display_name', 'Roon')} · RAAT"

    def output_volume(self):
        return next(
            (
                o.get("volume")
                for o in (self.zone or {}).get("outputs", [])
                if o["output_id"] == self.output_id
            ),
            None,
        )

    async def set_volume(self, percent):
        if not self.session or not self.session.active or self.suppressed:
            raise HoldEnded("Roon no longer owns playback")
        current = self.output_volume()
        await self.client.request(
            "volume",
            value=volume.value(current, percent),
            output_id=self.output_id,
            unmute=bool(current.get("is_muted")) and percent > 0,
        )

    async def stop_audio(self):
        if self._stop_task is None or self._stop_task.done():
            self._stop_task = asyncio.create_task(self._stop_audio())
        await asyncio.shield(self._stop_task)

    async def _stop_audio(self):
        self._stopping = True
        started = asyncio.get_running_loop().time()
        phase = "Stop acknowledgement"
        try:
            # Leave time to terminate Bridge within the host's three-second budget.
            async with asyncio.timeout(2.0):
                await self.client.request("control", control="stop", timeout=2.0)
                phase = "ALSA release"
                group = self.bridge.process.pid if self.bridge.process else None
                if self.hw_params and not await wait_released(
                    self.hw_params, process_group=group
                ):
                    raise RuntimeError("Roon acknowledged Stop but ALSA did not close")
            log.info(
                "Roon audio released in %.3fs",
                asyncio.get_running_loop().time() - started,
            )
        except (RuntimeError, OSError, asyncio.TimeoutError):
            # An acknowledgement alone is not evidence of DAC release.
            if not self.bridge.managed:
                log.error(
                    "Local Roon Server %s failed after %.3fs; leaving its process running",
                    phase,
                    asyncio.get_running_loop().time() - started,
                )
                raise RuntimeError(
                    "Roon Server did not confirm local audio release"
                ) from None
            log.warning(
                "Roon %s failed after %.3fs; terminating managed Bridge to free audio",
                phase,
                asyncio.get_running_loop().time() - started,
            )
            await self.bridge.stop()
        finally:
            self._stopping = False

    async def recover_busy(self, zone):
        """Retry one confirmed local ALSA collision, retaining the acquired hold."""
        if (
            zone.get("state") not in ("paused", "stopped")
            or not self.local_outputs
            or not self.session
            or not self.session.active
            or self.suppressed
            or self._stopping
            or self._retried
            or asyncio.get_running_loop().time() > self._retry_deadline
            or not self.local_outputs.busy_since(self._busy_checkpoint, self.output_id)
        ):
            return False
        self._retried = True
        log.warning(
            "Roon ALSA collision during handover; waiting for DAC and retrying Play once"
        )
        self.report({**zone, "state": "loading"})
        # A shared desktop backend can keep ALSA open until its idle timeout.
        # This is outside the revoke callback's budget; revocation still stops
        # audio promptly and prevents the delayed retry from taking over again.
        if not await wait_released(self.hw_params, timeout=6):
            log.warning("Roon handover retry abandoned: DAC did not close within 6s")
            return False
        if (
            self.suppressed
            or not self.session
            or not self.session.active
            or self._retry_deadline == 0
        ):
            return True
        try:
            await self.client.request("control", control="play")
            deadline = asyncio.get_running_loop().time() + 2
            while self.session and self.session.active and not self.suppressed:
                snapshot = await self.client.request("snapshot")
                self._ignore_revision = max(
                    self._ignore_revision, snapshot.get("revision", 0)
                )
                current = next(
                    (
                        z
                        for z in snapshot["zones"]
                        if any(
                            o["output_id"] == self.output_id
                            for o in z.get("outputs", [])
                        )
                    ),
                    None,
                )
                if (
                    current
                    and current.get("state") == "playing"
                    and running_on_bridge(
                        self.hw_params,
                        self.bridge.process.pid if self.bridge.process else None,
                    )
                ):
                    log.info("Roon handover recovered; playback restarted on local DAC")
                    await self.update(snapshot["zones"])
                    return True
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.05)
        except (RuntimeError, OSError, asyncio.TimeoutError):
            log.warning("Roon handover retry failed", exc_info=True)
        if self.suppressed or not self.session or not self.session.active:
            return True
        return False

    async def release(self):
        session, self.session = self.session, None
        if session:
            await session.release()

    async def on_revoked(self, reason):
        # Invalidate immediately; late playing/seek events must not take back
        # ownership. Only a quiet -> playing transition can re-arm it.
        self.session = None
        self.suppressed = True
        self._retry_deadline = 0
        log.info("Roon handover to Kalinka: %s", reason)
        await self.stop_audio()
        if self.previous_state == "paused":
            # Stop on an already-paused zone may produce no subscription event.
            # Establish a revision barrier before rearming: buffered pre-Stop
            # events must not reclaim the output, but a subsequent Play must.
            try:
                snapshot = await self.client.request("snapshot", timeout=0.2)
                quiet = any(
                    zone.get("state") in ("paused", "stopped")
                    and any(
                        output["output_id"] == self.output_id
                        for output in zone.get("outputs", [])
                    )
                    for zone in snapshot.get("zones", [])
                )
                if quiet:
                    self._ignore_revision = max(
                        self._ignore_revision, snapshot.get("revision", 0)
                    )
                    self.suppressed = False
            except (RuntimeError, OSError, asyncio.TimeoutError):
                log.warning("Could not refresh Roon state after paused Stop")

    async def on_command(self, request):
        if self.suppressed or self.session is None or not self.session.active:
            return
        if request.kind is TransportKind.SEEK:
            await self.client.request("seek", seconds=(request.position_ms or 0) / 1000)
        else:
            if request.kind is TransportKind.PAUSE:
                self._retry_deadline = 0
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
        except RuntimeError:
            log.warning("Closing Roon monitoring without confirmed audio release")
        finally:
            await self.bridge.stop()
            await self.release()
            await self.client.close()
