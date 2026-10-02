import asyncio
import sys
from unittest.mock import Mock

import pytest

from kalinka_plugin_roon.client import RoonClient
from kalinka_plugin_roon.process import Bridge, run_checked, terminate


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
