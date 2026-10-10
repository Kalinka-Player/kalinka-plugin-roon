#!/usr/bin/env python3
"""Hardware acceptance: paired Roon → local file → Roon, real ALSA and web UI.

Requires: pip install playwright; playwright install chromium
Runs against an installed development plugin, SSH/sudo, and an enabled output
with a playable Roon queue. Leaves playback stopped, preserves the queue, and
writes timestamped traces and screenshots (including failure evidence).
"""

import argparse
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from roon_rpc import rpc


class Acceptance:
    def __init__(self, args):
        self.args = args
        self.base = args.url.rstrip("/")
        self.artifacts = Path(args.artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.trace = (self.artifacts / "trace.jsonl").open("w")
        self.started = int(time.time())

    def record(self, kind, **data):
        self.trace.write(json.dumps({"time": time.time(), "kind": kind, **data}) + "\n")
        self.trace.flush()

    def api(self, path, method="GET", body=None):
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
        return json.loads(data) if data else None

    def roon(self, method, **arguments):
        result = rpc(self.args.host, method, **arguments)
        self.record("roon", method=method, arguments=arguments, result=result)
        return result

    def hardware(self):
        command = """sudo python3 - <<'PY'
import glob,json,pathlib,re
result=[]
for name in glob.glob('/proc/asound/card[0-9]*/pcm*p/sub*/status'):
 p=pathlib.Path(name); status=p.read_text(); match=re.search(r'owner_pid\\s*:\\s*(\\d+)',status)
 owner=''; executable=''
 if match:
  try:
   owner=pathlib.Path('/proc/'+match[1]+'/comm').read_text().strip()
   executable=str(pathlib.Path('/proc/'+match[1]+'/exe').resolve())
  except OSError: pass
 if status.strip()!='closed': result.append({'path':name,'status':status,'owner':owner,'executable':executable,'params':p.with_name('hw_params').read_text()})
print(json.dumps(result))
PY"""
        result = subprocess.run(
            ["ssh", self.args.host, command],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return json.loads(result.stdout)

    def collect_logs(self):
        for name, command in {
            "kalinka-journal.txt": f"sudo journalctl -u kalinka -u kalinka-renderer --since @{self.started} --no-pager -o short-iso",
            "raat-tail.txt": "sudo tail -c 131072 /var/lib/kalinka/roon/data/RAATServer/Logs/RAATServer_log.txt",
        }.items():
            result = subprocess.run(
                ["ssh", self.args.host, command],
                capture_output=True,
                text=True,
                timeout=20,
            )
            (self.artifacts / name).write_text(result.stdout + result.stderr)

    def wait_playing(self, source, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.api("/queue/state")
            self.record("state", expected=source, state=state)
            track = state.get("current_track") or {}
            if state["state"] == "PLAYING" and f":{source}:" in track.get("id", ""):
                return state
            self.page.wait_for_timeout(200)
        raise AssertionError(f"{source} did not become PLAYING: {state}")

    def verify(self, source, label):
        state = self.wait_playing(source)
        initial = state["position"]
        deadline = time.monotonic() + 6
        self.page.wait_for_timeout(2500)
        while True:
            state = self.wait_playing(source, timeout=5)
            if state["position"] > initial:
                break
            assert time.monotonic() < deadline, ("progress stalled", initial, state)
            self.page.wait_for_timeout(200)
        hardware = self.hardware()
        self.record("hardware", source=source, devices=hardware)
        owner = "RAATServer" if source == "roon" else "kalinka-renderer"
        assert any(
            d["executable"].endswith(owner) and "RUNNING" in d["status"]
            for d in hardware
        ), hardware
        title = state["current_track"]["title"]
        self.page.get_by_text(title, exact=True).first.wait_for(timeout=10000)
        if source == "roon":
            control = self.api("/queue/control")
            assert control["plugin_id"] == "roon", control
            snapshot = self.roon("snapshot")
            zone = next(
                z
                for z in snapshot["zones"]
                if any(o["output_id"] == snapshot["output_id"] for o in z["outputs"])
            )
            lines = zone["now_playing"]["three_line"]
            assert title == lines["line1"]
            assert state["current_track"]["performer"]["name"] == lines["line2"]
            assert state["current_track"]["album"]["title"] == lines["line3"]
            output = state["audio_info"]["output"]
            assert output and output["sample_rate"] > 0, state
            assert any(
                f"rate: {output['sample_rate']} " in d["params"] for d in hardware
            )
            assert state["audio_info"]["sample_rate"] == 0  # unknown source format
            image = state["current_track"]["album"]["image"]
            if zone["now_playing"].get("image_key"):
                with urllib.request.urlopen(
                    self.base + image["large"], timeout=10
                ) as response:
                    assert response.headers["Content-Type"].startswith("image/jpeg")
                    assert response.read(2) == b"\xff\xd8"
        self.page.screenshot(path=str(self.artifacts / f"{label}.png"))
        self.page.get_by_role("button", name=re.compile("^Stream info")).press("Enter")
        self.page.wait_for_timeout(300)
        semantics = self.page.locator("body").aria_snapshot()
        (self.artifacts / f"{label}-ui.txt").write_text(semantics)
        self.page.screenshot(path=str(self.artifacts / f"{label}-stream.png"))
        if source == "roon":
            assert "kalinka:roon:" in semantics and "kHz" in semantics, semantics
            assert "0 bit" not in semantics and "0 kHz" not in semantics, semantics
            assert "FORMAT" not in semantics and "INDEX" not in semantics, semantics
        self.page.keyboard.press("Escape")
        print(f"PASS {label}: {title}, position={state['position']}ms", flush=True)

    def verify_controls(self):
        self.api("/queue/current_track/seek?position_ms=30000", "PUT")
        deadline = time.monotonic() + 5
        while True:
            state = self.api("/queue/state")
            if 30000 <= state["position"] < 35000:
                break
            assert time.monotonic() < deadline, ("Roon seek failed", state)
            self.page.wait_for_timeout(100)
        self.api("/queue/pause", "PUT")
        deadline = time.monotonic() + 5
        while True:
            hardware = self.hardware()
            if self.api("/queue/control").get("plugin_id") != "roon" and not any(
                d["executable"].endswith("RAATServer") for d in hardware
            ):
                break
            assert time.monotonic() < deadline, (
                "Roon pause did not release DAC",
                hardware,
            )
            self.page.wait_for_timeout(100)
        self.page.wait_for_timeout(1200)
        assert self.api("/queue/control").get("plugin_id") != "roon", (
            "Pause was unexpectedly retried"
        )
        self.record("controls", seek=True, pause_releases_dac=True)
        self.roon("control", control="play")
        self.verify("roon", "resume-after-pause")

    def run(self):
        config = self.api("/server/config")["values"]
        assert not config["input_modules.roon.output_id"], (
            "Clear the output override to test automatic selection"
        )
        original_queue = self.api("/queue/list?limit=10000")
        assert len(original_queue["items"]) == original_queue["total"], (
            "The entire saved queue must fit in the snapshot for preservation checks"
        )
        index = original_queue["total"]
        added = False
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            self.page = browser.new_page(viewport={"width": 1440, "height": 1000})
            self.page.on(
                "websocket",
                lambda ws: ws.on(
                    "framereceived",
                    lambda frame: self.record(
                        "ui-websocket",
                        data=frame if isinstance(frame, str) else "binary",
                    ),
                ),
            )
            try:
                snapshot = self.roon("snapshot")
                zone = next(
                    z
                    for z in snapshot["zones"]
                    if any(
                        o["output_id"] == snapshot["output_id"] for o in z["outputs"]
                    )
                )
                if not zone["is_play_allowed"] and zone["state"] == "stopped":
                    # A Bridge restart leaves Roon's previous queue in history.
                    self.roon("control", control="previous")
                    self.page.wait_for_timeout(300)
                self.page.goto(self.base)
                self.page.locator("flt-semantics-placeholder").wait_for(
                    state="attached", timeout=60000
                )
                self.page.locator("flt-semantics-placeholder").evaluate(
                    "e => e.click()"
                )
                self.api("/queue/add", "POST", [self.args.local_track])
                added = True
                self.roon("control", control="play")
                self.verify("roon", "00-roon")
                for cycle in range(1, self.args.cycles + 1):
                    self.api(f"/queue/play?index={index}", "PUT")
                    self.verify("localfiles", f"{cycle:02}-local")
                    # No preceding Stop or artificial handover delay: a real takeover.
                    self.roon("control", control="play")
                    self.verify("roon", f"{cycle:02}-roon")
                self.verify_controls()
            except BaseException:
                self.page.screenshot(path=str(self.artifacts / "failure.png"))
                raise
            finally:
                try:
                    self.api("/queue/stop", "PUT")
                    if added:
                        self.api(f"/queue/remove?index={index}", "POST")
                        after = self.api("/queue/list?limit=10000")
                        assert [t["id"] for t in after["items"]] == [
                            t["id"] for t in original_queue["items"]
                        ], "Saved queue changed"
                        self.record("queue_preserved", tracks=after["total"])
                finally:
                    browser.close()
        # Cleanup and queue preservation are part of acceptance, too.
        self.record("result", passed=True, cycles=self.args.cycles)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="raspberrypi.local")
    parser.add_argument("--url", default="http://raspberrypi.local:8000")
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--local-track", required=True)
    parser.add_argument("--artifacts", default="artifacts/e2e")
    test = Acceptance(parser.parse_args())
    try:
        test.run()
    except BaseException as error:
        test.record("result", passed=False, error=str(error))
        raise
    finally:
        try:
            test.collect_logs()
        except Exception as error:
            test.record("diagnostic_error", error=str(error))
        finally:
            test.trace.close()
