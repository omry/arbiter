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
reploy_package="${recording_param_reploy_package:-reploy}"
operator_venv_cache_retain="${recording_param_operator_venv_cache_retain:-8}"
recording_operator_venv_cache_root="$recording_repo/media/cache/operator-venvs"
recording_operator_venv_log="$recording_tmp/operator-venv.log"
: >"$recording_operator_venv_log"

recording_filter_docker_compose_progress() {
  "$recording_python" - <<'PY'
import re
import sys

ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
progress = re.compile(
    r"^\s*(Network|Container) .+ "
    r"(Creating|Created|Starting|Started|Stopping|Stopped|Removing|Removed)\s*$"
)

for raw_line in sys.stdin:
    clean = ansi.sub("", raw_line).replace("\r", "").rstrip("\n")
    if progress.match(clean):
        continue
    sys.stdout.write(raw_line)
PY
}

recording_validate_operator_venv_cache_retain() {
  if [[ ! "$operator_venv_cache_retain" =~ ^[1-9][0-9]*$ ]]; then
    printf 'operator_venv_cache_retain must be a positive integer: %s\n' \
      "$operator_venv_cache_retain" >&2
    return 1
  fi
}

recording_operator_wheelhouse_metadata() {
  local package_requirement="$1"
  local wheelhouse="$2"
  local output_path="$3"
  "$recording_python" - "$package_requirement" "$wheelhouse" "$output_path" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

requirement, wheelhouse, output_path = sys.argv[1:]
files = []
for path in sorted(Path(wheelhouse).iterdir()):
    if not path.is_file():
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files.append({"name": path.name, "sha256": digest, "size": path.stat().st_size})
payload = {
    "files": files,
    "kind": "operator-venv",
    "mode": "resolved-wheelhouse",
    "package_requirement": requirement,
    "python": list(sys.version_info[:3]),
}
payload["cache_key"] = hashlib.sha256(
    json.dumps(payload, sort_keys=True).encode()
).hexdigest()[:16]
Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

recording_wheel_requirement() {
  local wheelhouse="$1"
  local package_name="$2"
  "$recording_python" - "$wheelhouse" "$package_name" <<'PY'
import sys
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

wheelhouse, package_name = sys.argv[1:]
expected_name = canonicalize_name(package_name)
matches = []
for path in sorted(Path(wheelhouse).glob("*.whl")):
    name, version, _build, _tags = parse_wheel_filename(path.name)
    if canonicalize_name(name) == expected_name:
        matches.append((version, path.name))
if len(matches) != 1:
    raise SystemExit(
        f"expected one wheel for {package_name}, found {len(matches)}"
    )
version, _name = matches[0]
print(f"{package_name}=={version}")
PY
}

recording_wheel_path() {
  local wheelhouse="$1"
  local package_name="$2"
  "$recording_python" - "$wheelhouse" "$package_name" <<'PY'
import sys
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

wheelhouse, package_name = sys.argv[1:]
expected_name = canonicalize_name(package_name)
matches = []
for path in sorted(Path(wheelhouse).glob("*.whl")):
    name, _version, _build, _tags = parse_wheel_filename(path.name)
    if canonicalize_name(name) == expected_name:
        matches.append(path)
if len(matches) != 1:
    raise SystemExit(
        f"expected one wheel for {package_name}, found {len(matches)}"
    )
print(matches[0])
PY
}

recording_operator_venv_is_healthy() {
  local venv="$1"
  local script
  [[ -x "$venv/bin/python" ]] || return 1
  for script in "$venv/bin/reploy" "$venv/bin/arbiter-server" "$venv/bin/arbiter"; do
    [[ -x "$script" ]] || return 1
  done
  script="$venv/bin/arbiter-server"
  local shebang
  IFS= read -r shebang <"$script" || return 1
  if [[ "$shebang" == '#!'* ]]; then
    local interpreter="${shebang:2}"
    interpreter="${interpreter%% *}"
    [[ -x "$interpreter" ]] || return 1
  fi
}

recording_run_operator_venv_step() {
  local label="$1"
  shift
  {
    printf '\n::: %s\n' "$label"
    printf '$'
    printf ' %q' "$@"
    printf '\n'
  } >>"$recording_operator_venv_log"
  if ! "$@" >>"$recording_operator_venv_log" 2>&1; then
    printf 'operator venv step failed: %s\n' "$label" >&2
    if [[ -s "$recording_operator_venv_log" ]]; then
      printf -- '--- operator venv log (last 120 lines) ---\n' >&2
      tail -n 120 "$recording_operator_venv_log" >&2
      printf -- '--- end operator venv log ---\n' >&2
    fi
    return 1
  fi
}

recording_prune_operator_venv_cache() {
  local current_cache_key="$1"
  recording_validate_operator_venv_cache_retain || return 1
  "$recording_python" - \
    "$recording_operator_venv_cache_root" \
    "$current_cache_key" \
    "$operator_venv_cache_retain" <<'PY'
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
current_cache_key = sys.argv[2]
retain = int(sys.argv[3])

if not root.is_dir():
    raise SystemExit(0)

entries = []
for path in root.iterdir():
    if not path.is_dir() or path.name.endswith(".lock"):
        continue
    ready = path / "READY"
    try:
        timestamp = ready.stat().st_mtime if ready.exists() else path.stat().st_mtime
    except OSError:
        continue
    entries.append((timestamp, path.name, path))

entries.sort(reverse=True)
keep = {name for _timestamp, name, _path in entries[:retain]}
keep.add(current_cache_key)

for _timestamp, name, path in entries:
    if name in keep:
        continue
    if (root / f"{name}.lock").exists():
        continue
    try:
        shutil.rmtree(path)
    except OSError as exc:
        print(f"warning: failed to prune operator venv cache {path}: {exc}", file=sys.stderr)
PY
}

recording_prepare_operator_venv_from_wheelhouse() {
  local cache_requirement="$1"
  local wheelhouse="$2"
  local install_mode="${3:-offline}"
  shift 3 || true
  local metadata_path="$recording_tmp/operator-venv.json"
  local cache_key
  local cache_dir
  local cached_venv
  local ready_file
  local lock_dir
  local -a install_requirements=("$@")
  local -a pip_install_args=(--find-links "$wheelhouse")

  if [[ ${#install_requirements[@]} -eq 0 ]]; then
    install_requirements=("$cache_requirement")
  fi
  if [[ "$install_mode" == offline ]]; then
    pip_install_args=(--no-index "${pip_install_args[@]}" "${install_requirements[@]}")
  elif [[ "$install_mode" == online ]]; then
    pip_install_args+=("${install_requirements[@]}")
  else
    printf 'unknown operator venv install mode: %s\n' "$install_mode" >&2
    return 1
  fi

  recording_operator_wheelhouse_metadata \
    "$cache_requirement" "$wheelhouse" "$metadata_path"
  cache_key="$("$recording_python" - "$metadata_path" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cache_key"])
PY
)"
  cache_dir="$recording_operator_venv_cache_root/$cache_key"
  cached_venv="$cache_dir/venv"
  ready_file="$cache_dir/READY"
  lock_dir="$recording_operator_venv_cache_root/$cache_key.lock"

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
      {
        if [[ ! -f "$ready_file" ]]; then
          rm -rf "$cache_dir"
          mkdir -p "$cache_dir"
          recording_run_operator_venv_step \
            "create operator venv" \
            "$recording_python" -m venv "$cached_venv"
          recording_run_operator_venv_step \
            "upgrade operator venv pip" \
            "$cached_venv/bin/python" -m pip install --upgrade pip
          recording_run_operator_venv_step \
            "install operator commands" \
            "$cached_venv/bin/python" -m pip install \
            "${pip_install_args[@]}"
          cp "$metadata_path" "$cache_dir/metadata.json"
          touch "$ready_file"
        fi
      } || {
        rmdir "$lock_dir" 2>/dev/null || true
        return 1
      }
      rmdir "$lock_dir" 2>/dev/null || true
    fi
  fi
  [[ -f "$ready_file" ]] || {
    printf 'operator venv cache was not created: %s\n' "$cache_dir" >&2
    return 1
  }
  touch "$ready_file"
  recording_prune_operator_venv_cache "$cache_key"
  operator_venv="$recording_tmp/operator-venv"
  ln -sfn "$cached_venv" "$operator_venv"
  export PATH="$operator_venv/bin:$PATH"
  export ARBITER_CINEMA_OPERATOR_VENV_CACHE_KEY="$cache_key"
  export ARBITER_CINEMA_RESOLVED_PACKAGE_REQUIREMENT="$package_requirement"
}

recording_prepare_pypi_operator_venv() {
  local package_requirement="$1"
  local wheelhouse="$recording_tmp/operator-wheelhouse"
  local cache_requirement="$reploy_package $package_requirement"

  rm -rf "$wheelhouse"
  mkdir -p "$wheelhouse"
  recording_run_operator_venv_step \
    "build operator wheelhouse" \
    "$recording_python" -m pip wheel \
    --disable-pip-version-check \
    --no-cache-dir \
    --wheel-dir "$wheelhouse" \
    "$reploy_package" \
    "$package_requirement"
  recording_prepare_operator_venv_from_wheelhouse \
    "$cache_requirement" "$wheelhouse" offline "$reploy_package" "$package_requirement"
}

recording_prepare_local_operator_venv() {
  local wheelhouse="$recording_tmp/operator-wheelhouse"
  local package_requirement
  local package_name
  local source_dir
  local package_source
  local -a package_sources=(
    server
    client
    plugins/smtp
    plugins/imap
    meta/arbiter-suite
  )
  local -a local_package_wheels=()
  local -a local_package_names=(
    arbiter-server
    arbiter-client
    arbiter-smtp
    arbiter-imap
    arbiter-suite
  )

  rm -rf "$wheelhouse"
  mkdir -p "$wheelhouse"
  for package_source in "${package_sources[@]}"; do
    source_dir="$recording_repo/$package_source"
    recording_run_operator_venv_step \
      "build local operator wheel: $package_source" \
      "$recording_python" -m pip wheel \
      --disable-pip-version-check \
      --no-deps \
      --no-build-isolation \
      --wheel-dir "$wheelhouse" \
      "$source_dir"
  done
  recording_run_operator_venv_step \
    "build reploy operator wheel" \
    "$recording_python" -m pip wheel \
    --disable-pip-version-check \
    --no-cache-dir \
    --wheel-dir "$wheelhouse" \
    "$reploy_package"

  package_requirement="$(recording_wheel_requirement "$wheelhouse" "$arbiter_package")"
  for package_name in "${local_package_names[@]}"; do
    local_package_wheels+=("$(recording_wheel_path "$wheelhouse" "$package_name")")
  done
  recording_prepare_operator_venv_from_wheelhouse \
    "$reploy_package $package_requirement" \
    "$wheelhouse" \
    online \
    "$reploy_package" \
    "${local_package_wheels[@]}"
  export ARBITER_REPO_ROOT="$recording_repo"
  export ARBITER_PYTHON="$recording_python"
}

if [[ "$arbiter_source" == local ]]; then
  if ! recording_prepare_local_operator_venv; then
    return 1
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
  if ! recording_prepare_pypi_operator_venv "$package_requirement"; then
    return 1
  fi
fi

recording_prepare_cli_env() {
  [[ -n "$operator_venv" ]] || {
    printf 'operator venv is not available\n' >&2
    return 1
  }
  recording_operator_venv_is_healthy "$operator_venv" || {
    printf 'operator venv is not healthy: %s\n' "$operator_venv" >&2
    return 1
  }
  ln -sfn "$operator_venv" arbiter_venv
}

recording_prepare_bundle() {
  if [[ "$arbiter_source" == local ]]; then
    local bundle_output
    if ! bundle_output="$(
      {
        ./reploy bundle add-source "$recording_repo/server"
        ./reploy bundle add-source "$recording_repo/plugins/imap"
        ./reploy bundle add-source "$recording_repo/plugins/smtp"
      } 2>&1
    )"; then
      printf '%s\n' "$bundle_output" >&2
      return 1
    fi
  fi
  ./reploy bundle build
}

recording_configure_staging_subnet() {
  :
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
  --host 0.0.0.0 \
  --container-host host.docker.internal \
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
"$operator_venv/bin/arbiter-server" version --json || return 1
"$operator_venv/bin/arbiter" --version || return 1
"$operator_venv/bin/reploy" --version || return 1
}

recording_setup_main
