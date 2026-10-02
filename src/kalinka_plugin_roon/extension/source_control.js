"use strict";

// Roon waits for convenience_switch to complete before starting its output.
// Complete it only after Python has acquired Kalinka's external playback hold.
class SourceSwitch {
    constructor(service, outputId, currentCore, available, send, timeoutMs = 15000) {
        this.currentCore = currentCore;
        this.available = available;
        this.send = send;
        this.timeoutMs = timeoutMs;
        this.sequence = 0;
        this.pending = undefined;
        this.device = outputId ? service.new_device({
            state: {control_key: outputId, display_name: "Kalinka Roon Bridge",
                supports_standby: false, status: "deselected"},
            convenience_switch: req => this.switch(req),
        }) : undefined;
    }

    switch(req) {
        const core = this.currentCore();
        if (!this.device || !core || !this.available()) return req.send_complete("Failed");
        if (this.pending) {
            this.pending.requests.push(req);
            return;
        }
        const id = ++this.sequence;
        this.pending = {id, core, requests: [req],
            timer: setTimeout(() => this.complete(id, false), this.timeoutMs)};
        this.send({event: "source_switch", request_id: id, core_id: core.core_id});
    }

    complete(id, success) {
        const pending = this.pending;
        if (!pending || pending.id !== id) return false;
        this.pending = undefined;
        clearTimeout(pending.timer);
        success = success && pending.core === this.currentCore() && !!this.available();
        this.select(success);
        for (const req of pending.requests) req.send_complete(success ? "Success" : "Failed");
        return true;
    }

    select(selected) {
        if (!selected && this.pending) this.complete(this.pending.id, false);
        this.device?.update_state({status: selected ? "selected" : "deselected"});
    }
}

module.exports = {SourceSwitch};
