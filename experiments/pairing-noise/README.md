# Archived Python/Go pairing experiment

The experiment code and its CI workflow remain in [unmerged PR
#34](https://github.com/omry/arbiter/pull/34). They are not part of the
authentication delivery stack or product dependencies. This note preserves
the evidence links used by the authentication design.

The [exact source and run instructions](https://github.com/omry/arbiter/tree/b02cf0c9fb42eb5862f27c7eeda1af9d45033d12/experiments/pairing-noise)
are retained at the experiment's published commit. The probe uses
`Noise_NNpsk0_25519_ChaChaPoly_SHA256`, Python `noiseprotocol==0.3.1`, and Go
`github.com/flynn/noise v1.1.0` with a random 256-bit pre-shared secret.

All 13 synthetic compatibility and negative-test groups passed locally on
Linux and in [native Windows ARM64 CI](https://github.com/omry/arbiter/actions/runs/39405923025/job/118572268983).
The Windows result used native CPython 3.13.16 and Go 1.26.9 with CGo disabled.
That run retains the `pairing-noise-windows-arm64` evidence artifact. Its
successful experiment job does not mean every unrelated job in that workflow
passed. GitHub artifacts have finite retention; the authentication evidence
document also records the findings and their limits.

The probe establishes bounded synthetic interoperability. It does not approve
production libraries, implement enrollment, prove all supported platform and
Python combinations, or establish release security. It uses no real account
credentials or service listener. Refer to the archived source for the full
test scope and implementation limitations.
