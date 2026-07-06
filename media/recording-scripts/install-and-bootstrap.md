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
    reploy_app: "reploy app"
  id: install-and-bootstrap
  title: Install Arbiter Server
  capture:
    window_size: 100x28
    headless: true
    baseline_compressed: true
  failure_summary:
    terminal_animations:
    - regex: '^(?P<prefix>\[STAGING : arbiter\] building installation bundle) [|/\\-]$'
      replacement: "{prefix}..."
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
    minimum_section_spacing: 1.0
  environment:
    working_directory: .
    variables:
      ARBITER_COLOR: always
      ARBITER_CINEMA_STAGING_SUBNET: 10.213.240.0/24
      ARBITER_CINEMA_STAGING_URL: ${recording.vars.staging_url}
      ARBITER_CINEMA_INSTALLED_URL: ${recording.vars.installed_url}
      REPLOY_COLOR: always
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
      default: local
    arbiter_package:
      default: arbiter-suite
    reploy_source:
      default: local
    reploy_venv:
      default: ../reploy/.venv
    operator_venv_cache_retain:
      default: 8
  setup:
  - name: Prepare operator commands and local mail lab
    expect:
      file_exists:
      - $MAIL_LAB_ENV_FILE
    run_file: media/recording-scripts/install-and-bootstrap/setup-main.sh
  cleanup:
  - name: Stop Docker staging deployment and clean generated Reploy artifacts
    run_file: media/recording-scripts/install-and-bootstrap/cleanup-main.sh
```


Purpose: show a new operator how to install Arbiter with Reploy, configure one
local bot mail account, prove the staged server works, and install the checked
staging instance into a permanent host location.

Audience: an operator preparing an Arbiter server for the first time.

Target length: 3 to 4 minutes.

Maintenance note: review this script whenever release media is refreshed. If
Reploy commands, default ports, generated files, install behavior, account
templates, or the first-run flow changes, update the script before regenerating
casts, narration, captions, or static renders.

Recording notes:

- Keep the visible operator flow focused on Reploy and Arbiter commands. Any
  helper virtual environment or local package build is recorder setup, not part
  of the tutorial.
- The recording uses a disposable `reploy-staging/` directory, a local SMTP/IMAP
  mail lab, generated test credentials, staging port `18075`, and installed
  service port `8075`.
- Do not use real mail credentials. The local mail lab and Docker staging
  deployment are cleaned up by hidden setup/cleanup directives.

## Script

### Overview

Set the tutorial frame before terminal commands begin.

```yaml studio-directive
beat:
  id: overview
  heading: Overview
  narration: >-
    In this tutorial, we will install Arbiter using the standard workflow: stage it locally with Reploy, configure and test it, then install it as a permanent Docker service on the system.
  viewer_hold: 1.0
```

### Install Reploy

Install only the deployment tool first, before any Arbiter-specific workflow.

```yaml studio-directive
beat:
  id: install-reploy
  heading: Install Reploy
  narration: >-
    First, @install@ install Reploy.
  marker: install-reploy
  caption: Install Reploy with the script installer.
  actions:
  - commands:
    - run: recording_install_reploy
      after: "@install@"
      display: "curl -fsSL https://reploy.yadan.net/install.sh | sh"
      follow_along: true
      output: suppress
  viewer_hold: 1.0
  guide:
    commands:
    - "curl -fsSL https://reploy.yadan.net/install.sh | sh"
    success_hint: The installer should report that Reploy is ready.
```

### Bootstrap Docker Staging Directory

Create the operator-owned staging workspace where the deployment is prepared
and tested.

```yaml studio-directive
beat:
  id: init-staging
  heading: Bootstrap Docker Staging Directory
  narration: >-
    Use Reploy to @create@ create the staging directory. @enter@ Then
    enter it; the rest of the setup runs from inside that staged deployment.
  marker: init-staging
  caption: Create a Docker staging deployment.
  actions:
  - commands:
    - run: |
        if [[ "$arbiter_source" == local ]]; then
          reploy stage "file:$recording_repo/server/src/arbiter_server/reploy/arbiter.blueprint.yaml"
        else
          reploy stage arbiter-server
        fi
      display: reploy stage arbiter-server
      after: "@create@"
      post_enter_pause: 1.5
    - run: cd reploy-staging
      after: "@enter@"
    expect:
      file_exists:
      - ./arbiterctl
      - ./.reploy/runtime/compose.yaml
      - ./.reploy/docker.env
  viewer_hold: 1.0
  guide:
    commands:
    - reploy stage arbiter-server
    - cd reploy-staging
    - reploy bundle list
    success_hint: You should enter the staging directory and see the selected runtime bundle.
