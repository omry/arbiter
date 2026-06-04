# Arbiter Echo Plugin Example

This is a small copyable Arbiter service plugin. It is intentionally simple:
the plugin exposes one capability, `echo`, and one operation, `echo:echo_message`.

Use it as a starting point for a new plugin:

1. Copy this directory.
2. Rename the distribution, package, capability, and entry point.
3. Replace the config dataclasses with service-specific account and policy
   fields.
4. Replace `EchoRuntime.echo_message()` with real service behavior.

## Package Entry Point

Arbiter discovers service plugins through the `arbiter.services` entry point:

```toml
[project.entry-points."arbiter.services"]
echo = "arbiter_echo_example:plugin"
```

The entry point should point to a factory function that returns a plugin object
implementing the `ServicePlugin` protocol from `arbiter_core.services`.

## Files

- `src/arbiter_echo_example/config.py`: account and policy config schemas plus
  Hydra ConfigStore registration.
- `src/arbiter_echo_example/__init__.py`: runtime, operation metadata,
  bootstrap examples, and plugin object.
- `tests/test_echo_example.py`: a focused test showing direct runtime use and
  core catalog dispatch.
