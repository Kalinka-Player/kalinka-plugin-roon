import asyncio
import base64
from unittest.mock import AsyncMock, Mock, call

import pytest
from kalinka_plugin_sdk.direct_playback import (
    RevokeReason,
    TransportKind,
    TransportRequest,
)

from kalinka_plugin_roon.client import RoonClient
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
    bridge = Mock(running=True, process=None, start=AsyncMock(), stop=AsyncMock())
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
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )
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


@pytest.mark.parametrize(
    "reply_delay,released", [(1.1, True), (None, True), (1.7, False)]
)
async def test_stop_allows_slow_acknowledgement_but_still_bounds_failure(
    service, tmp_path, reply_delay, released
):
    (tmp_path / "main.js").write_text(
        """
const readline = require('node:readline');
readline.createInterface({input:process.stdin}).on('line', line => {
 const message = JSON.parse(line);
 if (message.control === 'stop' && REPLY)
   setTimeout(() => process.stdout.write(JSON.stringify({id:message.id}) + '\\n'), DELAY);
});
""".replace("REPLY", str(reply_delay is not None).lower()).replace(
            "DELAY", str((reply_delay or 0) * 1000)
        )
    )
    client = RoonClient(tmp_path, tmp_path / "state", "local")
    await client.start()
    service.client = client
    params = tmp_path / "hw_params"
    params.write_text("closed\n" if released else "rate: 44100\n")
    service.hw_params = str(params)

    async def terminate_bridge():
        await asyncio.sleep(0.5)

    service.bridge.stop.side_effect = terminate_bridge
    try:
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(service.stop_audio(), 2.8)
        elapsed = asyncio.get_running_loop().time() - started
        if reply_delay is not None and released:
            service.bridge.stop.assert_not_called()
            assert elapsed >= reply_delay
        else:
            service.bridge.stop.assert_awaited_once()
        assert elapsed < 2.8
        assert not client.pending
    finally:
        await client.close()


async def test_stop_acknowledgement_waits_for_actual_dac_release(service, tmp_path):
    params = tmp_path / "hw_params"
    params.write_text("rate: 44100\n")
    service.hw_params = str(params)
    await service.update([zone()])
    stopping = asyncio.create_task(service.on_revoked(RevokeReason.QUEUE_PLAY))
    await asyncio.sleep(0.04)
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )
    assert not stopping.done()
    params.write_text("closed\n")
    await stopping
    service.bridge.stop.assert_not_called()
    # The Stop response's subsequent paused event must not issue another Stop
    # that could overtake a new Play from the user.
    await service.update([zone("paused")])
    assert service.client.request.await_count == 1


@pytest.mark.parametrize("state", ["paused", "stopped"])
async def test_remote_stop_or_pause_sends_stop_releases_and_does_not_loop(
    service, state
):
    await service.update([zone()])
    session = service.session
    await service.update([zone(state)])
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )
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
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )


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
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )
    service.bridge.stop.assert_awaited_once()
    service.client.close.assert_awaited_once()


async def test_automatic_selection_matches_only_local_advertised_output(service):
    service.configured_output_id = service.output_id = ""
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": "/proc/asound/card1/pcm0p/sub0/hw_params"})
    )
    await service.update(
        [zone("stopped"), zone("playing", output="remote", zone_id="remote")]
    )
    assert service.output_id == "local"
    assert service.hw_params == "/proc/asound/card1/pcm0p/sub0/hw_params"
    service.client.request.assert_awaited_once_with("select_output", output_id="local")
    service.playback.acquire.assert_not_called()
    await service.update([zone()])
    assert service.session is not None


async def test_automatic_selection_defaults_to_first_local_and_ignores_remote(service):
    service.configured_output_id = service.output_id = ""
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": "", "other-local": ""})
    )
    await service.update([zone(output="remote")])
    assert not service.output_id
    local = zone("stopped")
    other = zone("stopped", output="other-local", zone_id="other")
    remote = zone(output="remote")
    await service.update([remote, other, local])
    assert service.output_id == "local"
    service.client.request.assert_awaited_once_with("select_output", output_id="local")
    await service.update([local, other, remote])
    assert service.client.request.await_count == 1
    service.playback.acquire.assert_not_called()


async def test_automatic_selection_follows_roon_playback_and_retains_idle_choice(
    service,
):
    service.configured_output_id = ""
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": "", "other-local": ""})
    )
    await service.update(
        [zone(), zone("stopped", output="other-local", zone_id="other")]
    )
    session = service.session
    service.client.request.reset_mock()
    await service.update(
        [
            zone("stopped"),
            zone(
                output="other-local",
                zone_id="other",
                three_line={"line1": "Other song"},
            ),
        ]
    )
    assert service.output_id == "other-local"
    # Stop the old target before retargeting; never stop the new playback.
    assert service.client.request.await_args_list == [
        call("control", control="stop", timeout=2.0),
        call("select_output", output_id="other-local"),
    ]
    session.release.assert_awaited_once()
    assert service.playback.acquire.await_count == 2
    assert service.session.report.call_args.args[0].current_track.title == "Other song"
    await service.update(
        [zone("stopped"), zone("stopped", output="other-local", zone_id="other")]
    )
    assert service.output_id == "other-local"
    assert service.session is None


async def test_automatic_selection_does_not_flap_when_multiple_outputs_play(service):
    service.configured_output_id = ""
    service.output_id = "other-local"
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": "", "other-local": ""})
    )
    other = zone(output="other-local", zone_id="other")
    await service.update([zone(), other])
    await service.update([other, zone()])
    assert service.output_id == "other-local"
    service.client.request.assert_not_called()
    assert service.playback.acquire.await_count == 1


