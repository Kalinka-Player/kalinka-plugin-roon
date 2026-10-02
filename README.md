# Kalinka Roon Bridge

An experimental Roon endpoint plugin for [Kalinka](https://github.com/Kalinka-Player/KalinkaPlayer).
Enabling it downloads the official Roon Bridge for this Linux machine and runs
it as a child of Kalinka. Roon sends audio directly to the local DAC through
RAAT. Kalinka displays the track, artist, album, artwork, progress and a **Roon
endpoint** badge. Its queue stays intact while Roon plays.

This is an independent integration, not Roon Ready certification or a Roon
Labs product. Roon Bridge is proprietary and is downloaded from Roon Labs at
runtime; it is not included in this repository, wheels or native packages.

## Requirements

- Linux x86-64, ARM64, or ARMv7 hard-float, with Python 3.11+.
- Kalinka with the **external playback API**, SDK **3.7+ and <4**. Installing
  only a newer SDK on an older server is insufficient. See the companion
  [server change, PR #250](https://github.com/Kalinka-Player/KalinkaPlayer/pull/250);
  the plugin refuses an unsupported server before
  downloading or starting anything.
- Node.js 18+; npm is also needed for a source/pip install. The Debian build
  bundles the official Roon extension libraries at pinned commits, with a
  lockfile and integrity hashes. It does not require npm on the device.
- A Roon Server on the same LAN and a Roon account/subscription.
- ALSA and the libraries required by the current official Bridge build.
  Roon's `check.sh` runs before startup and failures appear in plugin status
  and the Kalinka journal. The official downloads can update independently
  of this plugin; see [Roon's Linux requirements](https://help.roonlabs.com/portal/en/kb/articles/linux-install).

Bridge runs on the **Kalinka server machine**, not on a remotely selected
Kalinka renderer. Install this plugin on the machine connected to the DAC.
Only the selected Roon output is coordinated; leave other outputs of the
managed Bridge disabled in Roon. Do not run a second standalone Bridge on
the same device.

## Setup

1. Install the matching Kalinka server/SDK and this plugin, then restart
   Kalinka. For a source checkout:

   ```sh
   /opt/kalinka/venv/bin/pip install .
   ```

   The service user needs access to `/dev/snd`. The Debian package supplies a
   systemd drop-in with `SupplementaryGroups=audio`. For a source installation,
   add the same setting using `sudo systemctl edit kalinka`, then run
   `sudo systemctl daemon-reload` and `sudo systemctl restart kalinka`.

2. In Kalinka settings enable **Roon Bridge**. The first start downloads the
   platform archive over HTTPS into `/var/lib/kalinka/roon/runtime/`.
3. In Roon **Settings → Audio**, enable the desired output on this device.
   In **Settings → Extensions**, authorize **Kalinka Roon Bridge** and pair
   it with your Roon Server.
4. Refresh Kalinka settings and set **This device's Roon output** to that
   output. Save/restart through Kalinka's settings flow. The choice stores the
   stable output ID, so renaming or regrouping a zone does not change it.
   Do this before starting playback; an unselected output cannot be coordinated.
5. Play to it from Roon. Kalinka shows Roon's now-playing state and supports
   pause, next, previous, and seek when Roon permits them. Stop or starting
   another Kalinka source sends Roon `stop`, which releases its audio device.

Pairing credentials are private files in `/var/lib/kalinka/roon/pairing/`.
Disabling the plugin stops its extension and all managed Bridge processes,
but keeps the download and pairing for the next enable. Paths honor
`KALINKA_PREFIX` for source installations. To forget a pairing, disable the
plugin, delete its `pairing/pairing.json`, then enable and authorize again.

## Playback and audio details

- Audio does not pass through Kalinka's decoder. Kalinka volume and DSP
  settings do not configure Roon; configure Roon's device and volume in Roon.
- A loading/playing event takes ownership from Kalinka's queue or another
  plugin. Queue playback, a different plugin, Stop, a renderer selection
  change, or shutdown revokes that ownership. The host waits for the plugin's
  Roon `stop` acknowledgement before completing handover. If control fails,
  the plugin kills its managed Bridge process group to release the DAC.
- Roon pause and stop both issue an explicit `stop` and give playback back
  to Kalinka. Resume from Roon. Queued playing/seek events cannot immediately
  reclaim a revoked hold: a quiet state followed by new playback is required.
  After restart/reconnection an already-playing snapshot is stopped first.
- With a grouped Roon zone, transport controls affect the entire group. The
  saved output ID follows that output when groups change.
- The public [transport API](https://github.com/RoonLabs/node-roon-api-transport/blob/master/lib.js)
  supplies now-playing text, duration, position and output identity, but not
  source codec, source sample rate, source bit depth, or Roon's full signal
  path. These are left unknown, rather than reported as bit-perfect or guessed.
- Optionally set **ALSA output details** to the chosen DAC's
  `/proc/asound/cardN/pcmMp/subK/hw_params`. This adds measured output rate
  and channel count. ALSA's container width is not source bit depth. The
  setting must refer to the device selected in Roon; there is no reliable
  public mapping from a Roon output ID to an ALSA device.
- Artwork is retrieved through the authorized [Roon image API](https://github.com/RoonLabs/node-roon-api-image/blob/master/lib.js)
  and served through Kalinka's content endpoint. Credentials and Roon Server
  addresses are not exposed to the app. The cache is limited to 32 images,
  each at most 2 MiB, requested at 600×600.

The Roon API announces playback after Roon initiates it; it provides no
pre-open callback for an ALSA device. The plugin releases Kalinka's renderer
on the earliest loading/playing event. Starting Roon while another source
owns the same exclusive DAC needs hardware testing; if Roon reports a busy
device before delivering an event, stop Kalinka playback and retry in Roon.
No source of silent audio or dummy renderer stream is used.

## Development and packaging

```sh
python3 -m venv .venv
.venv/bin/pip install ../RpiPlayer-roon/packages/kalinka-plugin-sdk
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
node --test tests/*.test.js
.venv/bin/ruff check src tests
PYTHON=.venv/bin/python scripts/build_deb.sh
```

The architecture-independent Debian package includes the Python wheel, Node
extension dependencies and audio-access drop-in. Bridge itself is chosen and
downloaded on first enable. No Roon installer script runs as root. Downloads
are bounded, extracted using tar's data filter, checked for traversal and
escaping links, and published atomically. Bridge's own supported updater is
free to update its writable installation.

CI currently builds against the server/SDK commit in PR #250. Until that
change ships in a Kalinka release, this plugin requires that development
server. CI artifacts are experimental builds, not a stable release.

Tests cover metadata, progress, artwork isolation and limits, grouping,
transport, source takeover, late events, disconnects, process-group teardown,
archive extraction, platform selection and disabled/unsupported-server setup.
The companion host tests cover arbitration against simulated renderers.
Actual Roon Server pairing, DAC handover, ARM runtime dependencies and live
audio quality still require device testing; this is not a production validation.

See [architecture and verification](docs/integration.md) for the implementation
contract, source references and a hardware acceptance procedure.

## License

Plugin: GPL-3.0-or-later. Roon's Node API packages retain their Apache-2.0
licenses; their transitive dependencies retain their own licenses. See
[THIRD_PARTY.md](THIRD_PARTY.md). Downloaded Bridge remains subject to Roon's
terms and is not covered by the plugin license.
