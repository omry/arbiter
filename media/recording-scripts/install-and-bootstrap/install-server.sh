install_root="$(pwd -P)/fakeroot-install"
current_user="$(id -un)"
current_group="$(id -gn)"
rm -rf "$install_root"
mkdir -p "$install_root/bin" "$install_root/etc/systemd/system"
cat > "$install_root/bin/sudo" <<'SH'
#!/usr/bin/env sh
exec fakeroot "$@"
SH
cat > "$install_root/bin/systemctl" <<'SH'
#!/usr/bin/env sh
printf 'systemctl %s\n' "$*" >> "$ARBITER_FAKE_SYSTEMCTL_LOG"
exit 0
SH
chmod +x "$install_root/bin/sudo" "$install_root/bin/systemctl"
rewrite_install_output() {
  sed \
    -e "s#${install_root}/opt/arbiter#/opt/arbiter#g" \
    -e "s#${install_root}/etc/systemd/system#/etc/systemd/system#g" \
    -e "s#Running as ${current_user}:${current_group}#Running as arbiter:arbiter#g"
}
ARBITER_SYSTEMD_DIR="$install_root/etc/systemd/system" ARBITER_FAKE_SYSTEMCTL_LOG="$install_root/systemctl.log" PATH="$install_root/bin:$PATH" sudo ./arbiter-docker install --to "$install_root/opt/arbiter" --user "$current_user" --group "$current_group" --no-start | rewrite_install_output
sed -n '1,22p' "$install_root/etc/systemd/system/arbiter.service" | rewrite_install_output
cat "$install_root/systemctl.log"
