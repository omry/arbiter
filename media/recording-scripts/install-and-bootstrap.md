# Install Arbiter Server with Docker

```yaml studio-directive
scene: Install Arbiter Server
```

```yaml studio-directive
recording:
  vars:
    loopback_host: 127.0.0.1
    staging_port: 18075
    installed_port: 8075
    staging_url: "https://${.loopback_host}:${.staging_port}"
    installed_url: "https://${.loopback_host}:${.installed_port}"
    arbiter_server: "arbiter-server --config-dir ./conf"
  id: install-and-bootstrap
  title: Install Arbiter Server
  capture:
    window_size: 100x28
    headless: true
    baseline_compressed: true
  requirements:
    commands:
    - docker
    - fakeroot
    - jq
  style:
    color: true
    typing: true
    typing_min_delay: 0.025
    typing_max_delay: 0.095
    typing_space_delay: 0.04
    typing_punctuation_delay: 0.08
    typing_newline_delay: 0.22
    typing_seed: 17
  outputs:
    cast: website/static/casts/install-and-bootstrap.cast
    audio: website/static/audio/casts/install-and-bootstrap.mp3
  publish:
    default: docusaurus
    surfaces:
      docusaurus:
        type: docusaurus_mdx
        file: website/docs/media/terminal-recordings.mdx
        placeholder: install-and-bootstrap
        component: TerminalCast
        intro_segment: overview
      standalone_html:
        type: standalone_html
        file: website/static/casts/install-and-bootstrap.html
        intro_segment: overview
  retime:
    typing_char_delay: 0.035
    typing_space_delay: 0.02
    typing_punctuation_delay: 0.05
    typing_newline_delay: 0.0
    post_enter_pause: 0.35
    post_command_pause: 0.85
  environment:
    working_directory: .
    variables:
      ARBITER_CINEMA_STAGING_SUBNET: 10.213.240.0/24
      ARBITER_CINEMA_STAGING_URL: ${recording.vars.staging_url}
      ARBITER_CINEMA_INSTALLED_URL: ${recording.vars.installed_url}
  audio:
    enabled: true
    provider: openai
    env: OPENAI_ARBITER_CINEMA_AUDIO_API_KEY
    model: gpt-4o-mini-tts
    voice: marin
    format: mp3
    instructions: Speak clearly and calmly, like a concise technical walkthrough.
    cache_dir: media/cache/audio
    transcription:
      model: whisper-1
      timestamp_granularities:
      - word
      - segment
  parameters:
    arbiter_source:
      default: latest
    arbiter_package:
      default: arbiter-suite
    operator_venv_cache_retain:
      default: 8
  setup:
  - name: Prepare operator commands and local mail lab
    expect:
      file_exists:
      - $MAIL_LAB_ENV_FILE
    run_file: media/recording-scripts/install-and-bootstrap/setup-main.sh
  cleanup:
  - name: Stop Docker staging deployment
    run: |
      if [[ -f ./arbiter-docker && -x ./arbiter-docker ]]; then
        COMPOSE_PROGRESS=quiet ./arbiter-docker down --remove-orphans 2> >(recording_filter_docker_compose_progress >&2)
      elif [[ -f arbiter-docker/arbiter-docker && -x arbiter-docker/arbiter-docker ]]; then
        (cd arbiter-docker && COMPOSE_PROGRESS=quiet ./arbiter-docker down --remove-orphans 2> >(recording_filter_docker_compose_progress >&2))
      fi
  beats:
  - id: prepare-cli
    marker: prepare-cli
    caption: Create and activate a virtual environment.
    guide:
      try_command: source arbiter_venv/bin/activate && arbiter-server version
      success_hint: You should see the Arbiter server version from the local virtual environment.
    actions:
    - display: |
        python3 -m venv arbiter_venv
        source arbiter_venv/bin/activate
      run: |
        recording_prepare_cli_env
        source arbiter_venv/bin/activate
      expect:
        file_exists:
        - ./arbiter_venv/bin/activate
    viewer_hold: 2.0
  - id: install-suite
    marker: install-suite
    caption: Install the Arbiter suite packages.
    guide:
      try_command: source arbiter_venv/bin/activate && arbiter-server version
      success_hint: You should see the Arbiter server version from the local virtual environment.
    actions:
    - display: |
        arbiter_venv/bin/python -m pip install arbiter-suite
        arbiter-server version
      run: |
        true
        arbiter-server version
      expect:
        file_exists:
        - ./arbiter_venv/bin/arbiter-server
        output_contains:
        - server
        - api
    viewer_hold: 3.0
  - id: init-staging
    marker: init-staging
    caption: Create a Docker staging deployment.
    guide:
      try_command: cd arbiter-docker && ./arbiter-docker bundle list
      success_hint: You should enter the staging directory and see the selected runtime bundle.
    actions:
    - display: |
        arbiter-server deploy docker init
        cd arbiter-docker
      run: |
        arbiter-server deploy docker init
        recording_configure_staging_subnet
        cd arbiter-docker
      expect:
        file_exists:
        - ./arbiter-docker
        - ./compose.yaml
        - ./docker.env
    viewer_hold: 3.0
  - id: prepare-bundle
    marker: prepare-bundle
    caption: Inspect the mail plugin bundle and prepare the runtime.
    guide:
      try_command: ./arbiter-docker bundle list
      success_hint: You should still be inside arbiter-docker and see the prepared bundle
        selection.
    actions:
    - display: |
        ./arbiter-docker bundle list
        ./arbiter-docker bundle prepare
      run: |
        ./arbiter-docker bundle list
        recording_prepare_bundle
      expect:
        file_exists:
        - ./requirements.txt
    viewer_hold: 3.0
  - id: bootstrap-config
    marker: bootstrap-config
    caption: Bootstrap the server and bot account config.
    guide:
      try_command: test -f conf/arbiter/account/imap/bot.yaml && test -f conf/arbiter/account/smtp/bot.yaml
        && echo "bot accounts generated"
      success_hint: You should see "bot accounts generated".
    actions:
    - run: |
        ${recording.vars.arbiter_server} bootstrap arbiter
        ${recording.vars.arbiter_server} bootstrap plugin imap account bot
        ${recording.vars.arbiter_server} bootstrap plugin smtp account bot
        ${recording.vars.arbiter_server} config activate account imap bot
        ${recording.vars.arbiter_server} config activate account smtp bot
      expect:
        file_exists:
        - ./conf/arbiter-server.yaml
        - ./conf/arbiter/account/imap/bot.yaml
        - ./conf/arbiter/account/smtp/bot.yaml
    viewer_hold: 4.0
  - id: review-generated-config
    marker: review-generated-config
    caption: Inspect the generated bot account and policy files.
    actions:
    - run: |
        sed -n '1,26p' conf/arbiter/account/imap/bot.yaml
        sed -n '1,34p' conf/arbiter/policy/imap/bot_policy.yaml
        sed -n '1,26p' conf/arbiter/account/smtp/bot.yaml
        sed -n '1,30p' conf/arbiter/policy/smtp/bot_policy.yaml
      expect:
        output_contains:
        - 'host: imap.example.com'
        - 'folder_access:'
        - 'host: smtp.example.com'
        - 'recipient_policy:'
    viewer_hold: 4.0
  - id: edit-bot-access
    marker: edit-bot-access
    caption: Edit bot accounts and policies for the local mail lab.
    guide:
      try_command: grep -R "host.docker.internal" conf/arbiter/account/imap/bot.yaml conf/arbiter/account/smtp/bot.yaml
      success_hint: You should see both bot account files pointing at host.docker.internal.
    actions:
    - display: |
        $EDITOR conf/arbiter/account/imap/bot.yaml conf/arbiter/policy/imap/bot_policy.yaml
        $EDITOR conf/arbiter/account/smtp/bot.yaml conf/arbiter/policy/smtp/bot_policy.yaml
        sed -n '1,26p' conf/arbiter/account/imap/bot.yaml
        sed -n '1,34p' conf/arbiter/policy/imap/bot_policy.yaml
        sed -n '1,26p' conf/arbiter/account/smtp/bot.yaml
        sed -n '1,30p' conf/arbiter/policy/smtp/bot_policy.yaml
      run: |
        recording_apply_mail_lab_config
        sed -n '1,26p' conf/arbiter/account/imap/bot.yaml
        sed -n '1,34p' conf/arbiter/policy/imap/bot_policy.yaml
        sed -n '1,26p' conf/arbiter/account/smtp/bot.yaml
        sed -n '1,30p' conf/arbiter/policy/smtp/bot_policy.yaml
      expect:
        output_contains:
        - 'host: host.docker.internal'
        - 'allow_glob: ''*'''
        - 'delete: allow'
        - 'folder_append: allow'
        - 'max_messages_per_minute: null'
        - 'max_recipients_per_message: null'
    viewer_hold: 5.0
  - id: bootstrap-env
    marker: bootstrap-env
    caption: Bootstrap the deployment env file with demo credentials.
    guide:
      try_command: grep "BOT_ACCOUNT" conf/.env
      success_hint: You should see IMAP and SMTP bot username and password entries.
    actions:
    - display: |
        arbiter-server --config-dir ./conf env bootstrap
        sed -n '1,16p' conf/.env
      run: |
        ${recording.vars.arbiter_server} env bootstrap
        recording_apply_mail_lab_config --update-env
        sed -n '1,16p' conf/.env
      expect:
        file_exists:
        - ./conf/.env
        output_contains:
        - IMAP_BOT_ACCOUNT_USERNAME=bot@example.test
        - IMAP_BOT_ACCOUNT_PASSWORD=bot-password
        - SMTP_BOT_ACCOUNT_USERNAME=bot@example.test
        - SMTP_BOT_ACCOUNT_PASSWORD=bot-password
    viewer_hold: 4.0
  - id: stage-server
    marker: stage-server
    caption: Check config and start staging on the staging HTTPS port.
    guide:
      try_command: ./arbiter-docker test
      success_hint: You should see the staged server test pass on ${recording.vars.staging_url}.
    actions:
    - display: |
        ./arbiter-docker config check
        ./arbiter-docker up
        ./arbiter-docker test
      run: |
        COMPOSE_PROGRESS=quiet ./arbiter-docker config check 2> >(recording_filter_docker_compose_progress >&2)
        COMPOSE_PROGRESS=quiet ./arbiter-docker up 2> >(recording_filter_docker_compose_progress >&2)
        ./arbiter-docker test
      expect:
        output_regex:
        - server\s+\|\s+pass
        - imap\s+\|\s+pass
        - smtp\s+\|\s+pass
        output_contains:
        - 'URL:'
        - ${recording.vars.staging_url}
        - 'Server test:'
    viewer_hold: 5.0
  - id: client-discovery
    marker: client-discovery
    caption: Discover capabilities with the Arbiter client.
    guide:
      try_command: arbiter arbiter.url=${recording.vars.staging_url} plugins
        | jq .
      success_hint: You should see the imap and smtp plugins.
    actions:
    - run: |
        arbiter arbiter.url=${recording.vars.staging_url} plugins | jq .
        arbiter arbiter.url=${recording.vars.staging_url} plugins smtp account bot | jq .
        arbiter arbiter.url=${recording.vars.staging_url} op list smtp | jq .
        arbiter arbiter.url=${recording.vars.staging_url} op desc smtp:send_email | jq '{id, description, input_schema}'
      expect:
        output_contains:
        - imap
        - smtp
        - bot
        - smtp:send_email
        - input_schema
    viewer_hold: 5.0
  - id: send-test-message
    marker: send-test-message
    caption: Send a test message from the bot to itself.
    guide:
      try_command: arbiter arbiter.url=${recording.vars.staging_url} op run imap:search_messages
        --args '{"account":"bot","folder":"INBOX","query":"Arbiter install smoke test","limit":1}'
        | jq .
      success_hint: You should see one matching message in the bot INBOX.
    actions:
    - run: |
        arbiter arbiter.url=${recording.vars.staging_url} op run smtp:send_email --args '{"account":"bot","to":["bot@example.test"],"subject":"Arbiter install smoke test","text_body":"Hello from Arbiter staging.","idempotency_key":"install-smoke-1"}' | jq .
      expect:
        output_contains:
        - message_id
        - recipient_count
        - sent_copy
    checks:
    - name: Wait for delivered test message
      run_file: media/recording-scripts/install-and-bootstrap/wait-for-delivered-message.sh
      expect:
        exit_code: 0
    viewer_hold: 4.0
  - id: fetch-test-message
    marker: fetch-test-message
    caption: Fetch the delivered message through IMAP.
    guide:
      try_command: arbiter arbiter.url=${recording.vars.staging_url} op run imap:search_messages
        --args '{"account":"bot","folder":"INBOX","query":"Arbiter install smoke test","limit":1}'
        | jq -er '.result.messages[0].subject'
      success_hint: You should see "Arbiter install smoke test".
    actions:
    - run: |
        message_uid="$(arbiter arbiter.url=${recording.vars.staging_url} op run imap:search_messages --args '{"account":"bot","folder":"INBOX","query":"Arbiter install smoke test","limit":1}' | jq -er '.result.messages[0].uid')"
        printf 'message_uid=%s\n' "$message_uid" && \
        arbiter arbiter.url=${recording.vars.staging_url} op run imap:get_message --args "{\"account\":\"bot\",\"folder\":\"INBOX\",\"message_id\":\"$message_uid\"}" | jq '{subject: .result.message.subject, text_body: .result.message.text_body}'
      progress:
      - search delivered message
      - fetch delivered message
      expect:
        output_contains:
        - message_uid=
        - Arbiter install smoke test
        - Hello from Arbiter staging.
    viewer_hold: 5.0
  - id: staging-vs-installed
    marker: staging-vs-installed
    caption: Compare staging and installed server URLs.
    actions:
    - run: |
        printf 'staging URL:   ${recording.vars.staging_url}\n'
        printf 'installed URL: ${recording.vars.installed_url}\n'
      expect:
        output_contains:
        - 'staging URL:'
        - 'installed URL:'
    viewer_hold: 3.0
  - id: preinstall-check
    marker: preinstall-check
    caption: Run the preinstall doctor for the Codex agent user.
    guide:
      try_command: ./arbiter-docker doctor --preinstall --agent-user codex
      success_hint: The doctor should pass before you promote the staged deployment.
    actions:
    - run: |
        ./arbiter-docker doctor --preinstall --agent-user codex
    viewer_hold: 4.0
  - id: install-server
    marker: install-server
    caption: Install Arbiter into /opt/arbiter.
    actions:
    - display: |
        # Defaults install to /opt/arbiter as user arbiter.
        sudo ./arbiter-docker install
      run_file: media/recording-scripts/install-and-bootstrap/install-server.sh
      expect:
        output_contains:
        - Installed Arbiter to
        - /opt/arbiter
        - 'systemd unit:'
        - 'ExecStart='
        - systemctl daemon-reload
        - systemctl enable arbiter.service
    viewer_hold: 5.0
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
  `+script_params.arbiter_source=VERSION` as a Hydra override. Local checkout
  commands are reserved for development rehearsals and should be selected
  explicitly with `+script_params.arbiter_source=local`.
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
  `+script_params.arbiter_source=VERSION` pins an exact package version. In
  local mode, it uses the `arbiter-server` and `arbiter` commands already on
  `PATH`. Do not let local package versions silently choose PyPI pins for this
  tutorial.
- Staging directory: create a disposable `arbiter-docker/` staging directory in
  the recording workspace.
- Permanent install target: promote the staged deployment to `/opt/arbiter`.
- Service user: use the default `arbiter` service user.
- Network: an installed production deployment defaults the Arbiter server URL
  to host port `8075`. The Docker helper defaults staging to host port
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

```yaml studio-directive
beat:
  id: overview
  heading: Overview
  narration: >-
    In this tutorial, we will prepare Arbiter in a staging directory, configure
    the server, test it with the Arbiter client, and then install it as a
    permanent Docker service.
