# Install Arbiter Server with Docker

```studio-directive
scene: Install Arbiter Server
```

Purpose: show a new operator how to install Arbiter with the Docker deployment
tooling, configure one local bot mail account, prove the staged server works,
then promote the checked deployment into a permanent host location.

Audience: an operator preparing an Arbiter server for the first time.

Target length: 3 to 4 minutes.

Maintenance note: review this script for every release that refreshes media.
If deployment helper commands, default ports, generated files, install
behavior, account templates, or the recommended first-run flow changes, update
the script before regenerating casts, narration, captions, or static renders.

Install-proof note: the recording should use the release-approved Docker
deployment tooling from the Arbiter server package. Do not show a local Python
virtual environment as the server runtime install path in this recording. The
operator creates a small local Python environment only to install the Arbiter
CLI commands used to bootstrap and inspect the Docker deployment.

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

- Narration source: this script's `studio-directive` beat narration.
- TTS credentials: read from the recording config's audio env var only when
  generating audio.
- Segment strategy: one generated audio segment per narrated beat, with
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
  taking its production port. The recording uses the default staging port, but
  writes a recording-specific Docker subnet into the disposable `docker.env`
  before the visible checks run. If the staging port or staging container name
  is already in use, the recording should abort so the operator can clean the
  host before regenerating release media.
- Local mail lab: before recording starts, the recorder launches local Python
  SMTP and IMAP servers backed by the same in-memory mailbox. The bot account
  uses the same username and password on both services. The SMTP server
  delivers mail into the mailbox that the IMAP server exposes.
- Docker-to-host mail access: the generated Docker Compose template maps
  `host.docker.internal` to the host gateway, so the Arbiter container can
  reach the local recording mail lab without external mail services.
- Secrets: use generated local recording credentials only. Do not configure
  real mail credentials in this recording.
- Cleanup: the recording config includes a hidden cleanup directive that stops
  the Docker staging deployment. The recorder also stops the local mail lab and
  removes temporary operator workspace after the session.

## Script

### Overview

```studio-directive
beat:
  id: overview
  heading: Overview
  narration: >-
    This video guides you through installing Arbiter for the first time using
    Arbiter's Docker tooling. We create a local command environment, create a
    staging directory, select the IMAP and SMTP plugins, configure and test
    them through the Arbiter client, and finish by reviewing the permanent
    install plan.
```

Introduce the workflow before the terminal commands begin.

Wait:

Viewer hold: pause until the overview narration segment completes.

### Prepare Arbiter Commands

```studio-directive
beat:
  id: prepare-cli
  heading: Prepare Arbiter Commands
  narration: >-
    First prepare a small Python virtual environment for the Arbiter command
    line tools. This installs `arbiter-server`, which creates the Docker
    staging directory, and `arbiter`, which we use later to test the staged
    server. This environment is only needed while preparing and testing
    staging; after permanent installation, the server runs from the installed
    deployment and no longer depends on it.
```

Install the Arbiter command line tools in an operator-owned virtual
environment before creating the Docker staging directory.

Action:

```bash
python3 -m venv arbiter_venv
arbiter_venv/bin/python -m pip install arbiter-suite
source arbiter_venv/bin/activate
arbiter-server version
```

Wait:

Event gate: `arbiter-server version` exits successfully.

Viewer hold: pause 3 seconds on the installed CLI version.

### Bootstrap Docker Staging Directory

```studio-directive
beat:
  id: init-staging
  heading: Bootstrap Docker Staging Directory
  narration: >-
    Start by creating a Docker staging directory. This workspace is where the
    operator prepares configuration, builds the runtime bundle, and tests the
    deployment before installing the server and config permanently.
```

Show that installation begins in a staging directory owned by the operator.
Nothing is installed as a system service yet.

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

```studio-directive
beat:
  id: prepare-bundle
  heading: Prepare Mail Bundle
  narration: >-
    This demo uses the IMAP and SMTP plugins. The bundle records the server and
    plugin package set, then prepares the wheelhouse the container installs
    from. That makes later staging runs independent of live package resolution.
```

Inspect the selected IMAP and SMTP plugin bundle and prepare the package set
that the Docker container will install at startup.

Action:

```bash
./arbiter-docker bundle list
./arbiter-docker bundle prepare
```

Wait:

Event gate: command exits successfully and reports the prepared wheelhouse or
bundle state.

Viewer hold: pause 3 seconds after bundle preparation finishes.

### Bootstrap Bot Account Files

```studio-directive
beat:
  id: bootstrap-config
  heading: Bootstrap Bot Account Files
  narration: >-
    Next create the server config and the bot account scaffolds. Arbiter writes
    separate account and policy files for IMAP and SMTP. The accounts describe
    where the services are; the policies describe what an agent is allowed to do
    through those accounts.
```

