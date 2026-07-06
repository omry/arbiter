staging_dir=""
if [[ -f ./.reploy/state.json ]]; then
  staging_dir="$PWD"
elif [[ -f reploy-staging/.reploy/state.json ]]; then
  staging_dir="$PWD/reploy-staging"
fi

if [[ -f "$staging_dir/arbiterctl" && -x "$staging_dir/arbiterctl" ]]; then
  (
    cd "$staging_dir"
    COMPOSE_PROGRESS=quiet ./arbiterctl down \
      2> >(recording_filter_docker_compose_progress >&2)
  ) || true
fi

if [[ ! -s "$recording_run_failure_path" ]]; then
  rm -rf "$recording_tmp/operator-wheelhouse"

  if [[ -n "$staging_dir" ]]; then
    reploy_cmd=""
    if [[ -x "$HOME/.local/bin/reploy" ]]; then
      reploy_cmd="$HOME/.local/bin/reploy"
    elif command -v reploy >/dev/null 2>&1; then
      reploy_cmd="$(command -v reploy)"
    fi

    if [[ -z "$reploy_cmd" ]]; then
      printf 'reploy not found; cannot clean staging bundle\n' >&2
      exit 1
    fi

    (cd "$staging_dir" && "$reploy_cmd" bundle clean)
  fi
fi