```

Introduce the workflow before the terminal commands begin.

Wait:

Viewer hold: pause until the overview narration segment completes.

### Create and activate a virtual environment

```yaml studio-directive
beat:
  id: prepare-cli
  heading: Create and activate a virtual environment
  narration: >-
    Create a small Python virtual environment and activate it. This keeps the
    Arbiter command line tools separate from the system Python while we prepare
    and test the deployment.
```

Create and activate an operator-owned virtual environment before installing
Arbiter's command line packages.

Action:

```bash
python3 -m venv arbiter_venv
source arbiter_venv/bin/activate
```

Wait:

Event gate: the virtual environment activation script exists.

Viewer hold: pause 2 seconds on the activated environment.

### Install Arbiter suite

```yaml studio-directive
beat:
  id: install-suite
  heading: Install Arbiter suite
  narration: >-
    Install the `arbiter-suite` package set into the virtual environment. One
    of those packages is `arbiter-server`, which we use next to create the
    Docker staging directory. The suite also installs `arbiter`, the client we
    use later to test the staged server.
```

Install the Arbiter command line packages and verify the server command before
moving on to Docker staging.

Action:

```bash
arbiter_venv/bin/python -m pip install arbiter-suite
arbiter-server version
```

Wait:

Event gate: `arbiter-server version` exits successfully.

Viewer hold: pause 3 seconds on the installed CLI version.

### Bootstrap Docker Staging Directory

```yaml studio-directive
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

