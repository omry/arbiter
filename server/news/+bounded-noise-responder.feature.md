Add the bounded Noise responder foundation for authentication pairing, with
role/request/key binding, live approval checks, authenticated attempt accounting,
immutable replay replies, and cancellation-safe ownership of connection and
crypto capacity. This introduces the pinned `noiseprotocol` dependency and named
`arbiter.limits.native_transport` settings. Public WebSocket enrollment and
durable activation are subsequent delivery steps; this foundation grants no access.
