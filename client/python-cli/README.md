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

For binary artifacts, run an explicit reader command so raw artifact bytes do
not pass through stdout:

```bash
arbiter-py artifact with-temp "$ARBITER_ARTIFACT_URL" -- pandoc '{}' -t plain
arbiter-py artifact with-stdin "$ARBITER_ARTIFACT_URL" -- pandoc -f docx -t plain -
```

When the user explicitly asks to save the artifact to a file:

```bash
arbiter-py artifact save "$ARBITER_ARTIFACT_URL" ./attachment.pdf
```
