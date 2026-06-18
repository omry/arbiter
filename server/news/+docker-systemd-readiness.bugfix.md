Installed Docker systemd units now wait for the Docker CLI and API to become
ready before running Compose, avoiding boot races with Docker Desktop WSL
integration and slow native Docker startup. The Docker installer can also
update installed files and the systemd unit while Docker is unavailable,
reporting Docker-backed checks and restart as skipped; when the existing
service is active, it updates only the unit so running Compose inputs are not
replaced underneath the service.
