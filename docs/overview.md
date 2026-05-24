# RFC: Mail Sentry Overview

- Document ID: `mail-sentry-overview`
- Version: `0.1.1.dev1`
- Status: `Draft`
- Authors: `Codex`, `Omry Yadan`
- Last Updated: `2026-03-07`
- Intended Use: implementation-driving overview for the Mail Sentry server

## Purpose

Define a Model Context Protocol server that gives an agent controlled access to email capabilities.

The first implementation focuses on outbound SMTP mail submission. The design also defines a future IMAP-based inbox capability that will be implemented in a second stage.

The initial deployment target is a bot operating on its own private email account. The design keeps a path open for a later deployment against a personal account with stricter guardrails.

## Scope

The server is a single MCP service that exposes multiple tools over a shared configuration, policy, and transport layer.

The initial tool set is:

- `list_accounts`
- `send_email`

The next major stage is IMAP support for reading and manipulating folders and messages on explicitly selected configured accounts.

## Goals

- Expose email capabilities through a narrow MCP surface
- Start with reliable SMTP message submission
- Support multiple configured email accounts
- Keep transport configuration and credentials outside tool inputs
- Make it easy to add IMAP read and folder actions later
- Preserve strong safety boundaries for credentials, recipients, account scope, and access policy

## Non-goals for v1

- Full mail client behavior
- Bulk email or campaign workflows
- Attachment handling
- Inbox search, read, delete, or move operations
- Per-call transport parameter overrides such as host, port, TLS mode, credentials, or sender identity

## Core design principles

### 1. Capability-first interface

The server exposes message-level, account-level, and later folder-level operations such as `send_email`, `list_messages`, and `get_message`, while keeping SMTP and IMAP session management internal.

### 2. Deployment-owned configuration

SMTP and future IMAP settings belong to server configuration, not tool payloads. The caller does not choose hosts, ports, TLS modes, or credentials.

### 3. Future personal inbox guardrails

The initial target is a private account used by the bot itself, but future access to a personal inbox needs stricter guardrails.

Questions to resolve before supporting a personal inbox:

- whether sending should be restricted to approved recipients or known correspondents
- whether first-contact messages should require a separate approval step
- whether inbox access should start as read-only before any write or delete operations are allowed
- what audit trail is required for message access, message sending, and destructive folder actions

### 4. Small, explicit surface area

Add capabilities incrementally. The initial implementation should expose a minimal tool set: account discovery plus a single mail submission tool. IMAP is designed in this document set, but implementation belongs to a second stage rather than a partial v1 rollout.

### 5. Auditable behavior

Every tool call should produce structured logs and normalized results so automated actions can be inspected later. Durable audit behavior is defined separately for SMTP and IMAP and is driven by the selected account access profile.

## Terminology

- `account`: the credential and identity boundary used for SMTP submission and IMAP access
- `folder`: an IMAP folder within an account, such as `INBOX` or `Alerts`
- `account_access_profile`: a policy profile applied at the account level; the initial profile types are `bot` and `personal`

This document uses these terms deliberately:

- SMTP is tied to an `account`
- IMAP is tied to an `account`
- IMAP operations target a `folder`
- sensitive behavior is controlled by the account's configured `account_access_profile`
- multiple configured accounts may coexist in one server deployment

## Trust model

The current design assumes the caller is trusted once connected to the MCP server. The server does not define caller authentication yet.

Implications of the current trust model:

- `list_accounts` returns all configured accounts
- callers may explicitly select any configured account
- caller authentication between the bot and the MCP server is out of scope for the current design

## Rollout stages

### Stage 1

Implement:

- shared configuration loading
- policy enforcement
- logging and audit handling
- `list_accounts`
- `send_email`

### Stage 2

Implement IMAP on top of the same service, config, and policy model.
Begin this stage only after the SMTP send flow is stable.

Planned IMAP capabilities:

- `list_messages`
- `get_message`
- `search_messages`
- `move_message`
- `mark_message_read`
- `delete_message`

## Open design decisions

- Whether to expose MCP resources in addition to tools
- Whether message drafts should exist as a separate future tool
- Whether attachments belong in v2 or later
- What approval hook is required before supporting a personal inbox

## Recommended next step

After this design is accepted, create a minimal MCP server skeleton with these initial tools:

- `list_accounts`
- `send_email`

That first implementation should wire together:

- config loading
- account discovery from config
- schema validation
- MIME message assembly
- SMTP submission
- normalized error handling
