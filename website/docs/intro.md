---
id: intro
title: What Is Arbiter?
slug: /
---

[![PyPI](https://img.shields.io/pypi/v/arbiter-server.svg?label=arbiter-server)](https://pypi.org/project/arbiter-server/) [![Python](https://img.shields.io/pypi/pyversions/arbiter-server.svg?label=python)](https://pypi.org/project/arbiter-server/) [![Downloads](https://pepy.tech/badge/arbiter-server/month)](https://pepy.tech/project/arbiter-server)

Arbiter is a policy firewall for AI agents that need access to sensitive
services. It runs in your trusted environment, keeps upstream account
credentials inside the deployment, and exposes only the actions an operator has
configured and approved.

Arbiter is built as a small module system with one plugin per service. The SMTP
and IMAP plugins can restrict who an agent may email, which messages it may read
from configured folders, and other service-specific behavior. Agents get a
narrow, discoverable surface through the `arbiter` CLI instead of raw service
access.

## The shape

```mermaid
%%{init: {"theme": "base", "themeVariables": {"background": "transparent", "primaryTextColor": "#f8fafc", "lineColor": "#cbd5e1", "edgeLabelBackground": "#243142", "fontFamily": "Inter, ui-sans-serif, system-ui"}}}%%
flowchart LR
  subgraph sandbox["Sandbox (Recommended)"]
    direction LR
    agent[Agent]
    access["Arbiter CLI"]

    agent -- Invokes --> access
  end

  subgraph arbiterRuntime[Arbiter]
    direction TB

    subgraph arbiterContent[" "]
      direction TB
      arbiter[Server]

      subgraph imapPlugin[IMAP Plugin]
        direction TB
        imapIncoming[Incoming Request]
        imapPolicy[Policy Check]
        imapDeny[Deny]
        imapForward[Native IMAP Call<br/>With Credentials]

        imapIncoming --> imapPolicy
        imapPolicy -- Deny --> imapDeny
        imapPolicy -- Allow --> imapForward
      end

      subgraph smtpPlugin[SMTP Plugin]
        direction TB
        smtpIncoming[Incoming Request]
        smtpPolicy[Policy Check]
        smtpDeny[Deny]
        smtpForward[Native SMTP Call<br/>With Credentials]

        smtpIncoming --> smtpPolicy
        smtpPolicy -- Deny --> smtpDeny
        smtpPolicy -- Allow --> smtpForward
      end
    end
  end

  imapServer[IMAP Server]
  smtpServer[SMTP Server]

  access -- Operation request --> arbiter
  arbiter -- Dispatches Operation --> imapIncoming
  arbiter -- Dispatches Operation --> smtpIncoming
  imapForward --> imapServer
  smtpForward --> smtpServer

  classDef accessNode fill:#1f4e5f,stroke:#67b7c7,color:#f0fdfa,stroke-width:2px
  classDef coreNode fill:#234b37,stroke:#76b391,color:#ecfdf5,stroke-width:2px
  classDef requestNode fill:#273f73,stroke:#7ea3d8,color:#eff6ff,stroke-width:2px
  classDef policyNode fill:#604719,stroke:#d7ae5f,color:#fffbeb,stroke-width:2px
  classDef denyNode fill:#6b2a2a,stroke:#d48989,color:#fef2f2,stroke-width:2px
  classDef forwardNode fill:#44306f,stroke:#aa8fd8,color:#faf5ff,stroke-width:2px
  classDef serviceNode fill:#3f4b5a,stroke:#98a4b3,color:#f8fafc,stroke-width:2px

  class agent,access accessNode
  class arbiter coreNode
  class imapIncoming,smtpIncoming requestNode
  class imapPolicy,smtpPolicy policyNode
  class imapDeny,smtpDeny denyNode
  class imapForward,smtpForward forwardNode
  class imapServer,smtpServer serviceNode

  style sandbox fill:#10252c,stroke:#8fb8c5,stroke-width:2px,stroke-dasharray: 6 4,color:#ecfeff
  style arbiterRuntime fill:#1d3529,stroke:#6ca783,stroke-width:2px,color:#ecfdf5
  style arbiterContent fill:transparent,stroke:transparent,color:transparent
  style imapPlugin fill:#342b45,stroke:#8c7ab8,stroke-width:2px,color:#f7f3ff
  style smtpPlugin fill:#342b45,stroke:#8c7ab8,stroke-width:2px,color:#f7f3ff
```

- The server composes config, loads plugins, exposes the Arbiter client
  interface, and enforces the shared discovery flow.
- Operators configure accounts, credentials, service activation, and policies.
- Agents discover capabilities before selecting operations.
- Service plugins own their schemas, bootstrap templates, policy checks, and
  runtime behavior.

## Current capabilities

- SMTP service plugin.
- IMAP service plugin.

## Where to start

- New operator: start with [Quickstart](get-started/quickstart.md).
- Docker deployment operator: install Reploy and follow
  [Docker Deployment](operate/deployment.md).
- Agent/tool user: start with [Arbiter CLI Reference](reference/client.md).
- Plugin author: start with [Writing Plugins](extend/plugins.md).
