# Enrollment and owner issuer trust

Fresh client enrollment uses the existing `arbiter enrol` command and local
operator approval. Registering an owner issuer establishes public trust only;
it does not enroll a client or grant account access. Selecting an existing
portable identity for another server remains unavailable in this revision.

Use `arbiter-server authority owner-issuer` through the same authenticated local
OS account as server administration. Commands work while the instance is stopped
or through its authenticated local control while running. Remote administration
uses verified SSH to run these commands on the server host.

```sh
arbiter-server authority owner-issuer list --state-dir ROOT --json
arbiter-server authority owner-issuer export --state-dir ROOT --issuer-sha256 FINGERPRINT > owner.der
arbiter-server authority owner-issuer register --state-dir ROOT --certificate-file owner.der --json
arbiter-server authority owner-issuer remove --state-dir ROOT --issuer-sha256 FINGERPRINT --json
```

Obtain public owner DER from an independently trusted operator. Registration
requires the exact valid Arbiter owner certificate. Keep private issuer and
recovery keys private. The fingerprint is the lowercase SHA-256 of the exact
canonical DER. Distinct issuer keys can share an owner ID; each certificate
requires its own explicit registration.

List returns at most 32 summaries and a `next_cursor`. Pass that value with
`list --cursor FINGERPRINT` to continue. Export returns one public DER certificate
as binary stdout. Register reads at most 4,096 bytes from the local CLI file;
it sends certificate bytes, not a file path for the server to open.

Removing trust retains an inactive record. Repeating the same registration or
removal is idempotent; reactivating a removed certificate advances its trust
revision. Restart does not reactivate removed trust. Removal blocks new
activation under that issuer and does not revoke an already activated access
credential or change its historical enrollment result.

The first upgraded startup performs an atomic, one-way authority schema upgrade.
It preserves existing credentials, exact enrollment results, denial and inactivity
state, and binds each existing agent to its enrollment. Invalid or duplicate
historical bindings stop startup and preserve the old database. Preserve the
state and resolve the reported error; do not delete authority files to retry.
New fresh enrollments maintain that binding in the same activation transaction.

The portable identity codec groundwork validates a source only from complete
`enrolled/` custody. It checks the owner and portable certificate at the current
time without contacting the source server or requiring its access certificate
to remain valid. Saved receipt recovery checks portable evidence at issuance;
this does not permit an expired portable identity to start another enrollment.
The public CLI continues to create fresh identities at this intermediate cut.
