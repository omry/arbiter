# Configuration

## Purpose

Define the deployment-owned configuration contract for the Mail Sentry server.

## Configuration system

The implementation language is Python so the server can use OmegaConf directly
for hierarchical configuration and environment-variable interpolation.

Examples below use OmegaConf interpolation. Secrets may be sourced from
environment variables via `oc.env`.

## Current status note

The current policy model uses `mail.account_access_profiles` as the shared
policy object attached to accounts through
`mail.accounts.<account>.account_access_profile`.

Two surrounding areas are still only partially implemented:

- SMTP idempotency config is reserved for future runtime work. The current
  server fails closed at startup if those unsupported fields are configured.
- Durable audit storage and audit policy configuration are parked for post-v1.
  V1 examples avoid audit knobs because the runtime does not honor them yet.

## Illustrative config shape

```yaml
mail:
  accounts:
    primary:
      description: Bot-owned account for automated email tasks.
      account_access_profile: bot
      smtp:
        host: smtp.example.com
        port: 587
        authenticate: true
        username: bot@example.com
        password: ${oc.env:SMTP_PASSWORD}
        tls: starttls
        verify_peer: true
        from_email: bot@example.com
        from_name: Bot
      imap:
        host: imap.example.com
        port: 993
        username: bot@example.com
        password: ${oc.env:IMAP_PASSWORD}
        tls: implicit
        verify_peer: true
        default_folder: INBOX
        folders:
          INBOX:
            description: Primary inbox folder.
          Alerts:
            description: Operational notifications.

    personal:
      description: Personal account with stricter send policy.
      account_access_profile: personal
      smtp:
        host: smtp.example.com
        port: 587
        authenticate: true
        username: personal@example.com
        password: ${oc.env:PERSONAL_SMTP_PASSWORD}
        tls: starttls
        verify_peer: true
        from_email: personal@example.com
        from_name: Personal

  account_access_profiles:
    bot:
      services:
        smtp:
          require_confirmation: false
          limits:
            max_messages_per_minute: 30
            max_recipients_per_message: 20
          recipient_policy:
            allowed_recipients:
              - ops@example.com
            blocked_recipients: []
            allowed_domain_patterns:
              - example.com
              - "*.example.org"
            blocked_domain_patterns: []
        imap:
          allow_read: true
          allow_search: true
          allow_move: true
          allow_delete: true
          confirmation_required: []
          system_flags:
            seen: read_write
            flagged: read_write
            answered: read_write
            deleted: read_write
            draft: read_write
          user_flags: {}

    personal:
      services:
        smtp:
          require_confirmation: true
          limits:
            max_messages_per_minute: 5
            max_recipients_per_message: 5
          recipient_policy:
            allowed_recipients:
              - alice@example.com
            blocked_recipients:
              - ceo@example.com
            allowed_domain_patterns:
              - example.com
            blocked_domain_patterns:
              - "*.external.example.net"
```

In this illustrative example, account transport config stays under `accounts`,
while shared policy lives under `account_access_profiles.<profile>.services`.

The access-profile schema is generic. Deployment config defines named profiles
such as `bot` and `personal`, and accounts may reuse those profiles across
multiple configured accounts.

## Policy model

- `account_access_profile` attaches a shared policy profile to an account
- `services.smtp` is constraint-oriented
- `services.imap` is capability-oriented
- service blocks are optional by omission at the profile level
- an account may only enable a protocol when the referenced profile also has a
  matching service-policy block
- unsupported SMTP idempotency config currently fails closed during startup
  validation instead of being silently ignored

### SMTP service policy

`services.smtp` answers "under what constraints may this account send mail?"

- `require_confirmation`: whether callers should require explicit confirmation
  before sending from accounts that use this profile
- `limits.max_messages_per_minute`: enforced as a per-account, per-process
  rolling 60-second submission cap
- `limits.max_recipients_per_message`: enforced per submission
- `idempotency.expiration_days`: reserved for future idempotency retention;
  startup rejects configs that customize it today
- `recipient_policy`: outbound recipient guardrails

Recipient-policy semantics:

- `allowed_recipients` and `blocked_recipients` are exact email addresses
- `allowed_domain_patterns` and `blocked_domain_patterns` match only the domain
  part of an address
- a domain pattern may be either:
  - an exact domain such as `example.com`
  - a leading-wildcard subdomain pattern such as `*.example.com`
- blocked rules win over allowed rules
- if any allow rule is configured, the default becomes deny-unless-allowed

### IMAP service policy

`services.imap` answers "which IMAP operations and flag mutations are allowed?"

- `allow_read`
- `allow_search`
- `allow_move`
- `allow_delete`
- `confirmation_required`: action list scoped to IMAP only
- `system_flags`
- `user_flags`

