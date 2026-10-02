import asyncio
import base64
from unittest.mock import AsyncMock, Mock

import pytest
from kalinka_plugin_sdk.direct_playback import (
    RevokeReason,
    TransportKind,
    TransportRequest,
)

from kalinka_plugin_roon.service import Service


def zone(state="playing", output="local", zone_id="zone-1", **now):
    return {
        "zone_id": zone_id,
        "display_name": "Living room",
        "state": state,
        "outputs": [{"output_id": output, "display_name": "Kalinka DAC"}],
        "now_playing": {
            "length": 240,
            "seek_position": 12.5,
            "image_key": "art/key",
            "three_line": {"line1": "Song", "line2": "Artist", "line3": "Album"},
            **now,
        },
    }


@pytest.fixture
async def service():
    session = Mock(active=True, release=AsyncMock())
    playback = Mock(acquire=AsyncMock(return_value=session))
    bridge = Mock(running=True, start=AsyncMock(), stop=AsyncMock())
    client = Mock(
        request=AsyncMock(),
        start=AsyncMock(),
        close=AsyncMock(),
        events=asyncio.Queue(),
    )
    service = Service(playback, bridge, client, "local")
    service.core_id = "core"
    await service.update([zone("stopped")])
    return service


async def test_playback_metadata_progress_and_artwork(service):
    await service.update([zone()])
    service.playback.acquire.assert_awaited_once_with("Roon endpoint", service)
    state = service.session.report.call_args.args[0]
    assert state.current_track.title == "Song"
    assert state.current_track.performer.name == "Artist"
    assert state.current_track.album.title == "Album"
    assert state.position == 12500
    assert state.audio_info.sample_rate == 0
    assert state.audio_info.output is None
    assert state.stream_url is None
    assert state.current_track.album.image.large.startswith("/content/roon/")
    await service.update([zone(seek_position=16)])
    assert service.session.report.call_args.args[0].position == 16000
    assert service.playback.acquire.await_count == 1


async def test_ignores_other_devices_and_follows_regrouped_output(service):
    await service.update([zone(output="someone-else")])
    service.playback.acquire.assert_not_called()
    await service.update([zone(zone_id="new-group")])
    assert service.session is not None


@pytest.mark.parametrize("reason", list(RevokeReason))
async def test_takeover_stops_roon_and_late_events_cannot_reacquire(service, reason):
    await service.update([zone()])
    await service.on_revoked(reason)
    service.client.request.assert_awaited_once_with("control", control="stop")
    await service.update([zone(seek_position=17)])
    assert service.playback.acquire.await_count == 1
    assert service.session is None
    await service.update([zone("stopped")])
    await service.update([zone()])
    assert service.playback.acquire.await_count == 2


async def test_failed_stop_kills_bridge_before_callback_returns(service):
    await service.update([zone()])
    service.client.request.side_effect = asyncio.TimeoutError
    await service.on_revoked(RevokeReason.QUEUE_PLAY)
    service.bridge.stop.assert_awaited_once()


@pytest.mark.parametrize("state", ["paused", "stopped"])
async def test_remote_stop_or_pause_sends_stop_releases_and_does_not_loop(
    service, state
):
    await service.update([zone()])
    session = service.session
    await service.update([zone(state)])
    service.client.request.assert_awaited_once_with("control", control="stop")
    session.release.assert_awaited_once()
    await service.update([zone(state)])
    assert service.client.request.await_count == 1


async def test_output_removed_stops_before_release(service):
    await service.update([zone()])
    events = []
    service.client.request.side_effect = lambda *a, **k: events.append("stop")
    service.session.release.side_effect = lambda: events.append("release")
    await service.update([])
    assert events == ["stop", "release"]


async def test_startup_playing_snapshot_is_stopped_not_adopted(service):
    service._resync = True
    await service.update([zone()])
    assert service.suppressed
    service.playback.acquire.assert_not_called()
    service.client.request.assert_awaited_once_with("control", control="stop")


async def test_transport_forwarding_and_no_commands_after_revoke(service):
    await service.update([zone()])
    await service.on_command(TransportRequest(TransportKind.SEEK, position_ms=20500))
    service.client.request.assert_awaited_with("seek", seconds=20.5)
    await service.on_command(TransportRequest(TransportKind.NEXT))
    service.client.request.assert_awaited_with("control", control="next")
    await service.on_revoked(RevokeReason.TAKEN_BACK)
    service.client.request.reset_mock()
    await service.on_command(TransportRequest(TransportKind.RESUME))
    service.client.request.assert_not_called()


async def test_artwork_is_bounded_cached_and_scoped_to_core(service):
    await service.update([zone()])
    asset = next(iter(service.art_keys))
    service.client.request.return_value = {
        "mime": "image/jpeg",
        "data": base64.b64encode(b"jpeg").decode(),
    }
    content = await service.content(asset)
    assert content.reader().read() == b"jpeg"
    assert content.size == 4
    await service.content(asset)
    assert service.client.request.await_count == 1
    assert await service.content("../../pairing.json") is None
    for index in range(50):
        await service.update([zone(image_key=str(index))])
    assert len(service.art_keys) == 32
    assert asset not in service.art_cache
    service.core_id = "different-core"
    assert await service.content(next(iter(service.art_keys))) is None


async def test_disconnect_stops_bridge_and_releases_ownership(service):
    await service.update([zone()])
    session = service.session
    service.client.events.put_nowait({"event": "disconnected"})
    service.client.events.put_nowait({"event": "closed"})
    with pytest.raises(RuntimeError, match="exited"):
        await service.run()
    service.bridge.stop.assert_awaited_once()
    session.release.assert_awaited_once()
    assert service.core_id is None


async def test_shutdown_stops_even_without_active_session(service):
    service.zone = zone("paused")
    await service.close()
    service.client.request.assert_awaited_once_with("control", control="stop")
    service.bridge.stop.assert_awaited_once()
    service.client.close.assert_awaited_once()
