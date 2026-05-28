# RFC: Agent Arbiter Overview

- Document ID: `agent-arbiter-overview`
- Version: `0.1.1.dev1`
- Status: `Draft`
- Authors: `Codex`, `Omry Yadan`
- Last Updated: `2026-05-24`
- Intended Use: implementation-driving overview for the Agent Arbiter server

## Purpose

Define a Model Context Protocol server that gives an agent controlled access to email capabilities.

The current implementation supports outbound SMTP mail submission and a first IMAP tool family for explicitly configured accounts and folders.

The default deployment target is a bot or gateway account with deployment-owned credentials. A separate read-only IMAP deployment variant exists for testing a real inbox with stricter guardrails.

## Scope

The server is a single MCP service that exposes multiple tools over a shared configuration, policy, and transport layer.

The current tool set is:

- `list_accounts`
- `send_email`
- `list_messages`
- `get_message`
- `search_messages`
- `move_message`
- `mark_message_read`
- `delete_message`

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
- Native OpenClaw MCP integration

## Core design principles

### 1. Capability-first interface

The server exposes message-level, account-level, and folder-level operations such as `send_email`, `list_messages`, and `get_message`, while keeping SMTP and IMAP session management internal.

### 2. Deployment-owned configuration

SMTP and IMAP settings belong to server configuration, not tool payloads. The caller does not choose hosts, ports, TLS modes, or credentials.

### 3. Configuration is the authority boundary

Agent Arbiter enforces what the configured account and policy allow. Account
names and descriptions may guide voluntary caller behavior, but labels such as
`personal` are not built-in enforcement tiers.

Questions to resolve before broader or more sensitive deployments:

- whether the account's configured SMTP recipient policy is narrow enough
- whether the account's configured IMAP policy should start read-only
- whether destructive IMAP operations should be disabled
- whether the bot-to-Agent Arbiter MCP connection needs caller authentication or
  authorization, such as a shared secret, bearer token, password, client
  certificate, or mTLS/PKI

### 4. Small, explicit surface area

Add capabilities incrementally. The implemented surface is still intentionally small: account discovery, one SMTP send operation, and folder-scoped IMAP message operations.

### 5. Observable behavior

Every tool call should produce structured operational logs and normalized results so automated actions can be inspected later. Durable audit storage and audit policy configuration are parked for post-v1.

## Terminology

- `account`: the credential and identity boundary used for SMTP submission and IMAP access
- `folder`: an IMAP folder within an account, such as `INBOX` or `Alerts`
- `account_access_profile`: the shared policy profile attached to an account and used for per-service SMTP and IMAP policy

This document uses these terms deliberately:

- SMTP is tied to an `account`
- IMAP is tied to an `account`
- IMAP operations target a `folder`
- current access control still comes from the account's configured `account_access_profile`
- caller confirmation metadata comes from SMTP `require_confirmation` and IMAP `confirmation_required`
- multiple configured accounts may coexist in one server deployment

## Trust model

The current design assumes the caller is trusted once connected to the MCP server. The server does not define caller authentication yet.

Implications of the current trust model:

- `list_accounts` returns all configured accounts
- callers may explicitly select any configured account
- caller authentication between the bot and the MCP server is out of scope for the current design
- Agent Arbiter config is the enforcement boundary for v1

## Current Implementation Status

Implemented:

- shared configuration loading
- profile-based enforcement for SMTP recipient policy, `max_recipients_per_message`, and IMAP read/search/move/delete
- profile-based confirmation metadata through service-local SMTP and IMAP fields
- IMAP flag visibility and `seen` mutation policy
- `list_accounts`
- `send_email`
- `list_messages`
- `get_message`
- `search_messages`
- `move_message`
- `mark_message_read`
- `delete_message`

Still open:

- structured operational logging
- normalized error-code responses
- idempotency replay and conflict handling
- durable audit storage and audit policy configuration, after v1

## Open design decisions

- Whether to expose MCP resources in addition to tools
- Whether message drafts should exist as a separate future tool
- Whether attachments belong in v2 or later
- Whether account access profiles should continue to own confirmation policy as well as access policy
- What caller authentication or authorization model, if any, should protect
  the bot-to-Agent Arbiter MCP connection

## Recommended next step

Prioritize the remaining v1 hardening work around the now-implemented policy contract: startup/runtime logging, normalized errors, and idempotency guardrails.
