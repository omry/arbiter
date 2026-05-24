#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ACTION="install"
DEFAULT_CONTAINER="openclaw"
DEFAULT_SKILL_DIR="/home/node/.openclaw/skills"
DEFAULT_GITHUB_REPO="omry/mail-sentry"
DEFAULT_REF="main"
DEFAULT_SOURCE_MODE="auto"

ACTION="${DEFAULT_ACTION}"
CONTAINER_NAME="${DEFAULT_CONTAINER}"
SKILL_DIR="${DEFAULT_SKILL_DIR}"
GITHUB_REPO="${DEFAULT_GITHUB_REPO}"
GIT_REF="${DEFAULT_REF}"
SOURCE_MODE="${DEFAULT_SOURCE_MODE}"

usage() {
  cat <<'EOF'
Usage:
  install-openclaw-skills.sh [install|uninstall] [options]

Options:
  --container <name>   Target OpenClaw container name. Default: openclaw
  --skill-dir <path>   Skill root inside the container. Default: /home/node/.openclaw/skills
  --ref <git-ref>      GitHub tag/branch/commit to install from. Default: main
  --source <mode>      Source mode: auto, local, or github. Default: auto
  --help               Show this help message.

Examples:
  install-openclaw-skills.sh install --source local
  install-openclaw-skills.sh install --ref 8475ab2 --source github
  install-openclaw-skills.sh uninstall
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

container_running() {
  docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -qx 'true'
}

docker_exec_node() {
  docker exec -u node "$CONTAINER_NAME" sh -lc "$1"
}

docker_exec_root() {
  docker exec -u 0 "$CONTAINER_NAME" sh -lc "$1"
}

ensure_container_ready() {
  docker inspect "$CONTAINER_NAME" >/dev/null 2>&1 || fail "container not found: $CONTAINER_NAME"
  container_running "$CONTAINER_NAME" || fail "container is not running: $CONTAINER_NAME"
  docker exec "$CONTAINER_NAME" python3 --version >/dev/null 2>&1 || fail "python3 is not available in container: $CONTAINER_NAME"
}

ensure_skill_dir() {
  docker_exec_node "mkdir -p '$SKILL_DIR'"
}

ensure_pip() {
  if docker exec "$CONTAINER_NAME" python3 -m pip --version >/dev/null 2>&1; then
    return
  fi

  echo "Installing python3-pip inside container $CONTAINER_NAME..." >&2
  docker_exec_root "apt-get update && apt-get install -y python3-pip"
}

install_python_dependencies() {
  echo "Installing Python dependencies inside container $CONTAINER_NAME..." >&2
  docker_exec_root "python3 -m pip install --break-system-packages httpx mcp"
}

local_skill_tree() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if [ -d "${script_dir}/_shared" ] && [ -d "${script_dir}/send-email-interactive" ] && [ -d "${script_dir}/send-email-predefined" ]; then
    printf '%s\n' "$script_dir"
    return 0
  fi

  return 1
}

download_skill_tree() {
  local tmp_dir="$1"
  local archive_path="${tmp_dir}/repo.tar.gz"
  local extract_dir="${tmp_dir}/extract"
  local tarball_url="https://codeload.github.com/${GITHUB_REPO}/tar.gz/${GIT_REF}"

  require_command curl
  echo "Downloading skills from ${GITHUB_REPO}@${GIT_REF}..." >&2
  curl -fsSL "$tarball_url" -o "$archive_path"
  mkdir -p "$extract_dir"
  tar -xzf "$archive_path" -C "$extract_dir"

  local source_root
  source_root="$(find "$extract_dir" -type d -name openclaw_skills -print -quit)"
  [ -n "$source_root" ] || fail "could not locate openclaw_skills in downloaded archive"

  printf '%s\n' "$source_root"
}

resolve_skill_tree() {
  local tmp_dir="$1"
  local local_root=""

  if local_root="$(local_skill_tree 2>/dev/null)"; then
    case "$SOURCE_MODE" in
      auto|local)
        echo "Using local skill tree from ${local_root}..." >&2
        printf '%s\n' "$local_root"
        return 0
        ;;
    esac
  fi

  case "$SOURCE_MODE" in
    auto|github)
      download_skill_tree "$tmp_dir"
      return 0
      ;;
    local)
      fail "local source mode requested, but no local openclaw_skills tree was found next to the installer script"
      ;;
    *)
      fail "unsupported source mode: $SOURCE_MODE"
      ;;
  esac
}

copy_skill_dir() {
  local source_root="$1"
  local skill_name="$2"

  [ -d "${source_root}/${skill_name}" ] || fail "missing skill directory in source tree: ${skill_name}"

  docker_exec_node "rm -rf '$SKILL_DIR/$skill_name'"
  tar -C "$source_root" -cf - "$skill_name" \
    | docker exec -i -u node "$CONTAINER_NAME" sh -lc "tar -xf - -C '$SKILL_DIR'"
}

install_skills() {
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap "rm -rf '$tmp_dir'" EXIT

  local source_root
  source_root="$(resolve_skill_tree "$tmp_dir")"

  ensure_skill_dir
  ensure_pip
  install_python_dependencies

  copy_skill_dir "$source_root" "_shared"
  copy_skill_dir "$source_root" "send-email-interactive"
  copy_skill_dir "$source_root" "send-email-predefined"

  docker_exec_node "test -f '$SKILL_DIR/_shared/scripts/mail_sentry_client.py'"
  docker_exec_node "test -f '$SKILL_DIR/send-email-interactive/SKILL.md'"
  docker_exec_node "test -f '$SKILL_DIR/send-email-predefined/SKILL.md'"
  trap - EXIT

  echo "Installed Mail Sentry OpenClaw skills into ${CONTAINER_NAME}:${SKILL_DIR}"
  echo "Built-in OpenClaw skills under /app/skills were not modified."
}

uninstall_skills() {
  docker_exec_node "rm -rf '$SKILL_DIR/_shared' '$SKILL_DIR/send-email-interactive' '$SKILL_DIR/send-email-predefined'"
  echo "Removed Mail Sentry OpenClaw skill files from ${CONTAINER_NAME}:${SKILL_DIR}"
  echo "Python dependencies were left installed."
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      install|uninstall)
        ACTION="$1"
        ;;
      --container)
        shift
        [ "$#" -gt 0 ] || fail "--container requires a value"
        CONTAINER_NAME="$1"
        ;;
      --skill-dir)
        shift
        [ "$#" -gt 0 ] || fail "--skill-dir requires a value"
        SKILL_DIR="$1"
        ;;
      --ref)
        shift
        [ "$#" -gt 0 ] || fail "--ref requires a value"
        GIT_REF="$1"
        ;;
      --source)
        shift
        [ "$#" -gt 0 ] || fail "--source requires a value"
        SOURCE_MODE="$1"
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        fail "unknown argument: $1"
        ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"

  require_command docker
  require_command tar
  require_command find
  ensure_container_ready

  case "$ACTION" in
    install)
      install_skills
      ;;
    uninstall)
      uninstall_skills
      ;;
    *)
      fail "unsupported action: $ACTION"
      ;;
  esac
}

main "$@"
