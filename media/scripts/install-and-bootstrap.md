# Install Arbiter Server with Docker

Purpose: show a new operator how to install Arbiter with the Docker deployment
tooling, configure one local bot mail account, prove the staged server works,
then promote the checked deployment into a permanent host location.

Audience: an operator preparing an Arbiter server for the first time.

Target length: 2 to 3 minutes.

Maintenance note: review this script for every release that refreshes media.
If deployment helper commands, default ports, generated files, install
behavior, account templates, or the recommended first-run flow changes, update
the script before regenerating casts, narration, captions, or static renders.

Install-proof note: the recording should use the release-approved Docker
deployment tooling from the Arbiter server package. Do not show a local Python
virtual environment as the server install path in this recording. The only
local Python assumption is that the recorder can create an off-camera operator
environment that provides `arbiter-server` and `arbiter`.

## Setup

### Capture

- Terminal size: 100 columns by 28 rows.
- Render target: derive pixels from terminal dimensions, font size, line
  height, and player or renderer padding.
- Renderer font size: 16px.
- Renderer line height: 1.4.
- Prompt: short and quiet.
- Working directory: an empty operator workspace created by the recorder.
- Path: use the `arbiter-server` and `arbiter` commands from the recording
  package source. For this tutorial, the default source is PyPI latest via the
  `arbiter-suite` meta package. For a pinned release proof, set
  `package_source.version=VERSION` or
  `package_source.requirement=arbiter-suite==VERSION` as a Hydra override.
  Local checkout commands are reserved for development rehearsals and should be
  selected explicitly with `package_source=local`.
- Baseline capture: record as quickly as possible. The baseline cast is proof
  that the workflow runs; it is not the watchable edit.
- Presentation timing: generate a retimed cast from the baseline cast and
  timeline sidecar. The retimer synthesizes command typing, inserts short
  reading pauses after Enter and command output, and restores viewer holds.
- Timing: use event gates for command completion, server readiness, Docker
  health, and expected output; use fixed waits only for viewer pacing.
- JSON output: pipe user-facing JSON output through `jq` so it is readable.

### Color

- Theme: Dracula for rendered previews.
- Terminal captions and command prompts should use ANSI color so the recording
  is visually scannable even before website/player theming exists.
- Do not rely on local terminal colors as the durable visual source.
- Do not use color as the only way to communicate state.

### Audio

- Narration source: this script's `Narration` blocks.
- TTS credentials: read from the recording config's audio env var only when
  generating audio.
- Segment strategy: one generated audio segment per script section, with
  scripted silence between segments.
- Sentence timing can be derived from transcription timestamps after generation
  and used for captions or overlays.

### Demo Environment

- Docker: Docker Compose must be available to the operator running the staging
  checks.
- Package source: the recorder prepares an off-camera operator virtual
  environment before capture. In PyPI mode, it installs `arbiter-suite` from
  PyPI, resolving the latest non-yanked release unless
  `package_source.version` or `package_source.requirement` pins an exact
  package requirement. In local mode, it uses the `arbiter-server` and
  `arbiter` commands already on `PATH`. Do not let local package versions
  silently choose PyPI pins for this tutorial.
- Staging directory: create a disposable `arbiter-docker/` staging directory in
  the recording workspace.
- Permanent install target: promote the staged deployment to `/opt/arbiter`.
- Service user: use the default `arbiter` service user.
- Network: an installed production deployment defaults the Arbiter native HTTP
  service to host port `8075`. The Docker helper defaults staging to host port
  `18075` so a staged rehearsal can run beside an installed server without
  taking its production port. The recording uses the default staging port; if
  that port or staging container name is already in use, the recording should
  abort so the operator can clean the host before regenerating release media.
- Local mail lab: before recording starts, the recorder launches local Python
  SMTP and IMAP servers backed by the same in-memory mailbox. The bot account
  uses the same username and password on both services. The SMTP server
  delivers mail into the mailbox that the IMAP server exposes.
- Docker-to-host mail access: the generated Docker Compose template maps
  `host.docker.internal` to the host gateway, so the Arbiter container can
  reach the local recording mail lab without external mail services.
- Secrets: use generated local recording credentials only. Do not configure
  real mail credentials in this recording.
- Cleanup: the recorder stops the local mail lab and removes the temporary
  operator workspace after the session.

## Script

### Create Staging Directory

Show that Arbiter installation starts in a normal operator-owned staging
directory. Nothing is installed under `/opt` yet.

Narration:

"Start by creating a Docker staging directory. Arbiter writes the Compose file,
configuration directory, bundle metadata, and a local helper script here. This
is still ordinary operator-owned state, so it is safe to inspect and revise
before installing the permanent service."

Action:

```bash
arbiter-server deploy docker init
cd arbiter-docker
```

Wait:

Event gate: `init` exits successfully and the `arbiter-docker` helper exists in
the staging directory.

Viewer hold: pause 3 seconds on the generated directory.

### Prepare Mail Bundle

Select the IMAP and SMTP plugins and prepare the package bundle that the Docker
container will install at startup.

Narration:

