#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cast_dir="$repo_root/website/static/casts"
image_dir="$repo_root/website/static/img/casts"

command -v agg >/dev/null 2>&1 || {
  printf 'error: agg is required to render asciinema casts\n' >&2
  exit 1
}

mkdir -p "$image_dir"
shopt -s nullglob

for cast_path in "$cast_dir"/*.cast; do
  name="$(basename "$cast_path" .cast)"
  out="$image_dir/$name.svg"
  agg "$cast_path" "$out"
  printf 'wrote %s\n' "$out"
done
