# Kalinka Roon Bridge

An experimental Roon endpoint plugin for [Kalinka](https://github.com/Kalinka-Player/KalinkaPlayer).
It uses a local Roon Server's audio endpoint when available. Otherwise, enabling
it downloads the official Roon Bridge and runs it as a child of Kalinka.
Roon sends audio directly to the local DAC through
RAAT. Kalinka displays the track, artist, album, artwork, progress and a **Roon
endpoint** badge. Its queue stays intact while Roon plays.

This is an independent integration, not Roon Ready certification or a Roon
Labs product. Roon Bridge is proprietary and is downloaded from Roon Labs at
runtime; it is not included in this repository, wheels or native packages.

The monochrome Roon wordmark identifies the music source in attribution and
plugin listings. Its origin is recorded in [NOTICE](NOTICE).

## Requirements

- Linux x86-64, ARM64, or ARMv7 hard-float, with Python 3.11+.
- Kalinka server with plugin artwork support and SDK **3.18+ and <4**.
  The server must also provide the **external playback API**; installing
  only a newer SDK is insufficient. The plugin refuses an unsupported server
  before downloading or starting anything.
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
The plugin automatically identifies enabled local ALSA outputs from the
local Roon Server's or managed Bridge's settings. It follows the local output playing in Roon and
defaults to the first local output, ordered by stable output ID, when idle.
Multiple enabled outputs do not require a Kalinka setting. Do not run a second
standalone Bridge on the same device.

On Linux, a local Roon Server running as the same Unix user is detected from its
RAAT process and open log file. Kalinka attaches to that endpoint and does not
launch an additional Bridge. It controls the selected zone through Roon's API
and leaves the Server process and library running when the plugin stops.

Kalinka intentionally keeps **one active source per server**. Starting Roon
stops that server's current source, including playback to a remote renderer.
Starting another source on that server stops Roon. This policy applies even
when the sources use different physical devices.

The playback hold does not identify every process sharing Bridge's local DAC.
A local renderer playing a session owned by another Kalinka server is **not**
stopped by this plugin, and can keep the DAC busy. Supporting that topology
would also require arbitration at the renderer host; the server's playback
hold does not cover it. See
[topology limits](docs/integration.md#topology-limits).

## Setup

1. Install the matching Kalinka server/SDK and this plugin. On a Kalinka
   machine set up from `.deb` packages, install or upgrade to the latest
   release with:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/Kalinka-Player/kalinka-plugin-roon/main/scripts/install-latest.sh | sudo bash
   ```

   The script checks the `.deb` against the release's `SHA256SUMS` and
   installs it with apt; Kalinka restarts by itself. For a source checkout,
   install it and restart Kalinka:

   ```sh
   /opt/kalinka/venv/bin/pip install .
   ```

   The service user needs access to `/dev/snd`. The Debian package supplies a
   systemd drop-in with `SupplementaryGroups=audio`. For a source installation,
   add the same setting using `sudo systemctl edit kalinka`, then run
   `sudo systemctl daemon-reload` and `sudo systemctl restart kalinka`.

2. In Kalinka settings enable **Roon**. If no local Roon Server is detected,
   the first start downloads the platform archive over HTTPS into
   `/var/lib/kalinka/roon/runtime/`.
3. In Roon **Settings → Audio**, enable the desired output on this device.
   In **Settings → Extensions**, authorize **Kalinka Roon Bridge** and pair
   it with your Roon Server.
4. Leave **Roon output override** empty. Kalinka matches the local endpoint's device
   UUID against the authorized Roon output subscription and detects its ALSA
   output details. Choose the playback destination in Roon's zone picker, or
   use Transfer Zone to move existing playback. Kalinka follows the active
   local output and retains that choice when idle. If several local outputs
   play simultaneously, it keeps the current active choice (or picks the first
   by ID); Kalinka displays one session. The advanced override can pin a specific
   output. Renaming or regrouping a zone does not change its identity. Clear a
   previously saved override to use automatic selection.
5. Play to it from Roon. Kalinka shows Roon's now-playing state and supports
   pause, next, previous, and seek when Roon permits them. Stop or starting
   another Kalinka source sends Roon `stop`, which releases its audio device.

If upgrading from the experimental source-control version, remove **Kalinka
Roon Bridge** from **External Source Controls** in the output's **Device Setup**
and restart Kalinka with the updated server and plugin. Keep the extension
authorized under **Settings → Extensions**. The plugin no longer advertises
source control. The handover wait and diagnostic settings have been removed.

Pairing credentials are private files in `/var/lib/kalinka/roon/pairing/`.
Disabling the plugin stops its extension and all managed Bridge processes,
but keeps the download and pairing for the next enable. Paths honor
`KALINKA_PREFIX` for source installations. To forget a pairing, disable the
plugin, delete its `pairing/pairing.json`, then enable and authorize again.

Desktop applications such as a browser can keep the ALSA device busy through
PipeWire. Roon may then report `DeviceOpenFailed` when enabling an output, and
that output will not appear in the transport API. Stop the other audio before
enabling it. Kalinka's exclusive session arbitrates its own sources; it does
not stop unrelated desktop applications.

## Playback and audio details

- On a server with external-volume support (SDK 3.11+ and matching server),
  Kalinka's slider controls the selected Roon output while Roon owns playback,
  including while paused. Changes made in Roon also update Kalinka. Local
  playback restores the renderer's controls; a configured downstream amplifier
  retains precedence. Older servers keep playback support without Roon volume.
- Numeric and dB volume ranges are normalized to 0–100 and respect Roon's limits.
  Fixed/increment-only outputs have no absolute slider. This controls the volume
  configured in Roon, not every ALSA mixer: a separate attenuated Speaker control
  can still limit loudness when Roon's Master control reads 100%.
- Audio does not pass through Kalinka's decoder. Configure Roon's device and
  DSP settings in Roon.
- A loading/playing event takes ownership from Kalinka's queue or another
  plugin. Queue playback, a different plugin, Stop, a renderer selection
  change, or shutdown revokes that ownership. The host waits for the plugin's
  Roon `stop` acknowledgement **and the actual ALSA close** before completing
  handover. If control or release fails, the plugin kills its managed Bridge
  process group to release the DAC.
  When attached to an existing Roon Server, a failed Stop is reported as an
  error; Kalinka never terminates that Server or its other zones.
- Pause keeps Roon's exclusive session, metadata and transport controls active.
  Resume from Kalinka or Roon. Kalinka Stop, an explicit source switch, or a
  Roon `stopped` state releases the hold. Queued playing/seek events cannot immediately
  reclaim a revoked hold: a quiet state followed by new playback is required.
  After restart/reconnection an already-playing snapshot is stopped first.
- With a grouped Roon zone, transport controls affect the entire group. The
  saved output ID follows that output when groups change.
- The public [transport API](https://github.com/RoonLabs/node-roon-api-transport/blob/master/lib.js)
  supplies now-playing text, duration, position and output identity, but not
  source codec, source sample rate, source bit depth, or Roon's full signal
  path. These are left unknown, rather than reported as bit-perfect or guessed.
- The managed Bridge's local settings supply its ALSA device automatically.
  `/proc/asound/cardN/pcmMp/subK/hw_params` supplies measured output rate and
  channels while that Bridge owns and runs the DAC. **ALSA output details**
  remains an optional override. ALSA container width is not source bit depth.
- Artwork is retrieved through the authorized [Roon image API](https://github.com/RoonLabs/node-roon-api-image/blob/master/lib.js)
  and served through Kalinka's content endpoint. Credentials and Roon Server
  addresses are not exposed to the app. The cache is limited to 32 images,
  each at most 2 MiB, requested at 600×600.

Roon can attempt to open the DAC before its transport notification reaches
Kalinka. After acquiring the playback hold, the plugin detects a fresh ALSA
`Device or resource busy` error for this output in the managed Bridge log. It
waits up to six seconds for the DAC to close (including a shared backend's idle
timeout) and retries Play once. Ordinary pauses, an ended
hold, and unrelated device errors do not trigger retries. Kalinka shows
Roon's transport state; buffering is shown while Roon loads or a confirmed
device-busy handover is being recovered. A short gap remains possible;
this is automatic recovery, not a pre-play RAAT handshake. No dummy renderer
stream is used.

At startup, the plugin cleans up orphaned processes from its own private Bridge
installation and data directory. This prevents a restarted launcher from
silently reusing an old RAAT process outside its managed process group. Local
ALSA ownership validates output measurements; it does not override Roon's
reported playback state.

The managed Bridge uses a private Roon instance name and localhost control port
to keep its RAAT process separate from a Roon Server on the same host. Existing
device identities are retained. Roon may require the device to be enabled again
under the Bridge's new instance name in Settings → Audio.

The UUID-to-output encoding and Bridge log format are observed implementation
details, not public Roon contracts. Detection accepts only exact IDs advertised
by the authorized transport API; unknown identities require the advanced
override. See [integration details](docs/integration.md) and the
[hardware E2E test](scripts/e2e.py).

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

The JavaScript dependencies occupy about 2.8 MiB; the system Node.js runtime
is additional (about 80 MiB for the Node 22 packages on our Fedora development
machine, varying by distribution and architecture). Native packages do not
depend on npm. Roon Bridge's own download/runtime is separate from these sizes.

CI pins a Kalinka core commit with SDK 3.18 artwork support.
CI artifacts from `main` are untagged development builds; install releases
instead.

To release, add a `## <version>` section to `CHANGELOG.md`, then push a
`kalinka-plugin-roon-v<version>` tag. The version comes from that tag;
the release workflow runs the checks, builds the `.deb` and publishes it with
`SHA256SUMS` to the GitHub release.

Tests cover metadata, progress, artwork isolation and limits, grouping,
transport, source takeover, late events, disconnects, process-group teardown,
archive extraction, platform selection and disabled/unsupported-server setup.
The companion host tests cover arbitration against simulated renderers.
The opt-in hardware E2E test exercises a real paired Roon Server, local ALSA
renderer and Chromium UI, including repeated source switches and output identity
with an empty override. See [hardware acceptance](docs/integration.md#hardware-acceptance-procedure).
Other platforms, grouped outputs and subjective audio quality need their own
validation.

See [architecture and verification](docs/integration.md) for the implementation
contract, source references and a hardware acceptance procedure.

## License

Plugin: GPL-3.0-or-later. Roon's Node API packages retain their Apache-2.0
licenses; their transitive dependencies retain their own licenses. See
[THIRD_PARTY.md](THIRD_PARTY.md). Downloaded Bridge remains subject to Roon's
terms and is not covered by the plugin license.
