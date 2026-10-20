Authority setup publishes complete private generations with a durable active
selector and restart reconciliation. Interrupted publication preserves the exact
initialized IDs and trust; damaged or missing saved state never creates a new
identity. Server startup holds the same instance lock as offline setup through
HTTP shutdown, preventing concurrent writers. Windows publication uses native
write-through moves. Runtime API authentication remains a later milestone.
