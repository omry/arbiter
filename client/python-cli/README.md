# Arbiter Python CLI

This package provides the `arbiter-py` command for talking to an Arbiter MCP
server from Python.

Install it from a source checkout with:

```bash
python -m pip install -e client/python-cli
```

It intentionally mirrors the native `arbiter` client command shape while the
Python CLI remains available. For explicit small textual artifacts:

```bash
arbiter-py artifact get "$ARBITER_ARTIFACT_URL" --stdout
```