Create the server config and bot account files inside the staging directory.

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

### Review Generated Bot Config

```studio-directive
beat:
  id: review-generated-config
  heading: Review Generated Bot Config
  narration: >-
    Before editing, inspect the generated files. The bot has one IMAP account,
    one SMTP account, and matching policies. In a real deployment these files
    point at your mail provider and enforce the access level you want agents to
    have.
```

Show the generated bot account and policy files before editing them.

Action:

```bash
sed -n '1,26p' conf/arbiter/account/imap/bot.yaml
sed -n '1,34p' conf/arbiter/policy/imap/bot_policy.yaml
sed -n '1,26p' conf/arbiter/account/smtp/bot.yaml
sed -n '1,30p' conf/arbiter/policy/smtp/bot_policy.yaml
```

Wait:

Event gate: output shows the generated account and policy files.

Viewer hold: pause 4 seconds on the generated config.

### Edit Bot Access

```studio-directive
beat:
  id: edit-bot-access
  heading: Edit Bot Access
  narration: >-
    For this tutorial the bot is intentionally broad: it can read, search,
    append, mark, move, and delete in the local IMAP mailbox, and it can send
    through the local SMTP account. This is safe here because the recording uses
    disposable mail servers created just for the demo.
```

Apply the demo's desired bot access level.

Action:

```bash
$EDITOR conf/arbiter/account/imap/bot.yaml conf/arbiter/policy/imap/bot_policy.yaml
$EDITOR conf/arbiter/account/smtp/bot.yaml conf/arbiter/policy/smtp/bot_policy.yaml
sed -n '1,26p' conf/arbiter/account/imap/bot.yaml
sed -n '1,34p' conf/arbiter/policy/imap/bot_policy.yaml
sed -n '1,26p' conf/arbiter/account/smtp/bot.yaml
sed -n '1,30p' conf/arbiter/policy/smtp/bot_policy.yaml
```

Wait:

Event gate: output shows `host.docker.internal`, `allow_glob: '*'`,
`delete: allow`, `folder_append: allow`, and unrestricted SMTP limits.

Viewer hold: pause 5 seconds on the edited policy files.

### Bootstrap Env File

```studio-directive
beat:
  id: bootstrap-env
  heading: Bootstrap Env File
  narration: >-
    The account files read credentials from the deployment environment. This
    demo uses dedicated local mail servers, so it is okay to show the generated
    username and password. In production, the env file contains real secrets and
    should stay protected.
```

Generate the staged config env file after the accounts are active, then fill it
with demo credentials.

Action:

```bash
arbiter-server --config-dir ./conf env bootstrap
sed -n '1,16p' conf/.env
```

Wait:

Event gate: command exits successfully and writes `conf/.env` with the local
mail-lab credentials.

Viewer hold: pause 4 seconds on the env file.

### Check and Start Staging

```studio-directive
beat:
  id: stage-server
  heading: Check and Start Staging
  narration: >-
    Before starting the server, run a config check. It verifies the server
    config, the plugin configuration, and the account-to-policy pairs. Then
    start staging on port 18075 and run the helper's server test.
```

Run a config check, then start the staged Docker service.

Action:

```bash
./arbiter-docker config check
./arbiter-docker up
./arbiter-docker test
```

Wait:

Event gate: config check passes, the helper reports the staged server URL, and
the server test passes.

Viewer hold: pause 5 seconds on the passing test output.

### Discover with the Arbiter Client

```studio-directive
beat:
  id: client-discovery
  heading: Discover with the Arbiter Client
  narration: >-
    Now use the Arbiter client against the staged server. Agents start by
    discovering plugins, then inspect accounts and operation schemas before
    running an operation. They see capabilities and policy-shaped controls, not
    service credentials.
```

Show the client commands an agent uses to discover available capabilities.

Action:

```bash
arbiter arbiter.url=http://127.0.0.1:18075 plugins | jq .
arbiter arbiter.url=http://127.0.0.1:18075 plugins smtp account bot | jq .
arbiter arbiter.url=http://127.0.0.1:18075 op list smtp | jq .
arbiter arbiter.url=http://127.0.0.1:18075 op desc smtp:send_email | jq '{id, description, input_schema}'
```

Wait:

Event gate: the client sees `imap`, `smtp`, the bot account, and
`smtp:send_email`.

Viewer hold: pause 5 seconds on the operation schema.

### Send a Test Message

```studio-directive
beat:
  id: send-test-message
  heading: Send a Test Message
  narration: >-
    With discovery complete, send a real message through the staged server. The
    SMTP plugin submits the message using the configured bot account. The caller
    provides only the operation input: account name, recipient, subject, body,
    and an idempotency key for safe retries.
```

