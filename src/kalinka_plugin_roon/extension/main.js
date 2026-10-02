"use strict";

// stdout is exclusively the private JSON-lines protocol with Python.
console.log = (...args) => console.error(...args);
const fs = require("node:fs");
const readline = require("node:readline");
const RoonApi = require("node-roon-api");
const Transport = require("node-roon-api-transport");
const Image = require("node-roon-api-image");
const Status = require("node-roon-api-status");
const {updateZones, selectedZone} = require("./zones");

process.umask(0o077);
const send = message => process.stdout.write(JSON.stringify(message) + "\n");
const outputId = process.env.ROON_OUTPUT_ID || "";
const zones = new Map();
let core;
const roon = new RoonApi({
    extension_id: "org.kalinka-player.roon-bridge",
    display_name: "Kalinka Roon Bridge",
    display_version: "0.1.0",
    publisher: "Kalinka-Player",
    email: "envelsavinds@gmail.com",
    website: "https://github.com/Kalinka-Player/kalinka-plugin-roon",
    log_level: "none",
    get_persisted_state: () => {
        try { return JSON.parse(fs.readFileSync("pairing.json", "utf8")); }
        catch (error) { if (error.code === "ENOENT") return {}; throw error; }
    },
    set_persisted_state: state => {
        fs.writeFileSync("pairing.json.tmp", JSON.stringify(state), {mode: 0o600});
        fs.renameSync("pairing.json.tmp", "pairing.json");
    },
    core_paired: paired => {
        core = paired;
        zones.clear();
        send({event: "paired", core_id: core.core_id, name: core.display_name});
        core.services.RoonApiTransport.subscribe_zones((response, message) => {
            if (core !== paired) return;
            updateZones(zones, response, message);
            send({event: "zones", core_id: core.core_id, zones: [...zones.values()]});
        });
    },
    core_unpaired: lost => {
        if (core !== lost) return;
        core = undefined;
        zones.clear();
        send({event: "disconnected"});
    },
});
const status = new Status(roon);
roon.init_services({required_services: [Transport, Image], provided_services: [status]});
status.set_status("Select this device's output in Kalinka settings", false);

function command(message) {
    const reply = (error, body = {}) => send({id: message.id, error: error || null, ...body});
    if (!core) return reply("Roon Server is disconnected");
    const transport = core.services.RoonApiTransport;
    if (message.method === "image") {
        return core.services.RoonApiImage.get_image(message.key,
            {scale: "fit", width: 600, height: 600, format: "image/jpeg"},
            (error, mime, bytes) => {
                if (error) return reply(error);
                if (bytes.length > 2 * 1024 * 1024) return reply("Artwork exceeds limit");
                reply(null, {mime, data: bytes.toString("base64")});
            });
    }
    const zone = selectedZone(zones, outputId);
    if (!zone) return reply("Selected Roon output is unavailable");
    // Address the saved output, so regrouping cannot target the old zone.
    const target = {output_id: outputId};
    if (message.method === "seek") {
        if (!zone.is_seek_allowed || !Number.isFinite(message.seconds) || message.seconds < 0)
            return reply("Seek is unavailable");
        return transport.seek(target, "absolute", message.seconds, reply);
    }
    if (message.method !== "control") return reply("Unknown method");
    const control = message.control;
    if (!["stop", "pause", "play", "next", "previous"].includes(control))
        return reply("Unknown control");
    if (control !== "stop" && !zone[`is_${control}_allowed`])
        return reply("Control is unavailable");
    transport.control(target, control, reply);
}

readline.createInterface({input: process.stdin}).on("line", line => {
    try { command(JSON.parse(line)); }
    catch (error) { console.error(error.message); }
}).on("close", () => process.exit(0));
setInterval(() => send({event: "heartbeat"}), 5000);
roon.start_discovery();
send({event: "ready"});
