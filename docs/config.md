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

- SMTP rate limiting and idempotency config are reserved for future runtime
  work. The current server fails closed at startup if those unsupported fields
  are configured.
- Durable audit storage is still a design contract even though audit settings
  are already represented in the profile config.

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
            max_recipients_per_message: 20
          recipient_policy:
            allowed_recipients:
              - ops@example.com
            blocked_recipients: []
            allowed_domain_patterns:
              - example.com
              - "*.example.org"
            blocked_domain_patterns: []
          audit:
            enabled: true
            retention_days: 365
            store_message_metadata: true
            store_message_body: false
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
          audit:
            enabled: true
            retention_days: 365
            store_message_metadata: true
            store_message_body: false
            audit_read_access: false
            audit_search_queries: false
            audit_message_state_changes: true
            audit_message_moves: true
            audit_message_deletes: true

    personal:
      services:
        smtp:
          require_confirmation: true
          limits:
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
          audit:
            enabled: true
            retention_days: 365
            store_message_metadata: true
            store_message_body: false
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
- unsupported SMTP config such as rate limiting or idempotency currently fails
  closed during startup validation instead of being silently ignored

### SMTP service policy

`services.smtp` answers "under what constraints may this account send mail?"

- `require_confirmation`: whether callers should require explicit confirmation
  before sending from accounts that use this profile
- `limits.max_messages_per_minute`: reserved for future rate limiting; startup
  rejects configs that set it today
- `limits.max_recipients_per_message`: enforced per submission
- `idempotency.expiration_days`: reserved for future idempotency retention;
  startup rejects configs that customize it today
- `recipient_policy`: outbound recipient guardrails
- `audit`: SMTP audit settings

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
- `audit`

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

- `mail.accounts`: required mapping of configured accounts
- `mail.accounts.<account>.description`: required human-readable account purpose
- `mail.accounts.<account>.account_access_profile`: required reference to a
  profile under `mail.account_access_profiles`
- `mail.account_access_profiles`: required mapping of shared account access
  profile definitions
- `mail.account_access_profiles.<profile>.services.smtp`: optional SMTP service
  policy
- `mail.account_access_profiles.<profile>.services.imap`: optional IMAP service
  policy

Relevant SMTP transport settings for an account with SMTP enabled:

- `mail.accounts.<account>.smtp.host`: required
- `mail.accounts.<account>.smtp.port`: required
- `mail.accounts.<account>.smtp.authenticate`: required
- `mail.accounts.<account>.smtp.username`: optional
- `mail.accounts.<account>.smtp.password`: optional secret
- `mail.accounts.<account>.smtp.tls`: required; valid values: `none`,
  `starttls`, `implicit`
- `mail.accounts.<account>.smtp.verify_peer`: required when TLS is enabled
- `mail.accounts.<account>.smtp.from_email`: required
- `mail.accounts.<account>.smtp.from_name`: optional

Relevant SMTP policy settings:

- `mail.account_access_profiles.<profile>.services.smtp.require_confirmation`:
  optional boolean
- `mail.account_access_profiles.<profile>.services.smtp.limits.max_messages_per_minute`:
  reserved for future outbound rate limiting; startup currently rejects it
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
- `mail.account_access_profiles.<profile>.services.smtp.audit`: SMTP audit config

Relevant IMAP settings for an account with IMAP enabled:

- `mail.accounts.<account>.imap.host`: required
- `mail.accounts.<account>.imap.port`: required
- `mail.accounts.<account>.imap.username`: optional
- `mail.accounts.<account>.imap.password`: optional secret
- `mail.accounts.<account>.imap.tls`: required; valid values: `none`,
  `starttls`, `implicit`
- `mail.accounts.<account>.imap.verify_peer`: required when TLS is enabled
- `mail.accounts.<account>.imap.default_folder`: optional folder name used when
  a tool does not specify one
- `mail.accounts.<account>.imap.folders`: required mapping keyed by folder name
- `mail.accounts.<account>.imap.folders.<folder>.description`: optional
  human-readable folder purpose

Relevant IMAP policy settings:

- `mail.account_access_profiles.<profile>.services.imap.allow_read`
- `mail.account_access_profiles.<profile>.services.imap.allow_search`
- `mail.account_access_profiles.<profile>.services.imap.allow_move`
- `mail.account_access_profiles.<profile>.services.imap.allow_delete`
- `mail.account_access_profiles.<profile>.services.imap.confirmation_required`
- `mail.account_access_profiles.<profile>.services.imap.system_flags.<flag>`
- `mail.account_access_profiles.<profile>.services.imap.user_flags.<keyword>`
- `mail.account_access_profiles.<profile>.services.imap.audit`

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
  access, confirmation, and audit settings.
- Folder-specific policy and audit overrides are intentionally out of scope for
  the default shape.
- SMTP recipient policy and `max_recipients_per_message` are enforced.
- SMTP rate limiting and idempotency retention are represented in config but
  still need runtime enforcement beyond validation.
