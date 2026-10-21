On POSIX, authority initialization and status can now reach the running server
through a private Unix socket authenticated by kernel peer credentials in both
directions. Existing commands retain stopped-instance operation. Control messages,
connections and work are bounded, and shutdown keeps the instance lock until
actual administrative work finishes. Run commands as the server's OS account;
loopback HTTP and socket-path knowledge do not grant administrative authority.
