# Changelog

Curated, user-facing notes per release. Add a `## <version>` section before tagging — the release workflow puts the matching section into the GitHub Release body.

## 0.2.0

- Route Kalinka's volume slider to the selected Roon output on servers with external-volume support, including while paused; reflect Roon-side changes, honor limits and restore renderer controls when switching back to local playback.
- Attach to an existing local Roon Server audio endpoint instead of launching a duplicate Bridge. Discover its local outputs and output measurements automatically, without taking ownership of the Server process.
- Preserve Roon's exclusive session on pause and resume it through Kalinka; release only on Stop, source takeover or loss of the endpoint.
- Report Roon's transport state without turning an unrecognized ALSA owner into endless buffering. Reap orphaned processes from the managed Bridge installation on startup, and isolate its RAAT process from a Roon Server running on the same host.
- Accept new Roon playback after stopping an already-paused session, even if Roon sends no additional paused event.
- Allow confirmed handover recovery to wait for a shared desktop backend's device-release delay, and test local PipeWire playback as well as direct ALSA.
- Detect the managed Bridge's local ALSA outputs and output rate automatically; follow active local playback and use a stable first output by default, even with multiple outputs enabled. Manual selection is an advanced override.
- Wait for actual ALSA release after Roon acknowledges Stop. Recover one confirmed device-busy collision when switching back from local playback, without retrying ordinary pauses.
- Avoid showing the previous source's output format during handover.
- Add a private diagnostic socket and a hardware/browser E2E test for repeated Roon → local file → Roon switching, metadata, artwork, progress and ALSA ownership.

## 0.1.1

First release.

### Added
- **Use the Kalinka machine as a Roon endpoint.** Enabling the plugin downloads the official Roon Bridge for this machine and runs it under Kalinka. Roon plays straight to the local DAC.
- Kalinka shows what Roon is playing: track, artist, album, artwork and progress, with a **Roon endpoint** badge. Kalinka's own queue stays as it was.
- Starting Roon stops the server's current Kalinka source, and starting another source stops Roon, so only one plays at a time.
- Needs Kalinka server 5.6 or newer, which brings plugin SDK 3.7 (the external playback API). The package will not install on an older server.
