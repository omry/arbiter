recording_setup_main() {
recording_repo="$PWD"
export recording_repo
unset IMAP_BOT_ACCOUNT_USERNAME
unset IMAP_BOT_ACCOUNT_PASSWORD
unset SMTP_BOT_ACCOUNT_USERNAME
unset SMTP_BOT_ACCOUNT_PASSWORD
unset ARBITER_REPO_ROOT
unset ARBITER_PYTHON
operator_venv=""
arbiter_source="${recording_param_arbiter_source:-latest}"
arbiter_package="${recording_param_arbiter_package:-arbiter-suite}"

recording_operator_venv_cache_key() {
  local package_requirement="$1"
  "$recording_python" - "$package_requirement" <<'PY'
import hashlib
import json
import sys

payload = {
    "kind": "operator-venv",
    "mode": "pypi",
    "python": list(sys.version_info[:3]),
    "requirement": sys.argv[1],
}
print(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16])
PY
}

recording_cached_operator_venv() {
  local package_requirement="$1"
  local cache_key
  cache_key="$(recording_operator_venv_cache_key "$package_requirement")"
  local cache_dir="$recording_operator_venv_cache_root/$cache_key"
  local cached_venv="$cache_dir/venv"
  local ready_file="$cache_dir/READY"
  local lock_dir="$recording_operator_venv_cache_root/$cache_key.lock"

  mkdir -p "$recording_operator_venv_cache_root"
  if [[ -f "$ready_file" ]] && ! recording_operator_venv_is_healthy "$cached_venv"; then
    rm -f "$ready_file"
  fi
  if [[ ! -f "$ready_file" ]]; then
    local have_lock=0
    for _cache_lock_attempt in $(seq 1 600); do
      [[ -f "$ready_file" ]] && break
      if mkdir "$lock_dir" 2>/dev/null; then
        have_lock=1
        break
      fi
      sleep 0.2
    done
    if [[ ! -f "$ready_file" && "$have_lock" != 1 ]]; then
      printf 'timed out waiting for operator venv cache lock: %s\n' "$lock_dir" >&2
      return 1
    fi
    if [[ "$have_lock" == 1 ]]; then
      if [[ ! -f "$ready_file" ]]; then
        rm -rf "$cache_dir"
        mkdir -p "$cache_dir"
        "$recording_python" -m venv "$cached_venv" >&2
        "$cached_venv/bin/python" -m pip install --upgrade pip >&2
        "$cached_venv/bin/python" -m pip install "$package_requirement" >&2
        "$recording_python" - "$cache_dir/metadata.json" "$cache_key" "$package_requirement" "$recording_python" <<'PY'
import json
import sys
from pathlib import Path

path, cache_key, requirement, python = sys.argv[1:]
Path(path).write_text(
    json.dumps(
        {
            "cache_key": cache_key,
            "package_requirement": requirement,
            "python": python,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
        touch "$ready_file"
      fi
      rmdir "$lock_dir" 2>/dev/null || true
    fi
  fi
  [[ -f "$ready_file" ]] || {
    printf 'operator venv cache was not created: %s\n' "$cache_dir" >&2
    return 1
  }
  export ARBITER_CINEMA_OPERATOR_VENV_CACHE_KEY="$cache_key"
  printf '%s\n' "$cached_venv"
}

recording_operator_venv_is_healthy() {
  local venv="$1"
  local script="$venv/bin/arbiter-server"
  [[ -x "$venv/bin/python" && -x "$script" ]] || return 1
  local shebang
  IFS= read -r shebang <"$script" || return 1
  if [[ "$shebang" == '#!'* ]]; then
    local interpreter="${shebang:2}"
    interpreter="${interpreter%% *}"
    [[ -x "$interpreter" ]] || return 1
  fi
}

if [[ "$arbiter_source" == local ]]; then
  command -v arbiter-server >/dev/null
  command -v arbiter >/dev/null
  export ARBITER_REPO_ROOT="$recording_repo"
  export ARBITER_PYTHON="$recording_python"
  operator_bin="$(command -v arbiter-server)"
  detected_venv="$(cd "$(dirname "$operator_bin")/.." && pwd)"
  if [[ -f "$detected_venv/bin/activate" ]]; then
    operator_venv="$detected_venv"
  fi
else
  if [[ "$arbiter_source" == latest ]]; then
    if ! package_version="$("$recording_python" - "$arbiter_package" <<'PY'
import json
import sys
import urllib.request
from packaging.version import Version

package = sys.argv[1]
with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=30) as response:
    data = json.load(response)
versions = []
for version, files in data["releases"].items():
    if any(not file.get("yanked", False) for file in files):
        versions.append(Version(version))
if not versions:
    raise SystemExit(f"no non-yanked releases found for {package}")
print(max(versions))
PY
)"; then
      printf 'failed to resolve latest PyPI version for %s\n' "$arbiter_package" >&2
      return 1
    fi
  else
    package_version="$arbiter_source"
  fi
  package_requirement="$arbiter_package==$package_version"
  if ! cached_operator_venv="$(recording_cached_operator_venv "$package_requirement")"; then
    return 1
  fi
  operator_venv="$recording_tmp/operator-venv"
  ln -sfn "$cached_operator_venv" "$operator_venv"
  export PATH="$operator_venv/bin:$PATH"
  export ARBITER_CINEMA_RESOLVED_PACKAGE_REQUIREMENT="$package_requirement"
fi

recording_prepare_cli_env() {
  [[ -n "$operator_venv" ]] || {
    printf 'operator venv is not available\n' >&2
    return 1
  }
  ln -sfn "$operator_venv" arbiter_venv
}

recording_prepare_bundle() {
  if [[ "$arbiter_source" == local ]]; then
    local bundle_output
    if ! bundle_output="$(
      {
        ./arbiter-docker bundle add-source "$recording_repo/server"
        ./arbiter-docker bundle add-source "$recording_repo/plugins/imap"
        ./arbiter-docker bundle add-source "$recording_repo/plugins/smtp"
        "$recording_python" - ./requirements.txt <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
local_packages = {"arbiter-server", "arbiter-imap", "arbiter-smtp"}
kept = []
for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    name = re.split(r"==", line, maxsplit=1)[0].split("[", 1)[0]
    normalized_name = re.sub(r"[-_.]+", "-", name.lower())
    if "==" in line and normalized_name in local_packages:
        continue
    kept.append(raw_line)
path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
PY
      } 2>&1
    )"; then
      printf '%s\n' "$bundle_output" >&2
      return 1
    fi
  fi
  ./arbiter-docker bundle prepare
}

