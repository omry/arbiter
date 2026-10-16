# Retained security evidence

The [assessment](../prerelease-security-assessment.md) records the findings,
commands, results, trust decisions and limits for each review round. This
directory retains the independent DBB round 5 probe and its captured output so
that the latest review does not depend entirely on ignored `temp/` files.

- [Probe](round-5-probe.py): small in-memory IMAP transcripts using the real
  stdlib parser and Arbiter's plain/TLS parser classes.
- [Captured results](round-5-probe-results.json): the original three successful
  executions on Python 3.10, 3.13 and 3.14; 14 checks per interpreter.

Assessed source: `19fb583c032abc6d013dbe6aa3f2342ff5a6e83f`.
Assessor selection: `dbb`, alias `gpt-daybreak-blue-latest`, high reasoning.

The retained bytes match the original assessment artifacts:

| File | SHA256 |
| --- | --- |
| `round-5-probe.py` | `c22be88c5ea21eea73ac4d05bf3bd8c790716aca8bd475d1e6928788044e4ea4` |
| `round-5-probe-results.json` | `6722810cd7953da2daff9ca1b067bfa8fe193cb922eea8a9c518ef1cbf7edb70` |

The captured commands name the original temporary environments and probe path.
Those paths are provenance, not required checkout layout. To rerun the probe
with a repository development environment that has the server and IMAP plugin
installed:

```sh
.venv/bin/python docs/security-evidence/round-5-probe.py
```

Repeat with each supported interpreter environment when checking compatibility.
Runs on a later revision assess that revision, not the original merge. These
transcript probes do not use a live account, socket or TLS handshake. The
assessment separately records socket and regression test results; their raw
JUnit files and earlier rounds' local artifacts remain checkout-local and are
not included here. The report retains their essential commands and outcomes.