Current IMAP confirmation action vocabulary:

- `read`
- `search`
- `move`
- `mark_read`
- `delete`

Flag semantics:

- `system_flags` controls standard IMAP flags such as `seen` and `flagged`
- `user_flags` controls custom keywords such as `bot.followed_up`
- `hidden` means do not expose the flag in tool-visible responses and do not
  allow mutation
- `read_only` means expose the flag in tool-visible responses but do not allow
  mutation
- `read_write` means expose the flag and allow mutation
- unspecified `system_flags` default to `read_only`

## Relevant settings

This section uses three terms deliberately:

- `schema-required`: a valid runtime config must provide a value
- `schema-defaulted`: the dataclass schema supplies a default when omitted
- `deployment-required`: a real deployment normally needs an explicit value
  even though the schema has a placeholder default

Top-level mail settings:

- `mail.accounts`: schema-defaulted to one `primary` SMTP account, but
  deployment-required for real credentials and account purpose
- `mail.accounts.<account>.description`: schema-defaulted to `""`, but
  deployment-required for operator clarity
- `mail.accounts.<account>.account_access_profile`: schema-defaulted to `bot`;
  must reference a configured profile
- `mail.account_access_profiles`: schema-defaulted to one `bot` profile, but
  deployment-required for real access policy
- `mail.account_access_profiles.<profile>.services.smtp`: optional by omission;
  required when an account enables SMTP
- `mail.account_access_profiles.<profile>.services.imap`: optional by omission;
  required when an account enables IMAP

Relevant SMTP transport settings for an account with SMTP enabled:

- `mail.accounts.<account>.smtp.host`: schema-defaulted to `localhost`, but
  deployment-required for real SMTP submission
- `mail.accounts.<account>.smtp.port`: schema-defaulted to `587`
- `mail.accounts.<account>.smtp.authenticate`: schema-defaulted to `false`
- `mail.accounts.<account>.smtp.username`: optional unless `authenticate` is
  `true`
- `mail.accounts.<account>.smtp.password`: optional secret unless
  `authenticate` is `true`
- `mail.accounts.<account>.smtp.tls`: schema-defaulted to `starttls`; valid
  values: `none`, `starttls`, `implicit`
- `mail.accounts.<account>.smtp.verify_peer`: schema-defaulted to `true`
- `mail.accounts.<account>.smtp.from_email`: schema-defaulted to
  `agent@example.com`, but deployment-required for real SMTP submission
- `mail.accounts.<account>.smtp.from_name`: schema-defaulted to `Mail Sentry`
- `mail.accounts.<account>.smtp.timeout_seconds`: schema-defaulted to `30.0`

Relevant SMTP policy settings:

- `mail.account_access_profiles.<profile>.services.smtp.require_confirmation`:
  optional boolean
- `mail.account_access_profiles.<profile>.services.smtp.limits.max_messages_per_minute`:
  optional outbound rate limit; when set, the current server enforces it as a
  per-account, per-process rolling 60-second limit
- `mail.account_access_profiles.<profile>.services.smtp.limits.max_recipients_per_message`:
  optional per-message recipient cap
- `mail.account_access_profiles.<profile>.services.smtp.idempotency.expiration_days`:
  reserved for future idempotency retention; startup currently rejects non-default
  values
- `mail.account_access_profiles.<profile>.services.smtp.recipient_policy.allowed_recipients`:
  optional exact-address allowlist
- `mail.account_access_profiles.<profile>.services.smtp.recipient_policy.blocked_recipients`:
  optional exact-address denylist
- `mail.account_access_profiles.<profile>.services.smtp.recipient_policy.allowed_domain_patterns`:
  optional domain-pattern allowlist
- `mail.account_access_profiles.<profile>.services.smtp.recipient_policy.blocked_domain_patterns`:
  optional domain-pattern denylist

Relevant IMAP settings for an account with IMAP enabled:

- `mail.accounts.<account>.imap.host`: schema-defaulted to `localhost`, but
  deployment-required for real IMAP access
- `mail.accounts.<account>.imap.port`: schema-defaulted to `993`
- `mail.accounts.<account>.imap.username`: optional unless the IMAP server
  requires authentication
- `mail.accounts.<account>.imap.password`: optional secret unless username is
  set
- `mail.accounts.<account>.imap.tls`: schema-defaulted to `implicit`; valid
  values: `none`, `starttls`, `implicit`
- `mail.accounts.<account>.imap.verify_peer`: schema-defaulted to `true`
- `mail.accounts.<account>.imap.timeout_seconds`: schema-defaulted to `30.0`
- `mail.accounts.<account>.imap.default_folder`: optional folder name used when
  a tool does not specify one
