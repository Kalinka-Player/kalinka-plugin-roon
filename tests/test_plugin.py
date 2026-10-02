from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kalinka_plugin_roon import KalinkaPluginRoon, RoonConfig


async def test_disabled_plugin_does_not_install_or_start():
    plugin = KalinkaPluginRoon()
    await plugin.setup(SimpleNamespace(config=RoonConfig()))
    assert plugin.supervisor is None
    assert (await plugin.get_state()).state.value == "disabled"


async def test_old_server_is_rejected_before_install():
    plugin = KalinkaPluginRoon()
    with pytest.raises(RuntimeError, match="external playback"):
        await plugin.setup(SimpleNamespace(config=RoonConfig(enabled=True)))
    assert plugin.supervisor is None


async def test_discovered_output_options_are_stable_ids():
    plugin = KalinkaPluginRoon()
    plugin.supervisor = Mock(service=Mock(outputs={"stable-id": "Renamed output"}))
    options = await plugin.resolve_options("output_id")
    assert options[0].value == "stable-id"
    assert "Renamed output" in options[0].label
    with pytest.raises(KeyError):
        await plugin.resolve_options("unknown")
