Offline authority setup now requires an external private operator recovery file.
It creates separate recovery-signing and backup-decryption keys, registers only
their public trust in server state, and retains trusted backup-origin public keys
outside the server. Explicit reuse preserves recovery keys across setup retries
and additional servers. Setup refuses unsafe custody, unexpected overwrites and
incomplete recovery trust. Runtime recovery and encrypted backup export remain
later authentication milestones.
