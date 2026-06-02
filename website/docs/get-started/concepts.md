---
title: Core Concepts
---

Arbiter uses a few terms deliberately.

## Capability

A capability is a service area exposed by a plugin, such as `smtp` or `imap`.
Agents discover capabilities first with `list_caps` or `describe_caps`.

## Operation

An operation is an action within a capability. Operation ids use
`capability:operation` syntax:

- `smtp:send_email`
- `imap:list_messages`
- `imap:get_message`

Agents can inspect operation input schemas before running them.

## Account

An account is the credential and identity boundary for a service. SMTP and IMAP
can both have an account named `primary`, but those names are related only if the
operator chooses to configure them that way.

## Policy

A policy is reusable service-specific guardrail config. An account references a
policy in the same service namespace:

```yaml
arbiter:
  account:
    smtp:
      bot:
        policy: bot_policy
  policy:
    smtp:
      bot_policy:
        require_confirmation: false
```

## Config authority

Tool payloads do not carry transport settings, credentials, TLS options, sender
identity, or arbitrary folder access. Those belong to deployment-owned config.
