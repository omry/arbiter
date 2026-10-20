Add offline `arbiter-server authority init/status` commands for private, persistent
owner and server identity metadata. Initialization rejects existing or unsafe
state and serializes concurrent attempts. This is authentication groundwork;
caller authentication is not enabled by these commands.
