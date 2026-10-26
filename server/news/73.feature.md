Commit owner-approved enrollment once after durable client readiness. The native
client saves activated credentials only after authenticated receipt confirmation
and supports `arbiter enrol resume=SELECTOR` after an interrupted final exchange.
Committed results survive server restart without a pairing secret. Ordinary
authenticated API operations remain part of a later milestone.
