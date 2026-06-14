---
title: Plugins
---

Plugins are Arbiter's service adapters. A plugin connects Arbiter to one
service area, such as SMTP or IMAP, and defines the capability and operations
that agents can discover and use.

Installing a plugin makes its capability available to the server. Configuration
still decides which accounts and policies are active, so installing a plugin
does not by itself grant an agent access to an upstream service.

## What Plugins Provide

A service plugin is responsible for the service-specific parts of Arbiter:

- account and policy config schemas
- bootstrap examples for new accounts and policies
- operation names, descriptions, and input schemas
- runtime behavior for calling the upstream service
- service-specific policy checks
- management of service-specific writable state, when needed

For example, the SMTP plugin owns `smtp:send_email` and the policy controls that
decide whether a message can be sent. The IMAP plugin owns folder and message
operations, plus the policy controls that decide which folders and message
actions are allowed.

## What Operators Configure

Operators decide how installed plugins are exposed by configuring:

- accounts: connection settings, credential references, display metadata, and
  the policy selected for that account
- policies: service-specific guardrails for one or more accounts
- activation: which account and policy files are included in the composed
  server config

Bootstrap creates a matching policy for each new account, but accounts refer to
policies by name. Multiple accounts can share the same policy when that matches
the deployment model.

## From Plugin To Operation

At startup, Arbiter discovers installed service plugins, registers their config
schemas, composes the active deployment config, validates it, and then exposes
operations for configured accounts.

Agents see the resulting capability and operation surface. They do not provide
transport settings, credentials, folder access, recipient policy, or other
deployment-owned controls in operation arguments.

For the file layout and activation model, see
[Configuration Model](../operate/configuration-model.md). For plugin authoring,
see [Writing Plugins](../extend/plugins.md).
