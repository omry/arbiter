Add OS-authenticated local enrollment open/list/approve/reject commands with
hidden pairing-secret input and explicit configured account grants. Approval
binds the selected request's keys and revision without extending its deadline
or leaving the shared pending-request capacity bound. Restart, expiry and
rejection discard ephemeral approvals. This prepares pairing and does not yet
activate client access. Administrative opening is acknowledged without returning
the registry, and listing uses bounded pages with exact selector lookup for
approval and rejection so every accepted positive capacity remains operable.
Approval and rejection use `--selector=VALUE` to accept all generated selectors,
including those that begin with a hyphen.
