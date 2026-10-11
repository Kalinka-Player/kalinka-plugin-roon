import asyncio
import json
import os
import shutil
import sys
from unittest.mock import Mock

import pytest

from kalinka_plugin_roon.client import RoonClient
from kalinka_plugin_roon.process import Bridge, ServerAudio, run_checked, terminate


async def test_client_correlates_replies_and_times_out_without_blocking_events(
    tmp_path,
):
    (tmp_path / "main.js").write_text("""
const readline = require('node:readline');
const send = o => process.stdout.write(JSON.stringify(o) + '\\n');
readline.createInterface({input:process.stdin}).on('line', line => {
 const message = JSON.parse(line);
 send({event:'heartbeat'});
 if (message.method === 'fail') send({id:message.id, error:'No output'});
 else if (message.method !== 'hang') send({id:message.id, error:null, result:message.control});
});
""")
    client = RoonClient(tmp_path, tmp_path / "state", "output")
    await client.start()
    try:
        assert (await client.request("control", control="stop"))["result"] == "stop"
        assert (await client.events.get())["event"] == "heartbeat"
        with pytest.raises(RuntimeError, match="No output"):
            await client.request("fail")
        with pytest.raises(asyncio.TimeoutError):
            await client.request("hang", timeout=0.02)
        assert not client.pending
    finally:
        await client.close()
    assert client.process.returncode is not None


async def test_termination_kills_child_that_ignores_sigterm(tmp_path):
    script = tmp_path / "tree.py"
    script.write_text("""
import os, signal, time
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True: time.sleep(1)
else:
    print(child, flush=True)
    while True: time.sleep(1)
""")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
    )
    child = int(await process.stdout.readline())
    await terminate(process, grace=0.05)
    assert process.returncode is not None
    # A killed orphan can briefly remain a zombie until the container's init reaps it.
    for _ in range(30):
        try:
            with open(f"/proc/{child}/stat") as status:
                if status.read().split()[2] == "Z":
                    return
        except FileNotFoundError:
            return
        await asyncio.sleep(0.01)
    pytest.fail("Bridge child process survived group termination")


async def test_check_failure_preserves_diagnostic(tmp_path):
    with pytest.raises(RuntimeError, match="missing ALSA"):
        await run_checked(
            [sys.executable, "-c", "import sys; print('missing ALSA'); sys.exit(1)"],
            cwd=tmp_path,
        )


async def test_concurrent_stops_wait_for_the_same_teardown(monkeypatch, tmp_path):
    entered, finished = asyncio.Event(), asyncio.Event()

    async def slow_terminate(process):
        if process is not None:
            entered.set()
            await finished.wait()

    monkeypatch.setattr("kalinka_plugin_roon.process.terminate", slow_terminate)
    bridge = Bridge(tmp_path, tmp_path)
    bridge.process = Mock()
    first = asyncio.create_task(bridge.stop())
    await entered.wait()
    second = asyncio.create_task(bridge.stop())
    await asyncio.sleep(0)
    assert not second.done()
    finished.set()
    await asyncio.gather(first, second)


async def test_start_reaps_owned_orphan_but_leaves_other_installations_and_data(
    tmp_path,
):
    runtime = tmp_path / "runtime"
    (runtime / "Bridge").mkdir(parents=True)
    executable = runtime / "Bridge/RAATServer"
    shutil.copy2(sys.executable, executable)
    launcher = runtime / "start.sh"
    launcher.write_text("#!/bin/sh\nexec sleep 60\n")
    launcher.chmod(0o755)
    data = tmp_path / "data"
    bridge = Bridge(runtime, data)
    children = []
    try:
        for binary, root in [
            (executable, data),
            (executable, tmp_path / "other-data"),
            (sys.executable, data),
        ]:
            child = await asyncio.create_subprocess_exec(
                str(binary),
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)",
                env={**os.environ, "ROON_DATAROOT": str(root)},
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
            )
            children.append(child)
            await child.stdout.readline()
        await bridge.start()
        await asyncio.wait_for(children[0].wait(), 2)
        assert children[0].returncode is not None
        assert all(child.returncode is None for child in children[1:])
        assert bridge.running
    finally:
        await bridge.stop()
        for child in children:
            await terminate(child, grace=0.01)


async def test_bridge_private_instance_preserves_existing_output_state(tmp_path):
    launcher = tmp_path / "start.sh"
    launcher.write_text(
        f"#!{sys.executable}\n"
        + """
import json,os,socket,sys,time
from pathlib import Path
args=dict(arg[1:].split('=',1) for arg in sys.argv[1:])
root=Path(os.environ['ROON_DATAROOT'])
state=(root/('RAATServer-'+args['app'])/'output.json').read_text()
port=socket.socket()
port.bind(('127.0.0.1',int(args['refcountport'])))
port.listen()
(root/'started.json').write_text(json.dumps({'args':args,'state':state}))
while True: time.sleep(1)
"""
    )
    launcher.chmod(0o755)
    data = tmp_path / "data"
    (data / "RAATServer").mkdir(parents=True)
    saved = '{"output_id":"existing-output"}'
    (data / "RAATServer/output.json").write_text(saved)
    bridge = Bridge(tmp_path, data)
    identities = []
    try:
        for _ in range(2):
            await bridge.start()
            async with asyncio.timeout(2):
                while not (data / "started.json").exists():
                    await asyncio.sleep(0.01)
            result = json.loads((data / "started.json").read_text())
            assert result["state"] == saved
            assert int(result["args"]["refcountport"]) != 9004
            identities.append(result["args"]["app"])
            await bridge.stop()
            (data / "started.json").unlink()
        assert identities[0] == identities[1]
        assert (data / "RAATServer/output.json").read_text() == saved
    finally:
        await bridge.stop()


async def test_local_core_audio_is_discovered_without_taking_process_ownership(
    tmp_path, monkeypatch
):
    proc = tmp_path / "proc"
    entry = proc / "500"
    (entry / "fd").mkdir(parents=True)
    (proc / "400").mkdir()
    for name, path in (("RAATServer.exe", entry), ("RoonAppliance.exe", proc / "400")):
        binary = tmp_path / name
        binary.touch()
        (path / "exe").symlink_to(binary)
    values = ["S", "400", "300"] + ["0"] * 16 + ["123"]
    (entry / "stat").write_text("500 (RAATServer) " + " ".join(values))
    raat = tmp_path / "custom-state/.RAATServer"
    (raat / "Logs").mkdir(parents=True)
    log = raat / "Logs/RAATServer_log.txt"
    log.touch()
    (entry / "fd/0").symlink_to("pipe:[123]")
    (entry / "fd/1").symlink_to(log)
    kill = Mock()
    monkeypatch.setattr("kalinka_plugin_roon.process.os.killpg", kill)
    audio = ServerAudio.find(proc)
    assert audio is not None and audio.raat_directory == raat
    assert audio.running and not audio.managed
    assert audio.process.pid == 300
    await audio.start()
    await audio.stop()
    kill.assert_not_called()
    assert audio.running
    values[19] = "456"  # A reused PID must not be mistaken for the same process.
    (entry / "stat").write_text("500 (RAATServer) " + " ".join(values))
    assert not audio.running
    with pytest.raises(RuntimeError, match="exited"):
        await audio.start()
    (proc / "400/exe").unlink()
    assert ServerAudio.find(proc) is None
