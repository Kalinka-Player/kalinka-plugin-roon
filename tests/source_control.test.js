"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {SourceSwitch} = require("../src/kalinka_plugin_roon/extension/source_control");

test("unanswered duplicate requests time out together and late success is refused", async () => {
    const events = [], replies = [];
    const core = {core_id: "core"};
    const service = {new_device() { return {update_state() {}}; }};
    const source = new SourceSwitch(service, "output", () => core, () => true,
        event => events.push(event), 10);
    let done;
    const completed = new Promise(resolve => { done = resolve; });
    source.switch({send_complete: result => replies.push(result)});
    source.switch({send_complete: result => { replies.push(result); done(); }});
    assert.equal(events.length, 1);
    await completed;
    assert.deepEqual(replies, ["Failed", "Failed"]);
    assert.equal(source.complete(events[0].request_id, true), false);
});

test("unavailable output refuses a switch without asking Kalinka to stop", () => {
    const events = [], replies = [];
    const service = {new_device() { return {update_state() {}}; }};
    const source = new SourceSwitch(service, "output", () => ({core_id: "core"}),
        () => false, event => events.push(event));
    source.switch({send_complete: result => replies.push(result)});
    assert.deepEqual(replies, ["Failed"]);
    assert.equal(events.length, 0);
});
