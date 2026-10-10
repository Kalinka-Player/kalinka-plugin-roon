import asyncio
import json
import os

from kalinka_plugin_roon.local import LocalOutputs, running_on_bridge, wait_released


def test_bridge_guid_matches_transport_and_follows_alsa_card_number(tmp_path):
    settings = tmp_path / "RAATServer/Settings"
    settings.mkdir(parents=True)
    (settings / "device_dac.json").write_text(
        json.dumps(
            {
                "unique_id": "fb62bd8e-3b32-73c8-5cf2-0ff1b682700f",
                "output": {"type": "alsa", "device": "hw:CARD=TestDAC,DEV=2"},
            }
        )
    )
    asound = tmp_path / "asound"
    (asound / "card3").mkdir(parents=True)
    (asound / "card3/id").write_text("TestDAC\n")
    assert LocalOutputs(tmp_path, asound).discover() == {
        "17018ebd62fb323bc8735cf20ff1b682700f": str(
            asound / "card3/pcm2p/sub0/hw_params"
        )
    }
    (settings / "device_broken.json").write_text("{")
    (settings / "device_dac.json").write_text('{"unique_id":"bad"}')
    assert LocalOutputs(tmp_path, asound).discover() == {}


async def test_release_waits_for_hardware_close_and_is_bounded(tmp_path):
    params = tmp_path / "hw_params"
    params.write_text("rate: 44100\n")
    waiting = asyncio.create_task(wait_released(params))
    await asyncio.sleep(0.04)
    assert not waiting.done()
    params.write_text("closed\n")
    assert await waiting
    params.write_text("rate: 44100\n")
    assert not await wait_released(params, timeout=0.02)
    assert not await wait_released(tmp_path / "absent")


async def test_release_does_not_wait_for_another_process_using_the_dac(tmp_path):
    params = tmp_path / "hw_params"
    params.write_text("rate: 44100\n")
    (tmp_path / "status").write_text(f"state: RUNNING\nowner_pid : {os.getpid()}\n")
    assert running_on_bridge(params, os.getpgrp())
    assert not running_on_bridge(params, os.getpgrp() + 1)
    assert await wait_released(params, process_group=os.getpgrp() + 1)
    assert not await wait_released(params, timeout=0.01, process_group=os.getpgrp())


def test_collision_evidence_is_fresh_and_belongs_to_selected_device(tmp_path):
    local = LocalOutputs(tmp_path)
    local.names = {"local": "DAC"}
    local.log_path.parent.mkdir(parents=True)
    busy = "Error: [RAAT::DAC] error in snd_pcm_open: Device or resource busy (-16)\n"
    local.log_path.write_text(busy)
    marker = local.checkpoint()
    assert not local.busy_since(marker, "local")
    with local.log_path.open("a") as log:
        log.write(busy.replace("DAC", "Other DAC"))
    assert not local.busy_since(marker, "local")
    with local.log_path.open("a") as log:
        log.write(busy)
    assert local.busy_since(marker, "local")
    assert not local.busy_since(marker, "remote")
    local.log_path.rename(local.log_path.with_suffix(".old"))
    local.log_path.write_text(busy)
    assert local.busy_since(marker, "local")
