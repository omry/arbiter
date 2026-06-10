# Arbiter Client CLI Menus

This document describes the current client command menus from the top down. It
is a design map, not a replacement for `--help` output.

## Menu Principles

- The primary `arbiter` surface should start from user tasks, not internal
  implementation concepts.
- Discovery should be progressive: list broad things first, then drill into a
  specific plugin, account, operation, or artifact.
- Raw MCP access, bootstrap, and config inspection are secondary surfaces.
- Binary artifact handling must avoid writing raw artifact bytes to agent
  stdout unless the command is explicitly text-only and bounded.
- The user-facing term for installed modules in this client is `plugin`.

## Top-Level `arbiter`

Default help focuses on the primary commands:

```text
arbiter
  info       discover server, plugin, account, test, and operation context
  op         discover, describe, and run operations
  artifact   safely read, process, or explicitly save artifacts
```

Extended help exposes setup and low-level commands:

```text
arbiter --help --extended
  primary:
    info
    op
    artifact

  setup:
    bootstrap client
    config mcp-url

  advanced:
    mcp
```

## `info`

`info` is the general discovery menu. It returns structured context about the
server and configured plugins. It is appropriate when the user needs metadata,
account details, or broad inspection.

```text
arbiter info [--short] [--yaml]
arbiter info plugins [--yaml]
arbiter info plugin <plugin> [--yaml]
arbiter info accounts <plugin> [--yaml]
arbiter info account <plugin> <account> [--yaml]
arbiter info tests [--yaml]
arbiter info test <plugin> [account] [--yaml]
arbiter info ops <plugin> [--yaml]
arbiter info op <plugin> <operation> [--yaml]
```

Current behavior:

- `arbiter info` returns the overview payload.
- `arbiter info --short` returns a compact account summary.
- `arbiter info plugins` lists plugins with plugin descriptions and counts.
- `arbiter info accounts <plugin>` lists account descriptions and guidance.
- `arbiter info ops <plugin>` remains available, but `op list <plugin>` is the
  preferred operation-discovery path.

Current gap:

- Plugin-level guidance does not exist today. Guidance is account-level.
  Plugin descriptions exist.

## `op`

`op` is the primary operation workflow. It combines operation discovery,
inspection, and execution in one menu.

```text
arbiter op list
arbiter op list <plugin>
arbiter op desc <plugin>
arbiter op desc <plugin>:<operation>
arbiter op run <plugin>:<operation> --args <json-object>
```

Current behavior:

- `arbiter op list` prints JSON by default, shaped as `{"plugins": [...]}`.
- `arbiter op list <plugin>` prints JSON by default with operation summaries
  for one plugin, keyed by operation id.
- `arbiter op list [plugin] --plain` prints ids, one per line.
- `arbiter op list [plugin] --yaml` prints structured YAML.
- `arbiter op desc <plugin>` describes one plugin's operation surface as JSON
  by default.
- Operation ids use `<plugin>:<operation>` syntax.
- `arbiter op desc <operation-id>` describes one operation schema.
- `arbiter op desc <target> --plain` prints a compact text summary.
- `arbiter op desc <target> --yaml` prints structured YAML.
- `arbiter op run <operation-id> --args JSON` runs one operation.

Design intent:

- Do not dump all operations from all plugins by default.
- Teach the hierarchy: plugin first, operation second.
- Keep `list` and `desc` parallel: both can operate at the plugin level, and
  `desc` can also drill into one operation id.
- Use JSON as the default renderer for operation discovery and inspection;
  plain text is opt-in for compact human-readable lists and summaries.
- Keep `info ops <plugin>` as a structured discovery path, but do not make it
  the main route in help text.

## `artifact`

`artifact` is the explicit artifact access menu. It is designed around safe
agent handling of text and binary artifacts.

```text
arbiter artifact get <url> --stdout [--max-bytes N]
arbiter artifact save <url> <path>
arbiter artifact with-temp <url> [--max-child-stdout-bytes N] -- <argv...>
arbiter artifact with-stdin <url> [--max-child-stdout-bytes N] -- <argv...>
```

Current behavior:

- `get --stdout` is text-only and size-bounded.
- `save` writes to a file only when the user explicitly asks to save an
  artifact.
- `with-temp` downloads the artifact to a private temporary file and substitutes
  `{}` with that path in the child argv.
- `with-stdin` streams artifact bytes to the child process stdin.
- `with-temp` and `with-stdin` execute argv directly without a shell.
- Child stdout is bounded and must be textual.

Design intent:

- Raw binary artifact bytes should not enter agent stdout/context.
- Persistent file output is explicit-user-request only.
- Path-based tools use `with-temp`; stdin-based tools use `with-stdin`.

## Setup Menus

Setup commands are useful, but not primary daily workflow commands.

```text
arbiter bootstrap client [--force]
arbiter config mcp-url
```

Current behavior:

- `bootstrap client` writes the client config file.
- `config mcp-url` prints the resolved MCP URL after config/env/override
  resolution.

## Advanced Menu

`mcp` is the raw escape hatch for low-level MCP inspection and calls.

```text
arbiter mcp tools [--json]
arbiter mcp call <tool-name> [--args <json-object>]
```

Design intent:

- Keep this available for debugging and advanced use.
- Do not foreground it in default `arbiter --help`.

## Error And Help Behavior

Current Go client behavior:

- `arbiter --help` shows primary commands.
- `arbiter --help --extended` shows setup and advanced commands.
- Invalid subcommands print a concise usage error plus a help hint, for example:

```text
Arbiter usage error: unknown info command: aa
Run 'arbiter info --help' for help.
```

## Python Client Legacy Surface

The Python client currently exposes additional compatibility menus:

```text
arbiter-py cap ...
arbiter-py capabilities ...
arbiter-py accounts ...
arbiter-py operation ...
```

These overlap with the newer primary menus:

- `cap` / `capabilities` overlaps with `info plugins`, `info plugin`, and
  parts of `op list`.
- `accounts` overlaps with `info accounts` and `info account`.
- `operation` is an alias for `op`.

Design question:

- Decide whether these remain compatibility-only Python surfaces, move behind
  extended help, or are eventually folded into the primary `info` and `op`
  menus.

## Open Design Questions

- Should `op list` gain a compact table or plain-with-descriptions mode without
  dumping every operation from every plugin by default?
- Should plugin-level guidance be added to the server model, or should guidance
  remain account-level only?
- Should `info plugins` gain a compact table mode for descriptions, counts, and
  possibly guidance if plugin guidance is introduced?
- Should `info ops <plugin>` remain documented once `op list <plugin>` is fully
  established as the primary discovery path?