recording_configure_staging_subnet() {
  local subnet="${ARBITER_CINEMA_STAGING_SUBNET:-}"
  [[ -n "$subnet" ]] || return 0
  [[ -f arbiter-docker/docker.env ]] || {
    printf 'recording staging docker.env not found: arbiter-docker/docker.env\n' >&2
    return 1
  }
  "$recording_python" - arbiter-docker/docker.env "$subnet" <<'PY'
import sys
from ipaddress import ip_network
from pathlib import Path

path = Path(sys.argv[1])
subnet = str(ip_network(sys.argv[2], strict=True))
lines = path.read_text(encoding="utf-8").splitlines()
updated = False
for index, line in enumerate(lines):
    if line.startswith("ARBITER_DOCKER_SUBNET="):
        lines[index] = f"ARBITER_DOCKER_SUBNET={subnet}"
        updated = True
        break
if not updated:
    lines.append(f"ARBITER_DOCKER_SUBNET={subnet}")
path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
PY
}

recording_apply_mail_lab_config() {
  set -a
  . "$MAIL_LAB_ENV_FILE"
  set +a
  "$recording_python" "$recording_repo/media/tools/apply_mail_lab_config.py" \
    --config-dir ./conf "$@"
}

recording_workspace="$recording_tmp/operator-workspace"
mkdir -p "$recording_workspace"
mail_lab_env="$recording_tmp/mail-lab.env"
mail_lab_ready="$recording_tmp/mail-lab.ready"
mail_lab_log="$recording_tmp/mail-lab.log"
"$recording_python" "$recording_repo/media/tools/mail_lab.py" \
  --env-file "$mail_lab_env" \
  --ready-file "$mail_lab_ready" \
  --seed \
  >"$mail_lab_log" 2>&1 &
mail_lab_pid=$!
cleanup_pids+=("$mail_lab_pid")
for _attempt in $(seq 1 80); do
  [[ -s "$mail_lab_env" && -e "$mail_lab_ready" ]] && break
  sleep 0.1
done
[[ -s "$mail_lab_env" ]] || { cat "$mail_lab_log" >&2; return 1; }
rm -f "$mail_lab_ready"
export MAIL_LAB_ENV_FILE="$mail_lab_env"
cd "$recording_workspace"
recording_write_postmortem_entrypoint "$recording_workspace" "$operator_venv"
arbiter-server version --json || return 1
arbiter --version || return 1
}

recording_setup_main
