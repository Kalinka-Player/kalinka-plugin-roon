"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {updateZones, selectedZone} = require("../src/kalinka_plugin_roon/extension/zones");

test("zone subscription follows a saved output through seek changes and regrouping", () => {
    const zones = new Map();
    updateZones(zones, "Subscribed", {zones: [{zone_id: "old", outputs: [{output_id: "local"}],
                                            now_playing: {seek_position: 0}}]});
    assert.equal(selectedZone(zones, ""), undefined);
    assert.equal(selectedZone(zones, "other"), undefined);
    assert.equal(selectedZone(zones, "local").zone_id, "old");
    updateZones(zones, "Changed", {zones_seek_changed: [{zone_id: "old", seek_position: 25}]});
    assert.equal(selectedZone(zones, "local").now_playing.seek_position, 25);
    updateZones(zones, "Changed", {zones_removed: ["old"], zones_added: [
        {zone_id: "group", outputs: [{output_id: "local"}, {output_id: "remote"}]}
    ]});
    assert.equal(selectedZone(zones, "local").zone_id, "group");
    updateZones(zones, "Unsubscribed");
    assert.equal(zones.size, 0);
});

test("late seek updates for deleted zones are ignored", () => {
    const zones = new Map();
    updateZones(zones, "Changed", {zones_seek_changed: [{zone_id: "gone", seek_position: 2}]});
    assert.equal(zones.size, 0);
});
