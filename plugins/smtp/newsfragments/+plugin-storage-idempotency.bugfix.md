Moved default SMTP idempotency storage into the server-managed SMTP plugin data
directory instead of a working-directory-relative cache path, and validate that
storage during account tests before keyed sends are attempted.