"Next, add the mail plugins and prepare the runtime bundle. The staging
directory records the selected Arbiter package set and builds the wheelhouse
that the container will install from. Later starts do not depend on live PyPI
resolution."

Action:

```bash
./arbiter-docker bundle add imap
./arbiter-docker bundle add smtp
./arbiter-docker bundle prepare
```

Wait:

Event gate: command exits successfully and reports the prepared wheelhouse or
bundle state.

Viewer hold: pause 3 seconds after bundle preparation finishes.

### Bootstrap Bot Config

Create the server config and bot account files inside the staging directory.

Narration:

"Now bootstrap the Arbiter server configuration and the bot mail account. The
generated IMAP and SMTP account files live under the staged `conf` directory,
beside the Docker wrapper files, so staging and the permanent service use the
same checked configuration package."

Action:

```bash
arbiter-server --config-dir ./conf bootstrap arbiter
arbiter-server --config-dir ./conf bootstrap plugin imap account bot
arbiter-server --config-dir ./conf bootstrap plugin smtp account bot
arbiter-server --config-dir ./conf config activate account imap bot
arbiter-server --config-dir ./conf config activate account smtp bot
```

Wait:

Event gate: commands exit successfully and create the server config, IMAP bot
account, SMTP bot account, and matching policies.

Viewer hold: pause 4 seconds on the written paths.

### Bootstrap Env File

Generate the staged config env file after the accounts are active.

Narration:

"The account files refer to credentials through environment variables. Bootstrap
the staged `.env` file now, after the accounts are active, so Arbiter can add
the credential placeholders that this deployment needs. Rerunning this command
later preserves existing values and adds only missing variables."

Action:

```bash
arbiter-server --config-dir ./conf env bootstrap
```

Wait:

Event gate: command exits successfully and writes `conf/.env`.

Viewer hold: pause 3 seconds on the env bootstrap output.

### Inspect Bot Endpoints

Show the configured local bot endpoints without exposing secrets.

Narration:

"For this recording, the bot account points at a local mail lab. The SMTP and
IMAP endpoints are both reached through `host.docker.internal`, and TLS is
disabled because the lab runs only on the local recording host. Real
deployments should use their actual provider hosts, TLS settings, and secrets."

Action:

```bash
sed -n '1,18p' conf/arbiter/account/imap/bot.yaml
sed -n '1,16p' conf/arbiter/account/smtp/bot.yaml
```

Wait:

Event gate: output shows `host.docker.internal` and `tls: none`.

Viewer hold: pause 4 seconds on the account endpoints.

### Start Staged Server

Run the staged Docker service before installing it permanently.

Narration:

"Start the staged server first. An installed production Arbiter service uses
port 8075 by default; staging publishes the same native HTTP service on 18075
so both can exist on one host during rollout. This recording intentionally uses
that staging default. If the port or staging container name is already taken,
the recording stops instead of silently changing the install story. This
validates the Docker wrapper, the prepared bundle, the mounted config, the
local mail account configuration, and the server startup path before any
privileged install step."

Action:

```bash
./arbiter-docker up
./arbiter-docker test
arbiter arbiter.url=http://127.0.0.1:18075 info plugins | jq .plugins
```

Wait:

Event gate: the helper reports the staged server URL, `test` passes, and the
client sees both `imap` and `smtp`.

Viewer hold: pause 5 seconds on the passing plugin output.

### Preinstall Check

Run the install readiness check from the staged directory.

Narration:

"Before promotion, run the preinstall doctor. This catches common mistakes
while the deployment is still staged and easy to fix."

Action:

```bash
./arbiter-docker doctor --preinstall
```

Wait:

Event gate: doctor exits successfully.

Viewer hold: pause 3 seconds on the successful preinstall result.

### Review Install Plan

Review the permanent install plan without changing the host.

Narration:

"Once staging passes, review the install plan. The installed deployment is the
production shape, so its default HTTP port is 8075 unless the operator changes
the install environment. The dry run shows the permanent target, the service
identity, the systemd unit work, and the checks the helper would perform, but
it does not copy files into `/opt`, create users, or restart services."

Action:

```bash
./arbiter-docker install --dry-run --to /opt/arbiter --user arbiter
```

Wait:

Event gate: dry run exits successfully and reports the planned install actions.

Viewer hold: pause 5 seconds on the dry-run summary.

## Marker Plan

- `init-staging`: create the Docker staging directory
- `prepare-bundle`: select IMAP and SMTP and prepare the container runtime
  bundle
- `bootstrap-config`: create staged server config and bot account config
- `bootstrap-env`: bootstrap the staged `conf/.env` file
- `inspect-bot-config`: show local SMTP and IMAP endpoints without secrets
- `stage-server`: start and test the staged Docker server
- `preinstall-check`: run the staging preinstall doctor
- `review-install-plan`: review the non-privileged permanent install dry run

## Future Tracks

- Drive a real terminal editor for account config editing once the recorder has
  editor-control primitives.
- Add a second recording that sends a message through SMTP and reads it back
  through IMAP using the local mail lab.
- If a future recording must show a completed host install, run it only inside
  a disposable VM or image where privileged operations cannot affect the
  operator host.