```yaml studio-directive
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

```yaml studio-directive
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

```yaml studio-directive
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

```yaml studio-directive
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

```yaml studio-directive
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

```yaml studio-directive
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

```yaml studio-directive
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
arbiter arbiter.url=https://127.0.0.1:18075 plugins | jq .
arbiter arbiter.url=https://127.0.0.1:18075 plugins smtp account bot | jq .
arbiter arbiter.url=https://127.0.0.1:18075 op list smtp | jq .
arbiter arbiter.url=https://127.0.0.1:18075 op desc smtp:send_email | jq '{id, description, input_schema}'
```

Wait:

Event gate: the client sees `imap`, `smtp`, the bot account, and
`smtp:send_email`.

Viewer hold: pause 5 seconds on the operation schema.

### Send a Test Message

```yaml studio-directive
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
arbiter arbiter.url=https://127.0.0.1:18075 op run smtp:send_email --args '{"account":"bot","to":["bot@example.test"],"subject":"Arbiter install smoke test","text_body":"Hello from Arbiter staging.","idempotency_key":"install-smoke-1"}' | jq .
```

Wait:

Event gate: the send operation exits successfully, returns a message id, and a
hidden readiness check confirms that the delivered message is visible through
IMAP before the fetch beat starts.

Viewer hold: pause 4 seconds on the send result.

