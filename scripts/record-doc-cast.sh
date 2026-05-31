#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  printf 'usage: %s NAME COMMAND_FILE\n' "$0" >&2
  exit 2
fi

name="$1"
command_file="$2"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cast_dir="$repo_root/website/static/casts"
cast_path="$cast_dir/$name.cast"

command -v asciinema >/dev/null 2>&1 || {
  printf 'error: asciinema is required to record terminal sessions\n' >&2
  exit 1
}

[[ -f "$command_file" ]] || {
  printf 'error: command file not found: %s\n' "$command_file" >&2
  exit 1
}

mkdir -p "$cast_dir"
asciinema rec --overwrite --command "bash $command_file" "$cast_path"
printf 'wrote %s\n' "$cast_path"
