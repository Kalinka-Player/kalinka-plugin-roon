import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kalinka_plugin_roon import RoonConfig
from kalinka_plugin_roon.supervisor import Supervisor


@pytest.fixture(autouse=True)
def no_local_core(monkeypatch):
    monkeypatch.setattr("kalinka_plugin_roon.supervisor.ServerAudio.find", lambda: None)


async def test_installation_errors_are_visible_and_shutdown_cancels_retry(
    monkeypatch, tmp_path
):
    install = AsyncMock(side_effect=RuntimeError("Unsupported platform"))
    monkeypatch.setattr("kalinka_plugin_roon.supervisor.install_bridge", install)
    supervisor = Supervisor(Mock(), RoonConfig(enabled=True), tmp_path)
    supervisor.start()
    for _ in range(10):
        await asyncio.sleep(0)
        if supervisor.error:
            break
    assert supervisor.error == "Unsupported platform"
    await supervisor.stop()
    assert supervisor._task is None


async def test_two_instances_cannot_share_bridge_state(monkeypatch, tmp_path):
    entered = asyncio.Event()

    async def install(directory):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("kalinka_plugin_roon.supervisor.install_bridge", install)
    one = Supervisor(Mock(), RoonConfig(enabled=True), tmp_path)
    two = Supervisor(Mock(), RoonConfig(enabled=True), tmp_path)
    one.start()
    await entered.wait()
    two.start()
    await asyncio.wait_for(two._task, 1)
    assert "Another" in two.error
    await one.stop()


async def test_directory_failure_is_exposed_in_status(tmp_path):
    directory = tmp_path / "file"
    directory.write_text("not a directory")
    supervisor = Supervisor(Mock(), RoonConfig(enabled=True), directory)
    supervisor.start()
    await asyncio.wait_for(supervisor._task, 1)
    assert supervisor.error


async def test_local_core_is_attached_without_installing_or_launching_bridge(
    monkeypatch, tmp_path
):
    audio = Mock(raat_directory=tmp_path / "core/RAATServer")
    monkeypatch.setattr(
        "kalinka_plugin_roon.supervisor.ServerAudio.find", lambda: audio
    )
    install = AsyncMock()
    monkeypatch.setattr("kalinka_plugin_roon.supervisor.install_bridge", install)
    monkeypatch.setattr(
        "kalinka_plugin_roon.supervisor.install_extension",
        AsyncMock(return_value=tmp_path),
    )
    cleanup = AsyncMock()
    monkeypatch.setattr("kalinka_plugin_roon.supervisor.Bridge._stop_orphans", cleanup)
    entered = asyncio.Event()

    async def run():
        entered.set()
        await asyncio.Event().wait()

    service = Mock(run=run, close=AsyncMock())
    factory = Mock(return_value=service)
    monkeypatch.setattr("kalinka_plugin_roon.supervisor.Service", factory)
    supervisor = Supervisor(Mock(), RoonConfig(enabled=True), tmp_path)
    supervisor.start()
    await asyncio.wait_for(entered.wait(), 1)
    install.assert_not_called()
    cleanup.assert_awaited_once()
    assert factory.call_args.args[1] is audio
    assert factory.call_args.args[5].settings == audio.raat_directory / "Settings"
    await supervisor.stop()
    service.close.assert_awaited_once()
