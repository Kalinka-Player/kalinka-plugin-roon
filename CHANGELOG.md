# Changelog

Curated, user-facing notes per release. Add a `## <version>` section before tagging — the release workflow puts the matching section into the GitHub Release body.

## 0.1.1

First release.

### Added
- **Use the Kalinka machine as a Roon endpoint.** Enabling the plugin downloads the official Roon Bridge for this machine and runs it under Kalinka. Roon plays straight to the local DAC.
- Kalinka shows what Roon is playing: track, artist, album, artwork and progress, with a **Roon endpoint** badge. Kalinka's own queue stays as it was.
- Starting Roon stops the server's current Kalinka source, and starting another source stops Roon, so only one plays at a time.
- Needs Kalinka server 5.6 or newer, which brings plugin SDK 3.7 (the external playback API). The package will not install on an older server.
