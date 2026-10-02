"use strict";

// Keep the documented subscription model, including seek-only updates.
function updateZones(zones, response, message = {}) {
    if (response === "Subscribed") zones.clear();
    if (response === "Unsubscribed") { zones.clear(); return; }
    for (const id of message.zones_removed || []) zones.delete(id);
    for (const zone of [...(message.zones || []), ...(message.zones_added || []),
                        ...(message.zones_changed || [])]) {
        zones.set(zone.zone_id, structuredClone(zone));
    }
    for (const seek of message.zones_seek_changed || []) {
        const zone = zones.get(seek.zone_id);
        if (zone?.now_playing) zone.now_playing.seek_position = seek.seek_position;
    }
}

function selectedZone(zones, outputId) {
    if (!outputId) return undefined;
    return [...zones.values()].find(zone =>
        zone.outputs?.some(output => output.output_id === outputId));
}

module.exports = {updateZones, selectedZone};
