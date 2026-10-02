import asyncio
from unittest.mock import AsyncMock, Mock

from kalinka_plugin_roon import RoonConfig
from kalinka_plugin_roon.supervisor import Supervisor


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
