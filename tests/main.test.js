"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function fixture(outputId = "local") {
    const messages = [], calls = [], handlers = {};
    let descriptor, subscription;
    const noop = () => {};
    class RoonApi {
        constructor(options) { descriptor = options; }
        init_services() {}
        start_discovery() {}
    }
    class Status { set_status() {} }
    const modules = {
        "node:fs": {readFileSync() { const err = new Error(); err.code = "ENOENT"; throw err; }},
        "node:readline": {createInterface() {
            return {on(name, handler) { handlers[name] = handler; return this; }};
        }},
        "node-roon-api": RoonApi,
        "node-roon-api-transport": class Transport {},
        "node-roon-api-image": class Image {},
        "node-roon-api-status": Status,
        "./zones": require("../src/kalinka_plugin_roon/extension/zones"),
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../src/kalinka_plugin_roon/extension/main.js"), "utf8"), {
        require: name => modules[name], console: {log: noop, error: noop}, setInterval: noop,
        process: {umask: noop, env: {ROON_OUTPUT_ID: outputId}, stdin: {}, exit: noop,
                  stdout: {write: line => messages.push(JSON.parse(line))}},
    });
    const core = {core_id: "core", display_name: "Server", services: {
        RoonApiTransport: {
            subscribe_zones(cb) { subscription = cb; },
            control(output, control, cb) { calls.push({output, control}); cb(false); },
            seek(output, how, seconds, cb) { calls.push({output, how, seconds}); cb(false); },
        },
        RoonApiImage: {get_image(key, options, cb) { cb(false, "image/jpeg", Buffer.from("jpeg")); }},
    }};
    descriptor.core_paired(core);
    subscription("Subscribed", {zones: [{zone_id: "group", state: "playing", is_seek_allowed: true,
        is_pause_allowed: false, outputs: [{output_id: "remote"}, {output_id: "local"}]}]});
    return {messages, calls, descriptor, core, subscription,
        command: message => handlers.line(JSON.stringify({id: 1, ...message}))};
}

test("stop addresses the selected output, including in grouped zones", () => {
    const f = fixture();
    f.command({method: "control", control: "stop"});
    assert.equal(f.calls[0].output.output_id, "local");
    assert.equal(f.calls[0].control, "stop");
    assert.equal(f.messages.at(-1).error, null);
});

test("controls refuse an unconfigured or absent output", () => {
    for (const id of ["", "unknown"]) {
        const f = fixture(id);
        f.command({method: "control", control: "stop"});
        assert.equal(f.calls.length, 0);
        assert.match(f.messages.at(-1).error, /unavailable/);
    }
});

test("unsupported controls are refused and seeks use seconds", () => {
    const f = fixture();
    f.command({method: "control", control: "pause"});
    assert.equal(f.calls.length, 0);
    f.command({method: "seek", seconds: 20.5});
    assert.equal(f.calls[0].seconds, 20.5);
    assert.equal(f.calls[0].how, "absolute");
});

test("artwork uses the authorized image API and disconnect prevents commands", () => {
    const f = fixture();
    f.command({method: "image", key: "key"});
    assert.equal(f.messages.at(-1).data, Buffer.from("jpeg").toString("base64"));
    f.descriptor.core_unpaired(f.core);
    assert.equal(f.messages.at(-1).event, "disconnected");
    f.command({method: "control", control: "stop"});
    assert.equal(f.calls.length, 0);
    assert.match(f.messages.at(-1).error, /disconnected/);
});
