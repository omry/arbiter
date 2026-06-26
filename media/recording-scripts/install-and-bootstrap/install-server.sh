install_root="$(pwd -P)/fakeroot-install"
current_user="$(id -un)"
current_group="$(id -gn)"
set -euo pipefail
rm -rf "$install_root"
mkdir -p "$install_root/opt"

rewrite_install_output() {
  sed \
    -e "s#${install_root}/opt/arbiter#/opt/arbiter#g" \
    -e "s#${current_user}:${current_group}#arbiter:arbiter#g"
}

printf '\nREPLOY_INSTALL_OWNER=%s:%s\n' "$current_user" "$current_group" >> .reploy/docker.env
./reploy install --to "$install_root/opt/arbiter" --no-start --dry-run | rewrite_install_output
