# Backend feasibility and limits

This is planning evidence, not product validation or dependency-security approval.
The source baseline is `d52f8bc0acadc1de0dcb53fb1a96eb3e822e174a`.
Existing server dependencies are pyOpenSSL26.2.0/cryptography>=42,<49; the current
Go client uses Gorilla WebSocket1.5.3 and Go1.24. No dependency was installed or
changed for this design.

The existing historical synthetic probe establishes binary age interoperability,
not bounded product I/O or platform approval. For this revision, a small isolated
probe used the already-existing pyrage1.3.0 probe environment with synthetic keys
and a writer that raises on every write. `encrypt_io` returned normally despite
the failing final write; a caller-owned sticky failure recorded the error.
`decrypt_io` propagated OSError in the exercised case. Both paths therefore
require sticky I/O state and measured integrity/finalization checks; do not infer
successful publication from return status. No source or live service was changed.

The probe source and JSON result are retained in the redesign evidence under
`lld-probes/age_io_failure.py` and `lld-probes/age_io_failure.json`. Their immutable
content digests are recorded below by the authoring validator. This check does
not measure all-platform GIL scheduling, cancellation, disk durability, package
installation or release advisories. Those remain the concrete first-use/native
acceptance checks. Dependency adoption, compatible resolution, reachable advisory
checks and a supported-matrix native recipient-challenge round-trip belong to
first consumer 7.1.1. Streaming GIL/progress/cancellation and sticky I/O checks
belong to 7.2; 7.6 extends installed composition and packaging. Backup consumers
retain their own later acceptance. The historical probe does not satisfy these gates.

Primary source inspection supports the selected engineering boundary:

- [pyrage1.3.0 streaming source](https://github.com/woodruffw/pyrage/blob/v1.3.0/src/lib.rs): use file-like streaming callbacks, not whole-buffer convenience APIs.
- [pyrage1.3.0 package requirements](https://github.com/woodruffw/pyrage/blob/v1.3.0/pyproject.toml): Python>=3.9 is compatible with the project floor; this alone proves no wheel/OS matrix.
- [age1.2.1 module](https://github.com/FiloSottile/age/blob/v1.2.1/go.mod): Go1.19 minimum; evaluate with this project's newer transitive dependencies.
- [age1.3.2 module](https://github.com/FiloSottile/age/blob/v1.3.2/go.mod): Go1.25 minimum, so it is not selected under the current Go1.24 floor.

The plan keeps binary age/X25519, bounded headers and callback-owned work. It adds
no custom format to fix library I/O and does not claim old versions are indefinitely
safe. A failed current advisory/install/native check blocks the affected first-use
cut and requires supported dependency/design correction, not a platform reduction.

Retained probe identities:

- `age_io_failure.json` SHA256 `92504539724dd6fde9b267519ddfcd4887c2add9f6947fb0f533107d59af0b1c`.
- `age_io_failure.py` SHA256 `bbc9bb6db11270ecb98933e0bcb00d648897c642f1839d15fb55f148d74b4d3c`.