```

### Select Mail Bundle

Choose the IMAP and SMTP runtime pieces used by the mail demo.

```yaml studio-directive
beat:
  id: prepare-bundle
  heading: Select Mail Bundle
  narration: >-
    Arbiter has a pluggable architecture. For this demo, we add the IMAP and
    SMTP plugins. @list@ First, list the available bundle options.
    @wait:list-options@ @add@ Then add the mail plugins. @build@ Now build the
    bundle. Reploy builds a local bundle from the selected dependencies, and
    that bundle becomes part of the staged app. Installation will use the tested
    artifact instead of resolving packages again later. Reploy rebuilds the
    bundle automatically when dependencies change; running the build here pays
    that cost now instead of during the next app command.
  marker: prepare-bundle
  caption: Select the mail plugin bundle options.
  actions:
  - commands:
    - id: list-options
      run: reploy bundle list-options
      after: "@list@"
    - run: reploy bundle add --name imap,smtp
      after: "@add@"
      post_enter_pause: 0.8
    - run: reploy bundle build
      after: "@build@"
      retime: realtime
      post_command_pause: 0.0
    - run: clear
      pre_command_pause: 2.0
      show_prompt_after: false
    expect:
      file_exists:
      - ./.reploy/requirements.txt
  viewer_hold: 1.0
  guide:
    commands:
    - reploy bundle list-options
    - reploy bundle add --name imap,smtp
    - reploy bundle build
    - reploy bundle list
    success_hint: You should still be inside reploy-staging and see the selected
      bundle roots.
```

### Bootstrap Arbiter Config

Generate the initial Hydra/OmegaConf config tree that the operator will edit.

```yaml studio-directive
beat:
  id: bootstrap-config
  heading: Bootstrap Arbiter Config
  narration: >-
    Arbiter uses Hydra to compose one validated runtime config from YAML files
    in the conf directory. In this section, we will generate starter config,
    edit it for the local mail lab, and verify that Arbiter can load it.
    @server@ Start with the server config. @smtp_account@ Add the SMTP account
    and policy. @smtp_edit@ The generated files are meant to be edited, so point
    them at the local SMTP server. @smtp_activate@ Activate the account.
    @show_config@ Now inspect the composed config, which is what Arbiter will
    actually run with. @smtp_env@ Create the env file for secrets. @env_show@
    It starts with empty variables. @smtp_secret@ Fill in the SMTP credentials
    from the local mail lab. @imap_account@ Add the matching IMAP account.
    @imap_edit@ Edit it for the same local mail lab. @imap_activate@ Activate
    IMAP. @imap_env@ Bootstrap the env file again; this adds the new IMAP
    variables without losing the SMTP values. @imap_secret@ Fill in the IMAP
    credentials. @check@ The normal config check validates composition and
    schemas. @live_check@ The live check goes further and logs into the test
    IMAP and SMTP servers.
  marker: bootstrap-config
  caption: Bootstrap editable Arbiter config files.
  actions:
  - commands:
    - id: bootstrap-server
      run: reploy app bootstrap server
      after: "@server@"
    - run: reploy app bootstrap --plugin smtp --account mail-demo
      after: "@smtp_account@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins smtp
      display: "$EDITOR conf/arbiter/account/smtp/mail-demo.yaml conf/arbiter/policy/smtp/mail-demo_policy.yaml"
      after: "@smtp_edit@"
    - run: reploy app config activate --plugin smtp --account mail-demo
      after: "@smtp_activate@"
    - run: reploy app config show
      after: "@show_config@"
    - run: reploy app env bootstrap
      after: "@smtp_env@"
    - run: sed -n '1,80p' .arbiter.env
      after: "@env_show@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins smtp --update-env
      display: "$EDITOR .arbiter.env"
      after: "@smtp_secret@"
    - run: reploy app bootstrap --plugin imap --account mail-demo --variant default-open
      after: "@imap_account@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins imap
      display: "$EDITOR conf/arbiter/account/imap/mail-demo.yaml conf/arbiter/policy/imap/mail-demo_policy.yaml"
      after: "@imap_edit@"
    - run: reploy app config activate --plugin imap --account mail-demo
      after: "@imap_activate@"
    - run: reploy app env bootstrap
      after: "@imap_env@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins imap --update-env
      display: "$EDITOR .arbiter.env"
      after: "@imap_secret@"
    - run: reploy app config check
      after: "@check@"
    - run: recording_live_config_check
      display: arbiter-server --config-dir conf --config-name arbiter-server config check --live
      after: "@live_check@"
      retime: realtime
      post_command_pause: 0.0
    expect:
      file_exists:
      - ./conf/arbiter-server.yaml
      - ./conf/plugins.yaml
      - ./.arbiter.env
      - ./conf/arbiter/account/imap/mail-demo.yaml
      - ./conf/arbiter/policy/imap/mail-demo_policy.yaml
      - ./conf/arbiter/account/smtp/mail-demo.yaml
      - ./conf/arbiter/policy/smtp/mail-demo_policy.yaml
  viewer_hold: 1.0
  guide:
    commands:
    - reploy app bootstrap server
    - reploy app bootstrap --plugin smtp --account mail-demo
    - "$EDITOR conf/arbiter/account/smtp/mail-demo.yaml conf/arbiter/policy/smtp/mail-demo_policy.yaml"
    - reploy app config activate --plugin smtp --account mail-demo
    - reploy app config show
    - reploy app env bootstrap
    - "$EDITOR .arbiter.env"
    - reploy app bootstrap --plugin imap --account mail-demo --variant default-open
    - "$EDITOR conf/arbiter/account/imap/mail-demo.yaml conf/arbiter/policy/imap/mail-demo_policy.yaml"
    - reploy app config activate --plugin imap --account mail-demo
    - reploy app env bootstrap
    - "$EDITOR .arbiter.env"
    - reploy app config check
    - arbiter-server --config-dir conf --config-name arbiter-server config check --live
    success_hint: The normal and live config checks should both pass.
```
