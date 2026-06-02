# RFC: Arbiter Overview

- Document ID: `arbiter-overview`
- Version: `0.9.0.dev2`
- Status: `Draft`
- Authors: `Codex`, `Omry Yadan`
- Last Updated: `2026-05-24`
- Intended Use: implementation-driving overview for the Arbiter server

## Purpose

Define a Model Context Protocol server that gives an agent controlled access to email capabilities.

The current implementation supports outbound SMTP mail submission and a first IMAP tool family for explicitly configured accounts and folders.

The default deployment target is a bot or gateway account with deployment-owned credentials. A separate read-only IMAP deployment variant exists for testing a real inbox with stricter guardrails.

## Scope

The server is a single MCP service that exposes capability discovery and
operation execution over a shared configuration, policy, and transport layer.

The current MCP tool set is:

- `list_caps`
- `describe_caps`
- `describe_cap`
- `describe_op`
- `run_op`

Service operations are addressed as `capability:operation` ids, for example
`smtp:send_email`, `imap:list_messages`, and `imap:get_message`.

## Goals

- Expose email capabilities through a narrow MCP surface
- Support SMTP message submission
- Support IMAP folder/message operations only for configured accounts and folders
- Support multiple configured email accounts
- Keep transport configuration and credentials outside tool inputs
- Preserve strong safety boundaries for credentials, recipients, account scope, and access policy

## Non-goals for v1

- Full mail client behavior
- Bulk email or campaign workflows
- Attachment handling
- Per-call transport parameter overrides such as host, port, TLS mode, credentials, or sender identity
- Open-ended access to arbitrary IMAP folders outside the configured folder map
- Agent-facing skill integration

## Core design principles

### 1. Capability-first interface

The server exposes a small discovery surface first, then lets callers drill
down into message-level, account-level, and folder-level operations such as
`smtp:send_email`, `imap:list_messages`, and `imap:get_message`, while keeping
SMTP and IMAP session management internal.

### 2. Deployment-owned configuration

SMTP and IMAP settings belong to server configuration, not tool payloads. The caller does not choose hosts, ports, TLS modes, or credentials.

### 3. Configuration is the authority boundary

Arbiter enforces what the configured account and policy allow. Account
names and descriptions may guide voluntary caller behavior, but labels such as
`personal` are not built-in enforcement tiers.

Questions to resolve before broader or more sensitive deployments:

- whether the account's configured SMTP recipient policy is narrow enough
- whether the account's configured IMAP policy should start read-only
- whether destructive IMAP operations should be disabled
- whether the bot-to-Arbiter MCP connection needs caller authentication or
  authorization, such as a shared secret, bearer token, password, client
  certificate, or mTLS/PKI

### 4. Small, explicit surface area

Add capabilities incrementally. The implemented surface is still intentionally small: account discovery, one SMTP send operation, and folder-scoped IMAP message operations.

### 5. Observable behavior

Every tool call should produce structured operational logs and normalized results so automated actions can be inspected later. Durable audit storage and audit policy configuration are parked for post-v1.

## Terminology

- `account`: the credential and identity boundary used for SMTP submission and IMAP access
- `folder`: an IMAP folder within an account, such as `INBOX` or `Alerts`
- `policy`: the reusable service-scoped policy selected by a configured account

This document uses these terms deliberately:

- SMTP is tied to an `account`
- IMAP is tied to an `account`
- IMAP operations target a `folder`
- current access control comes from the account's configured service policy
- caller confirmation metadata comes from SMTP `require_confirmation` and IMAP `confirmation_required`
- multiple configured accounts may coexist in one server deployment

## Trust model

The current design assumes the caller is trusted once connected to the MCP server. The server does not define caller authentication yet.

Implications of the current trust model:

- `describe_caps` and `describe_cap` return configured account summaries
- callers may explicitly select any configured account
- caller authentication between the bot and the MCP server is out of scope for the current design
- Arbiter config is the enforcement boundary for v1

## Current Implementation Status

Implemented:

- shared configuration loading
- policy-based enforcement for SMTP recipient policy, `max_recipients_per_message`, and IMAP read/search/move/delete
- policy-based confirmation metadata through service-local SMTP and IMAP fields
- IMAP flag visibility and `seen` mutation policy
- capability discovery through `list_caps`, `describe_caps`, and `describe_cap`
- operation discovery through `describe_op`
- operation execution through `run_op`
- SMTP operation `smtp:send_email`
- IMAP operations `imap:list_messages`, `imap:get_message`,
  `imap:search_messages`, `imap:move_message`, `imap:mark_message_read`, and
  `imap:delete_message`

Still open:

- structured operational logging
- normalized error-code responses
- idempotency replay and conflict handling
- durable audit storage and audit policy configuration, after v1

## Open design decisions

- Whether to expose MCP resources in addition to tools
- Whether message drafts should exist as a separate future tool
- Whether attachments belong in v2 or later
- Whether service-scoped policies should remain the long-term home for caller
  confirmation metadata as well as runtime access policy
- What caller authentication or authorization model, if any, should protect
  the bot-to-Arbiter MCP connection

## Recommended next step

Prioritize the remaining v1 hardening work around the now-implemented policy contract: startup/runtime logging, normalized errors, and idempotency guardrails.
