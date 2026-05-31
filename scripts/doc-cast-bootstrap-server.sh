#!/usr/bin/env bash
set -euo pipefail

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

arbiter-server --config-dir "$tmpdir" bootstrap arbiter
arbiter-server --config-dir "$tmpdir" bootstrap plugin smtp account bot
arbiter-server --config-dir "$tmpdir" config activate account smtp bot
arbiter-server --config-dir "$tmpdir" config show
