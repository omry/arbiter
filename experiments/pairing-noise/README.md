# Python/Go pairing compatibility experiment

This isolated probe tests `Noise_NNpsk0_25519_ChaChaPoly_SHA256` with a fresh
256-bit pre-shared secret. It does not implement Arbiter enrollment or select
production dependencies. No real identity, account, credential or listener is
used. The module and virtual environment are separate from the product.

## Run

Use CPython 3.10–3.14 and Go 1.26 or newer:

```sh
python experiments/pairing-noise/run.py
```

The runner creates an isolated `.venv`, installs exact versions from
`requirements.txt`, builds the pinned Go peer with CGo disabled, runs `go vet`
and executes all 13 test groups. Logs, environment/dependency versions and
`test-results.json` go into the ignored `results/` directory. Select another
location with `--output-dir PATH`. Initial installation needs network access.

The Windows ARM64 CI step reuses the existing OpenSSL setup and permits source
builds of Python dependencies. It checks CPython 3.13 on that native runner;
`--require-windows-arm64` rejects an x64 interpreter or Go target. The experiment
uses a separate Go setup after product tests, leaving their toolchain intact.
CI uploads logs even if installation, compilation or tests fail.

There is no wheel-only installation requirement: Arbiter already depends on
`cryptography`, and its Windows ARM64 CI provisions OpenSSL for dependency
builds. A missing wheel alone is not evidence that Arbiter drops that platform.
This probe must actually run there before claiming native compatibility.

## Scope of the checks

Both Python/Go role combinations check matching handshake hashes and directional
key fingerprints, explicit encrypted confirmations, wrong secrets/context,
tampered ephemeral keys and handshake payloads, malformed/truncated messages,
transport tampering/replay, missing/wrong-length PSKs, fresh sessions, and
rejection of an old session's confirmation after a replayed first message.

The PSK is provided through a trusted local test-control pipe to configure the
Go peer. That pipe is not the simulated network: only Noise output bytes are
treated as network messages. Tests check that those messages do not contain
the raw secret or its Base64url form. Actual keys/secrets are not logged.
The probe uses synthetic context and credential payloads, not an adopted Arbiter
wire format. Key fingerprint extraction is test-only.

Noise alone does not reject a replayed first message. Real enrollment still
needs owner approval, bounded pending-state/attempts, expiry, binding and
transactional one-time completion. Missing PSKs must fail closed: the Go
library can otherwise configure unauthenticated NN. Failed handshake state is
discarded rather than reused.

These tests demonstrate interoperability only. The Python wrapper's tested
release is from 2020; its README states explicit Linux support only and leaves
peer review/side-channel work unfinished. Production library assessment,
certificate profiles, durable identity binding, full platform execution and
independent security review remain open. This experiment's crypto module needs
Go 1.26; it does not change Arbiter's Go 1.24 requirement.

No package release note is needed: this changes experimental evidence and CI,
with no change to a published package or product behavior.

Primary sources:

- [Noise PSK specification](https://noiseprotocol.org/noise.html#pre-shared-symmetric-keys)
- [Python candidate at the tested tag](https://github.com/plizonczyk/noiseprotocol/tree/v0.3.1)
- [Go candidate at the tested tag](https://github.com/flynn/noise/tree/v1.1.0)