Send a message from the bot SMTP account to the bot mailbox.

Action:

```bash
arbiter arbiter.url=http://127.0.0.1:18075 op run smtp:send_email --args '{"account":"bot","to":["bot@example.test"],"subject":"Arbiter install smoke test","text_body":"Hello from Arbiter staging.","idempotency_key":"install-smoke-1"}' | jq .
```

Wait:

Event gate: the send operation exits successfully, returns a message id, and a
hidden readiness check confirms that the delivered message is visible through
IMAP before the fetch beat starts.

Viewer hold: pause 4 seconds on the send result.

### Fetch the Test Message

```studio-directive
beat:
  id: fetch-test-message
  heading: Fetch the Test Message
  narration: >-
    The local SMTP server delivers into the same mailbox that the IMAP server
    exposes. Search the bot inbox by subject, keep the returned IMAP UID, then
    fetch the message body through `imap:get_message`.
```

Use IMAP operations to find and read the message that SMTP delivered.

Action:

```bash
message_uid="$(arbiter arbiter.url=http://127.0.0.1:18075 op run imap:search_messages --args '{"account":"bot","folder":"INBOX","query":"Arbiter install smoke test","limit":1}' | jq -er '.result.messages[0].uid')"
arbiter arbiter.url=http://127.0.0.1:18075 op run imap:get_message --args "{\"account\":\"bot\",\"folder\":\"INBOX\",\"message_id\":\"$message_uid\"}" | jq '{subject: .result.message.subject, text_body: .result.message.text_body}'
```

Wait:

Event gate: search returns an IMAP UID and fetch returns the test message
subject and body.

Viewer hold: pause 5 seconds on the fetched message.

### Staging Versus Installed

```studio-directive
beat:
  id: staging-vs-installed
  heading: Staging Versus Installed
  narration: >-
    Staging proves the bundle, configuration, mail access, and client operations
    before any privileged install step. It is still an operator workspace, not
    the long-running production service. The installed deployment uses protected
    ownership, systemd lifecycle management, and the production port, 8075.
```

Explain why passing staging is necessary but not the final deployment shape.

Action:

```bash
printf 'staging URL:   http://127.0.0.1:18075\n'
printf 'installed URL: http://127.0.0.1:8075\n'
```

Wait:

Viewer hold: pause 3 seconds on the port comparison.

### Preinstall Doctor

```studio-directive
beat:
  id: preinstall-check
  heading: Preinstall Doctor
  narration: >-
    Before promotion, run the preinstall doctor. Passing `--agent-user codex`
    also checks that the agent identity does not have inappropriate access to
    deployment state. This catches production-install mistakes while the
    deployment is still staged and easy to fix.
```

Run the install readiness check from the staged directory, including the Codex
agent identity.

Action:

```bash
./arbiter-docker doctor --preinstall --agent-user codex
```

Wait:

Event gate: doctor exits successfully.

Viewer hold: pause 4 seconds on the successful preinstall result.

### Review Install Plan

```studio-directive
beat:
  id: review-install-plan
  heading: Review Install Plan
  narration: >-
    Finally, review the install plan. A dry run shows what promotion would do:
    copy the checked deployment to the protected target, preserve or replace
    installed config according to flags, set ownership, write the systemd unit,
    and prepare the production service. The recording stops here, before real
    sudo or host mutation.
```

Review the permanent install plan without changing the host.

Action:

```bash
./arbiter-docker install --dry-run --to /opt/arbiter --user arbiter
```

Wait:

Event gate: dry run exits successfully and reports the planned install actions.

Viewer hold: pause 5 seconds on the dry-run summary.

## Marker Plan

- `init-staging`: create the Docker staging directory
- `prepare-bundle`: inspect the IMAP and SMTP bundle and prepare the container
  runtime bundle
- `bootstrap-config`: create staged server config and bot account config
- `review-generated-config`: inspect generated account and policy files
- `edit-bot-access`: apply local mail-lab endpoints and broad bot policies
- `bootstrap-env`: bootstrap and show the staged `conf/.env` file
- `stage-server`: config-check, start, and test the staged Docker server
- `client-discovery`: inspect plugins, account, and operation schema
- `send-test-message`: send mail through SMTP
- `fetch-test-message`: read the delivered message through IMAP
- `staging-vs-installed`: compare staging and installed server roles
- `preinstall-check`: run the staging preinstall doctor
- `review-install-plan`: review the non-privileged permanent install dry run

## Future Tracks

- Replace the non-interactive config patch with a real driven terminal editor
  once the recorder has editor-control primitives.
- If a future recording must show a completed host install, run it only inside
  a disposable VM or image where privileged operations cannot affect the
  operator host.