async def test_automatic_selection_falls_back_when_current_output_disappears(service):
    service.configured_output_id = ""
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": "", "other-local": ""})
    )
    await service.update([zone("stopped", output="other-local", zone_id="other")])
    assert service.output_id == "other-local"
    service.client.request.assert_awaited_once_with(
        "select_output", output_id="other-local"
    )


async def test_automatic_selection_keeps_startup_resync_for_active_output(service):
    service.configured_output_id = service.output_id = ""
    service._resync = True
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": "", "other-local": ""})
    )
    await service.update([zone("stopped"), zone(output="other-local", zone_id="other")])
    assert service.output_id == "other-local"
    service.playback.acquire.assert_not_called()
    assert service.client.request.await_args_list == [
        call("select_output", output_id="other-local"),
        call("control", control="stop", timeout=2.0),
    ]


async def test_explicit_override_is_not_changed_by_other_local_playback(service):
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": "", "other-local": ""})
    )
    await service.update([zone("stopped"), zone(output="other-local", zone_id="other")])
    assert service.output_id == "local"
    service.client.request.assert_not_called()
    service.playback.acquire.assert_not_called()


async def test_confirmed_busy_handover_retries_once_without_releasing_hold(
    service, tmp_path, monkeypatch
):
    params = tmp_path / "hw_params"
    params.write_text("closed\n")
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": str(params)}),
        checkpoint=Mock(return_value=(1, 0)),
        busy_since=Mock(return_value=True),
    )
    await service.update([zone()])
    session = service.session
    monkeypatch.setattr(
        "kalinka_plugin_roon.service.running_on_bridge",
        lambda *args: params.read_text() != "closed\n",
    )

    async def request(method, **kwargs):
        if method == "control":
            assert kwargs["control"] == "play"
            params.write_text("rate: 44100 (44100/1)\nchannels: 2\n")
            return {}
        assert method == "snapshot"
        return {"zones": [zone()], "revision": 12}

    service.client.request.side_effect = request
    await service.update([zone("paused")])
    assert service.session is session
    assert service._ignore_revision == 12
    assert service._retried
    session.release.assert_not_called()
    assert service.playback.acquire.await_count == 1
    # A second failure is not an endless restart loop.
    service.client.request.side_effect = None
    params.write_text("closed\n")
    await service.update([zone("paused")])
    service.client.request.assert_awaited_with("control", control="stop", timeout=2.0)
    session.release.assert_awaited_once()


async def test_pause_without_busy_evidence_is_never_retried(service):
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": ""}),
        busy_since=Mock(return_value=False),
    )
    await service.update([zone()])
    await service.update([zone("paused")])
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )


async def test_ui_buffers_until_bridge_really_owns_running_hardware(
    service, monkeypatch, tmp_path
):
    params = tmp_path / "hw_params"
    params.write_text("rate: 192000\nchannels: 2\n")
    service.hw_params = str(params)
    running = Mock(return_value=False)
    monkeypatch.setattr("kalinka_plugin_roon.service.running_on_bridge", running)
    await service.update([zone()])
    state = service.session.report.call_args.args[0]
    assert state.state.value == "BUFFERING"
    assert state.audio_info.output is None  # old local stream must not leak
    params.write_text("rate: 44100\nchannels: 2\n")
    running.return_value = True
    await service.update([zone()])
    state = service.session.report.call_args.args[0]
    assert state.state.value == "PLAYING"
    assert state.audio_info.output.sample_rate == 44100
    assert service._retry_deadline == 0


async def test_revocation_while_waiting_to_retry_never_restarts(service, monkeypatch):
    service.local_outputs = Mock(
        discover=Mock(return_value={"local": ""}),
        busy_since=Mock(return_value=True),
    )
    await service.update([zone()])
    entered, resume = asyncio.Event(), asyncio.Event()

    async def wait(*args, **kwargs):
        entered.set()
        await resume.wait()
        return True

    monkeypatch.setattr("kalinka_plugin_roon.service.wait_released", wait)
    retry = asyncio.create_task(service.update([zone("paused")]))
    await entered.wait()
    await service.on_revoked(RevokeReason.QUEUE_PLAY)
    resume.set()
    await retry
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )
    assert service.suppressed


async def test_recovery_ignores_queued_pause_older_than_fresh_snapshot(service):
    await service.update([zone()])
    service._ignore_revision = 12
    service.client.events.put_nowait(
        {"event": "zones", "core_id": "core", "revision": 11, "zones": [zone("paused")]}
    )
    service.client.events.put_nowait(
        {
            "event": "zones",
            "core_id": "core",
            "revision": 13,
            "zones": [zone(seek_position=20)],
        }
    )
    service.client.events.put_nowait({"event": "closed"})
    with pytest.raises(RuntimeError, match="exited"):
        await service.run()
    service.client.request.assert_not_called()
    assert service.session.report.call_args.args[0].position == 20000


async def test_concurrent_stops_share_cleanup_even_if_waiter_is_cancelled(service):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def stop(*args, **kwargs):
        entered.set()
        await finish.wait()

    service.client.request.side_effect = stop
    first = asyncio.create_task(service.stop_audio())
    await entered.wait()
    second = asyncio.create_task(service.stop_audio())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()
    finish.set()
    await second
    service.client.request.assert_awaited_once_with(
        "control", control="stop", timeout=2.0
    )
