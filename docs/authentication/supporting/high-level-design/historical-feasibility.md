# Historical authentication feasibility evidence

This preserves the original HLD probe record. It is context, not a current
implementation, packaging, dependency-security or release claim. The source HLD
was SHA-256 `b6511414b3a9b485542df83c8f7abfc499dc8d06e522819b48e162a562d21c57`.

Local evidence, 2026-10-20: fourteen synthetic checks passed on Linux amd64,
CPython 3.13.12 and Go 1.26.8, using pyOpenSSL 25.3.0, cryptography 46.0.7,
pyrage 1.3.0, websockets 15.0.1, Go age 1.2.1 and coder/websocket 1.8.14.
Results JSON SHA-256:
`34071a913ba400e79242ebe0b4481214a953f14fb78815e0f62ab82b180e789b`.
Checks covered TLS-over-WebSocket/exporter agreement, ordinary expired-certificate
rejection, separate expired-key control proofs and cross-channel replay rejection;
age both directions, second recipient, wrong key, tamper/truncation, immutable
snapshot/path replacement and 50 MiB plaintext; ES256 fixed-width JWS signing-byte
compatibility and changed-byte rejection. `go vet` passed and the peer compiled
with Go 1.24.13 (execution used 1.26.8). These fixtures are not complete profile,
JWS schema, recovery, overload or crash implementations. pyOpenSSL 26.3.0 was
rejected for this design's compatibility assessment because it requires
cryptography 49 outside Arbiter's current `<49` range. The compatible probe pair
is a tested candidate, not permission to freeze old versions indefinitely; resolve
production versions/advisories and platform builds in the implementation plan.
