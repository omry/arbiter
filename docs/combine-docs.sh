#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$script_dir/.." && pwd)
outfile=${1:-"$script_dir/agent_arbiter_design_docs.md"}

docs=(
  "$script_dir/overview.md"
  "$script_dir/architecture.md"
  "$script_dir/BACKLOG.md"
  "$script_dir/config.md"
  "$script_dir/policies.md"
  "$script_dir/errors.md"
  "$script_dir/todo.md"
  "$script_dir/tools/account_summaries.md"
  "$script_dir/tools/smtp_send_email.md"
  "$script_dir/tools/imap.md"
  "$script_dir/testing_backlog.md"
)

{
  printf '# Agent Arbiter Design Docs Bundle\n\n'
  printf 'Generated from repo docs in reading order.\n\n'
  printf '## Included Files\n\n'

  for i in "${!docs[@]}"; do
    rel_path=${docs[$i]#"$project_dir"/}
    printf '%d. %s\n' "$((i + 1))" "$rel_path"
  done

  for f in "${docs[@]}"; do
    rel_path=${f#"$project_dir"/}
    printf '\n\n---\n\n# File: %s\n\n' "$rel_path"
    sed -n '1,$p' "$f"
  done
} > "$outfile"

printf 'Wrote %s\n' "$outfile"
