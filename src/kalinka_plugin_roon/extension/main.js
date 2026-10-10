"use strict";

// stdout is exclusively the private JSON-lines protocol with Python.
console.log = (...args) => console.error(...args);
const fs = require("node:fs");
const readline = require("node:readline");
const RoonApi = require("node-roon-api");
const Transport = require("node-roon-api-transport");
const Image = require("node-roon-api-image");
const Status = require("node-roon-api-status");
const Browse = require("node-roon-api-browse");
const {updateZones, selectedZone} = require("./zones");

process.umask(0o077);
const send = message => process.stdout.write(JSON.stringify(message) + "\n");
let outputId = process.env.ROON_OUTPUT_ID || "";
const zones = new Map();
let revision = 0;
let core;
const roon = new RoonApi({
    extension_id: "org.kalinka-player.roon-bridge",
    display_name: "Kalinka Roon Bridge",
    display_version: process.env.ROON_DISPLAY_VERSION || "unknown",
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
            send({event: "zones", core_id: core.core_id, revision: ++revision, zones: [...zones.values()]});
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
roon.init_services({required_services: [Transport, Image],
    optional_services: process.env.ROON_ENABLE_BROWSE === "1" ? [Browse] : [],
    provided_services: [status]});
status.set_status("Monitoring this Bridge's local output", false);

function command(message, respond = send) {
    const reply = (error, body = {}) => respond({id: message.id, error: error || null, ...body});
    if (message.method === "select_output") {
        outputId = message.output_id || "";
        return reply(null);
    }
    if (!core) return reply("Roon Server is disconnected");
    if (message.method === "snapshot") return reply(null, {
        core_id: core.core_id, name: core.display_name, output_id: outputId,
        browse_available: !!core.services.RoonApiBrowse, revision, zones: [...zones.values()],
    });
    if (["browse", "browse_load"].includes(message.method)) {
        const browse = core.services.RoonApiBrowse;
        if (!browse) return reply("Roon browsing API is unavailable");
        const method = message.method === "browse" ? "browse" : "load";
        return browse[method]({...message.options, hierarchy: "browse",
            multi_session_key: "kalinka-diagnostics", zone_or_output_id: outputId}, reply);
    }
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
// Local administrators can inspect/control the existing paired connection.
// The socket lives inside the private pairing directory and is never network exposed.
if (process.env.ROON_CONTROL_SOCKET) {
    const net = require("node:net");
    const socketPath = process.env.ROON_CONTROL_SOCKET;
    try { fs.unlinkSync(socketPath); } catch (error) { if (error.code !== "ENOENT") throw error; }
    net.createServer(socket => {
        socket.setTimeout(10000, () => socket.destroy());
        let buffer = "";
        socket.on("error", () => {});
        socket.on("data", bytes => {
            buffer += bytes.toString();
            if (buffer.length > 65536) return socket.destroy();
            const end = buffer.indexOf("\n");
            if (end < 0) return;
            socket.pause();
            try {
                command(JSON.parse(buffer.slice(0, end)), message => socket.end(JSON.stringify(message) + "\n"));
            } catch (error) { socket.end(JSON.stringify({error: error.message}) + "\n"); }
        });
    }).listen(socketPath);
}
setInterval(() => send({event: "heartbeat"}), 5000);
roon.start_discovery();
send({event: "ready"});