### Fetch the Test Message

```yaml studio-directive
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
message_uid="$(arbiter arbiter.url=https://127.0.0.1:18075 op run imap:search_messages --args '{"account":"bot","folder":"INBOX","query":"Arbiter install smoke test","limit":1}' | jq -er '.result.messages[0].uid')"
printf 'message_uid=%s\n' "$message_uid" && \
arbiter arbiter.url=https://127.0.0.1:18075 op run imap:get_message --args "{\"account\":\"bot\",\"folder\":\"INBOX\",\"message_id\":\"$message_uid\"}" | jq '{subject: .result.message.subject, text_body: .result.message.text_body}'
```

Wait:

Event gate: search returns an IMAP UID and fetch returns the test message
subject and body.

Viewer hold: pause 5 seconds on the fetched message.

### Staging Versus Installed

```yaml studio-directive
beat:
  id: staging-vs-installed
  heading: Staging Versus Installed
  narration: >-
    Staging proves the bundle, configuration, mail access, and client operations
    before any privileged install step. It is still an operator workspace, not
    the long-running production service. The installed deployment uses protected
    ownership, systemd lifecycle management, and the production server port,
    8075.
```

Explain why passing staging is necessary but not the final deployment shape.

Action:

```bash
printf 'staging URL:   https://127.0.0.1:18075\n'
printf 'installed URL: https://127.0.0.1:8075\n'
```

Wait:

Viewer hold: pause 3 seconds on the port comparison.

### Preinstall Doctor

```yaml studio-directive
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

### Install Arbiter

```yaml studio-directive
beat:
  id: install-server
  heading: Install Arbiter
  narration: >-
    Finally, run the install command a production operator uses. It promotes
    the checked staging deployment to the protected target, writes the systemd
    unit, applies service ownership, and prepares Arbiter to run from
    /opt/arbiter.
```

Promote the checked staging deployment into the permanent install location.

Action:

```bash
# Defaults install to /opt/arbiter as user arbiter.
sudo ./arbiter-docker install
```

Wait:

Event gate: install exits successfully, reports `/opt/arbiter`, writes the
systemd unit, and enables the service.

Viewer hold: pause 5 seconds on the dry-run summary.

## Marker Plan

- `prepare-cli`: create and activate the local virtual environment
- `install-suite`: install the Arbiter suite and verify `arbiter-server`
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
- `install-server`: promote the staged deployment to `/opt/arbiter`

## Future Tracks

- Replace the non-interactive config patch with a real driven terminal editor
  once the recorder has editor-control primitives.
- If a future recording must show a completed host install, run it only inside
  a disposable VM or image where privileged operations cannot affect the
  operator host.