- `mail.accounts.<account>.imap.folders`: schema-defaulted to `{}`, but
  deployment-required for useful IMAP tools because operations are limited to
  configured folders
- `mail.accounts.<account>.imap.folders.<folder>.description`: schema-defaulted
  to `""`

Relevant IMAP policy settings:

- `mail.account_access_profiles.<profile>.services.imap.allow_read`:
  schema-defaulted to `true`
- `mail.account_access_profiles.<profile>.services.imap.allow_search`:
  schema-defaulted to `true`
- `mail.account_access_profiles.<profile>.services.imap.allow_move`:
  schema-defaulted to `true`
- `mail.account_access_profiles.<profile>.services.imap.allow_delete`:
  schema-defaulted to `true`
- `mail.account_access_profiles.<profile>.services.imap.confirmation_required`:
  schema-defaulted to `[]`
- `mail.account_access_profiles.<profile>.services.imap.system_flags.<flag>`:
  schema-defaulted to `read_only`
- `mail.account_access_profiles.<profile>.services.imap.user_flags.<keyword>`:
  optional and hidden by omission

## Validation rules

- Each configured account may define `smtp`, `imap`, or both.
- Any account used for SMTP operations must define `smtp`.
- Any account used for IMAP operations must define `imap`.
- `mail.accounts.<account>.account_access_profile` must match a key under
  `mail.account_access_profiles`.
- If an account enables `smtp`, its profile must define `services.smtp`.
- If an account enables `imap`, its profile must define `services.imap`.
- If `mail.accounts.<account>.smtp.authenticate` is `true`, both
  `mail.accounts.<account>.smtp.username` and
  `mail.accounts.<account>.smtp.password` must be set.
- If `mail.accounts.<account>.smtp.authenticate` is `false`, both
  `mail.accounts.<account>.smtp.username` and
  `mail.accounts.<account>.smtp.password` must be unset.
- If `mail.accounts.<account>.smtp.tls` is configured, failure to establish the
  configured TLS mode must fail closed.
- The `From` identity is server-owned and not caller-controlled in v1.
- `Reply-To` is omitted or set to the same sender identity in v1.
- If `mail.accounts.<account>.imap.default_folder` is set, it must match a key
  under `mail.accounts.<account>.imap.folders`.
- `mail.account_access_profiles.<profile>.services.smtp.recipient_policy.*_recipients`
  must contain valid email addresses.
- `mail.account_access_profiles.<profile>.services.smtp.recipient_policy.*_domain_patterns`
  must contain exact domains such as `example.com` or leading-wildcard patterns
  such as `*.example.com`.
- `mail.account_access_profiles.<profile>.services.imap.allow_search` requires
  `mail.account_access_profiles.<profile>.services.imap.allow_read = true`.
- `mail.account_access_profiles.<profile>.services.imap.allow_move` requires
  `mail.account_access_profiles.<profile>.services.imap.allow_read = true`.
- `mail.account_access_profiles.<profile>.services.imap.allow_delete` requires
  `mail.account_access_profiles.<profile>.services.imap.allow_read = true`.
- Every
  `mail.account_access_profiles.<profile>.services.imap.confirmation_required`
  entry must be one of: `read`, `search`, `move`, `mark_read`, `delete`.
- `confirmation_required: [mark_read]` requires
  `services.imap.allow_read = true` and
  `services.imap.system_flags.seen = read_write`.
- `mail.account_access_profiles.<profile>.services.imap.system_flags.<flag>`
  must be one of `hidden`, `read_only`, `read_write`.
- `mail.account_access_profiles.<profile>.services.imap.user_flags.<keyword>`
  must be one of `hidden`, `read_only`, `read_write`.

## Secret handling

- Secrets should not be committed to source control.
- SMTP and IMAP passwords may be sourced through OmegaConf environment
  interpolation such as `${oc.env:SMTP_PASSWORD}`.
- Prefer secret references such as `${oc.env:SMTP_PASSWORD}` over raw secret
  values in checked-in configs, but hard-coded values remain supported.

## Configuration evolution notes

- The config shape includes both SMTP and IMAP, and the current server
  implements both protocol families.
- `account_access_profile` is the active shared policy model for service-level
  access and confirmation settings.
- Durable audit storage and audit configuration are post-v1 work and are not
  part of the v1 config schema.
- Folder-specific policy overrides are intentionally out of scope for the
  default shape.
- SMTP recipient policy and `max_recipients_per_message` are enforced.
- SMTP rate limiting, recipient policy, and `max_recipients_per_message` are
  enforced.
- SMTP idempotency retention is represented in config but still needs runtime
  enforcement beyond validation.
