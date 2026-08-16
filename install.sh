#!/usr/bin/env bash
# =============================================================================
# Janus AI — install host tools and manage the local stack
# =============================================================================
#
# Idempotent tool install into $HOME/.local. Uses the same Python 3.12
# environment as the `venv` alias (dgx-ai-lab) when present.
#
# Usage:
#   ./install.sh                 Install tools + workspace deps (default)
#   ./install.sh install         Same as above
#   ./install.sh --tools-only    Terraform, AWS CLI, gh, Node — skip uv/npm
#   ./install.sh start           Start the full Docker Compose stack
#   ./install.sh stop            Stop Compose stack + host API/gateway/web
#   ./install.sh status          Show containers, ports, and health
#   ./install.sh uninstall       Stop stack; optionally remove images / data
#   ./install.sh help            Show this help
#
# After install:
#   venv                         # Python 3.12 (dgx-ai-lab)
#   aws configure --profile janus
#   See docs/aws-deploy.md
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

BIN_DIR="${JANUS_BIN_DIR:-${HOME}/.local/bin}"
OPT_DIR="${HOME}/.local"
NODE22="${HOME}/.local/node-v22"
# Same path the `venv` alias activates (see ~/.bashrc).
VENV="${JANUS_VENV:-${HOME}/spark-dev-workspace/projects/dgx-ai-lab/.venv}"

TERRAFORM_MIN="1.5.0"
TERRAFORM_DEFAULT="${TERRAFORM_VERSION:-1.11.4}"
AWSCLI_MIN="2.15.0"
GH_MIN="2.40.0"
TOOLS_ONLY=0
COMPOSE=(docker compose)

if [[ -t 1 ]] && [[ "${NO_COLOR:-}" != "1" ]]; then
  C_RESET=$'\033[0m' C_BLUE=$'\033[0;34m' C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m' C_RED=$'\033[0;31m' C_DIM=$'\033[2m' C_BOLD=$'\033[1m'
else
  C_RESET='' C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_DIM='' C_BOLD=''
fi

log()     { printf '%s%-8s%s %s\n' "${C_BLUE}"  "INFO"  "${C_RESET}" "$*"; }
ok()      { printf '%s%-8s%s %s\n' "${C_GREEN}" "OK"    "${C_RESET}" "$*"; }
skip()    { printf '%s%-8s%s %s\n' "${C_DIM}"   "SKIP"  "${C_RESET}" "$*"; }
warn()    { printf '%s%-8s%s %s\n' "${C_YELLOW}" "WARN" "${C_RESET}" "$*" >&2; }
die()     { printf '%s%-8s%s %s\n' "${C_RED}"   "ERROR" "${C_RESET}" "$*" >&2; exit 1; }
section() { printf '\n%s==> %s%s\n' "${C_BOLD}${C_BLUE}" "$*" "${C_RESET}"; }

have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
Janus AI — install tools and manage the local stack

  ./install.sh [command] [options]

Commands:
  install       Install host tools + workspace deps (default)
  start         Start postgres, redis, gateway, api, web (Compose);
                also ensures Ollama + tags from config/local-models.yaml
  ensure-models Ensure Ollama is up and pull tags from config/local-models.yaml
  stop          Stop Compose stack and any host-mode API/gateway/web
  status        Containers, published ports, and health probes
  uninstall     Stop everything; optionally remove images and DB volume
  help          Show this message

Install options:
  --tools-only  Terraform, AWS CLI, gh, Node — skip uv sync / npm install

Uninstall options:
  --yes, -y     Non-interactive (remove Janus Compose images)
  --purge       Also destroy the postgres data volume (destructive)

Examples:
  ./install.sh
  ./install.sh --tools-only
  ./install.sh start
  ./install.sh status
  ./install.sh stop
  ./install.sh uninstall --yes
  ./install.sh uninstall --yes --purge

EOF
}

version_ge() {
  printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1 | grep -qx "$2"
}

arch_pair() {
  case "$(uname -m)" in
    aarch64 | arm64) echo "arm64 aarch64" ;;
    x86_64 | amd64) echo "amd64 x86_64" ;;
    *) die "Unsupported architecture: $(uname -m)" ;;
  esac
}

ensure_path() {
  mkdir -p "${BIN_DIR}"
  case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) export PATH="${BIN_DIR}:${PATH}" ;;
  esac
  if [[ -x "${NODE22}/bin/node" ]]; then
    export PATH="${NODE22}/bin:${PATH}"
  fi
}

load_env() {
  if [[ -f "${ROOT}/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${ROOT}/.env"
    set +a
  fi
  JANUS_API_PORT="${JANUS_API_PORT:-8080}"
  JANUS_WEB_PORT="${JANUS_WEB_PORT:-3000}"
  JANUS_GATEWAY_PORT="${JANUS_GATEWAY_PORT:-8081}"
  JANUS_POSTGRES_PORT="${JANUS_POSTGRES_PORT:-5432}"
  JANUS_REDIS_PORT="${JANUS_REDIS_PORT:-6379}"
}

activate_venv() {
  section "Python 3.12 (dgx-ai-lab / venv alias)"
  if [[ -x "${VENV}/bin/python" ]]; then
    # shellcheck disable=SC1091
    source "${VENV}/bin/activate"
    ok "activated ${VENV}  ($("$(command -v python)" --version 2>&1))"
    return
  fi
  warn "venv not found at ${VENV}"
  warn "Your alias is:  venv='source \$HOME/spark-dev-workspace/projects/dgx-ai-lab/.venv/bin/activate'"
  if have python3; then
    local ver
    ver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [[ "${ver}" == "3.12" ]]; then
      ok "falling back to $(command -v python3) (${ver})"
      return
    fi
  fi
  die "Need Python 3.12. Create the dgx-ai-lab venv, then re-run. Override with JANUS_VENV=..."
}

need_curl() {
  have curl || die "curl is required"
  have unzip || die "unzip is required (sudo apt install unzip)"
}

install_terraform() {
  section "Terraform"
  local tf_arch aws_arch current want
  read -r tf_arch aws_arch <<<"$(arch_pair)"
  if have terraform; then
    current="$(terraform version -json 2>/dev/null | python -c 'import json,sys; print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null \
      || terraform version | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
    if [[ -n "${current}" ]] && version_ge "${current}" "${TERRAFORM_MIN}"; then
      skip "terraform ${current} already on PATH"
      return
    fi
  fi

  want="${TERRAFORM_VERSION:-}"
  if [[ -z "${want}" ]]; then
    want="$(curl -fsSL --retry 3 --retry-delay 1 https://checkpoint-api.hashicorp.com/v1/check/terraform 2>/dev/null \
      | python -c 'import json,sys; print(json.load(sys.stdin)["current_version"])' 2>/dev/null || true)"
  fi
  if [[ -z "${want}" ]]; then
    want="${TERRAFORM_DEFAULT}"
    log "HashiCorp checkpoint unavailable; using Terraform ${want}"
  fi

  local zip="terraform_${want}_linux_${tf_arch}.zip"
  local url="https://releases.hashicorp.com/terraform/${want}/${zip}"
  local tmp
  tmp="$(mktemp -d)"
  log "downloading Terraform ${want} (${tf_arch})"
  curl -fsSL --retry 3 --retry-delay 2 -o "${tmp}/${zip}" "${url}"
  unzip -qo "${tmp}/${zip}" -d "${tmp}"
  install -m 0755 "${tmp}/terraform" "${BIN_DIR}/terraform"
  rm -rf "${tmp}"
  ok "terraform $(terraform version | head -n1) → ${BIN_DIR}/terraform"
}

install_awscli() {
  section "AWS CLI v2"
  if have aws; then
    local current
    current="$(aws --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
    if [[ -n "${current}" ]] && version_ge "${current}" "${AWSCLI_MIN}"; then
      skip "aws ${current} already on PATH"
      return
    fi
  fi

  local tf_arch aws_arch
  read -r tf_arch aws_arch <<<"$(arch_pair)"
  local zip="awscliv2.zip"
  local url="https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip"
  local tmp
  tmp="$(mktemp -d)"
  log "downloading AWS CLI v2 (${aws_arch})"
  curl -fsSL --retry 3 --retry-delay 2 -o "${tmp}/${zip}" "${url}"
  unzip -qo "${tmp}/${zip}" -d "${tmp}"
  "${tmp}/aws/install" --update --install-dir "${OPT_DIR}/aws-cli" --bin-dir "${BIN_DIR}" \
    || "${tmp}/aws/install" --install-dir "${OPT_DIR}/aws-cli" --bin-dir "${BIN_DIR}"
  rm -rf "${tmp}"
  ok "aws $(aws --version 2>&1) → ${BIN_DIR}/aws"
}

install_gh() {
  section "GitHub CLI"
  if have gh; then
    local current
    current="$(gh --version | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
    if [[ -n "${current}" ]] && version_ge "${current}" "${GH_MIN}"; then
      skip "gh ${current} already on PATH"
      return
    fi
  fi

  local tf_arch aws_arch tag tarball
  read -r tf_arch aws_arch <<<"$(arch_pair)"
  tag="$(curl -fsSL --retry 3 https://api.github.com/repos/cli/cli/releases/latest 2>/dev/null \
    | python -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null || true)"
  if [[ -z "${tag}" ]]; then
    tag="v2.74.2"
    log "GitHub API unavailable; using gh ${tag}"
  fi
  tarball="gh_${tag#v}_linux_${tf_arch}.tar.gz"
  local url="https://github.com/cli/cli/releases/download/${tag}/${tarball}"
  local tmp
  tmp="$(mktemp -d)"
  log "downloading GitHub CLI ${tag} (${tf_arch})"
  curl -fsSL --retry 3 --retry-delay 2 -L -o "${tmp}/${tarball}" "${url}"
  tar -xzf "${tmp}/${tarball}" -C "${tmp}"
  install -m 0755 "${tmp}/gh_${tag#v}_linux_${tf_arch}/bin/gh" "${BIN_DIR}/gh"
  rm -rf "${tmp}"
  ok "gh $(gh --version | head -n1) → ${BIN_DIR}/gh"
}

ensure_node22() {
  section "Node.js 22"
  local major=0
  if have node; then
    major="$(node -v | sed 's/^v//' | cut -d. -f1)"
  fi
  if [[ "${major}" -ge 20 ]] && have node && node -e "const [m,n]=process.versions.node.split('.').map(Number); process.exit((m>20||(m===20&&n>=12))?0:1)"; then
    skip "node $(node -v) already usable"
    return
  fi
  if [[ -x "${NODE22}/bin/node" ]]; then
    export PATH="${NODE22}/bin:${PATH}"
    skip "using ${NODE22}/bin/node ($("${NODE22}/bin/node" -v))"
    return
  fi
  if have docker; then
    log "installing Node 22 to ${NODE22} (distro node is too old for Next 16)"
    mkdir -p "${NODE22}"
    docker run --rm --user root -v "${NODE22}:/out" node:22-bookworm-slim \
      bash -c 'tar -C /usr/local -cf - bin/node bin/npm bin/npx bin/corepack lib include share | tar -C /out -xf -'
    export PATH="${NODE22}/bin:${PATH}"
    ok "node $("${NODE22}/bin/node" -v) → ${NODE22}"
    return
  fi
  warn "Node 20.12+ required; distro is $(node -v 2>/dev/null || echo missing) and Docker is not available to install 22"
}

ensure_docker() {
  section "Docker"
  have docker || die "Docker is required. Install it, then re-run."
  docker info >/dev/null 2>&1 || die "Docker daemon is not reachable. Start it, then re-run."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 plugin is required (docker compose)."
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo ok), $(docker compose version --short 2>/dev/null || docker compose version)"
}

ensure_uv() {
  section "uv"
  if have uv; then
    skip "uv $(uv --version | awk '{print $2}') already on PATH"
    return
  fi
  log "installing uv to ${BIN_DIR}"
  curl -fsSL https://astral.sh/uv/install.sh | UV_INSTALL_DIR="${OPT_DIR}" sh
  ensure_path
  ok "uv $(uv --version)"
}

sync_workspace() {
  section "Janus workspace (uv + npm)"
  if [[ ! -f "${ROOT}/pyproject.toml" ]]; then
    die "Run this script from the janus-ai repository root"
  fi
  (cd "${ROOT}" && uv sync)
  if [[ -x "${NODE22}/bin/npm" ]]; then
    (cd "${ROOT}/apps/web" && PATH="${NODE22}/bin:${PATH}" npm install)
  else
    (cd "${ROOT}/apps/web" && npm install)
  fi
  if [[ ! -f "${ROOT}/.env" && -f "${ROOT}/.env.example" ]]; then
    cp "${ROOT}/.env.example" "${ROOT}/.env"
    ok "created .env from .env.example — edit published ports if 3000/8080 are taken"
  else
    skip ".env already present"
  fi
  ok "Python workspace and web dependencies installed"
}

print_summary() {
  section "Installed"
  printf '  %-12s %s\n' "python" "$(python --version 2>&1)  [$(command -v python)]"
  printf '  %-12s %s\n' "terraform" "$(command -v terraform >/dev/null && terraform version | head -n1 || echo missing)"
  printf '  %-12s %s\n' "aws" "$(command -v aws >/dev/null && aws --version 2>&1 || echo missing)"
  printf '  %-12s %s\n' "gh" "$(command -v gh >/dev/null && gh --version | head -n1 || echo missing)"
  printf '  %-12s %s\n' "node" "$(command -v node >/dev/null && node -v || echo missing)  [$(command -v node 2>/dev/null || echo)]"
  printf '  %-12s %s\n' "uv" "$(command -v uv >/dev/null && uv --version || echo missing)"
  printf '  %-12s %s\n' "docker" "$(command -v docker >/dev/null && docker compose version --short 2>/dev/null || echo missing)"

  cat <<EOF

${C_GREEN}Next${C_RESET}
  # Put ~/.local/bin on PATH in this shell (already exported for this process):
  export PATH="${BIN_DIR}:\$PATH"

  # Interactive Python 3.12 — your existing alias:
  venv

  # AWS credentials stay on this machine. Never paste keys into chat or git.
  aws configure --profile janus
  export AWS_PROFILE=janus
  aws sts get-caller-identity

  # Local product
  ./install.sh start
  ./install.sh status
  ./install.sh stop

  # AWS deploy
  #   docs/aws-deploy.md
EOF
}

# ---- stack lifecycle -------------------------------------------------------

stop_host_listeners() {
  # Host-mode: make run-api / run-gateway / run-web (or next start).
  # Scoped patterns only — do not touch unrelated uvicorn/Next apps.
  local killed=0

  if pgrep -f 'uvicorn api_app\.main:app' >/dev/null 2>&1; then
    pkill -f 'uvicorn api_app\.main:app' 2>/dev/null || true
    killed=1
  fi
  if pgrep -f 'uvicorn gateway_app\.main:app' >/dev/null 2>&1; then
    pkill -f 'uvicorn gateway_app\.main:app' 2>/dev/null || true
    killed=1
  fi

  # Anything listening on the configured Janus web port that looks like Next/node.
  if have ss; then
    local pids pid cmd
    pids="$(ss -ltnp "sport = :${JANUS_WEB_PORT}" 2>/dev/null \
      | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
    for pid in ${pids}; do
      [[ -z "${pid}" ]] && continue
      cmd="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
      case "${cmd}" in
        *next*|*node*"apps/web"*|*janus-ai/apps/web*)
          kill "${pid}" 2>/dev/null || true
          # Walk up one parent if it is npm/node wrapping next.
          local ppid
          ppid="$(ps -p "${pid}" -o ppid= 2>/dev/null | tr -d ' ' || true)"
          if [[ -n "${ppid}" && "${ppid}" != "1" ]]; then
            local pcmd
            pcmd="$(ps -p "${ppid}" -o args= 2>/dev/null || true)"
            case "${pcmd}" in
              *npm*|*next*|*node*) kill "${ppid}" 2>/dev/null || true ;;
            esac
          fi
          killed=1
          ;;
      esac
    done
  fi

  if [[ "${killed}" -eq 1 ]]; then
    sleep 1
    ok "Stopped host-mode API / gateway / web processes"
  else
    skip "No host-mode Janus listeners found"
  fi
}

cmd_install() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tools-only) TOOLS_ONLY=1 ;;
      -h | --help) usage; exit 0 ;;
      *) die "Unknown install option: $1 (try ./install.sh help)" ;;
    esac
    shift
  done

  ensure_path
  need_curl
  activate_venv
  ensure_uv
  ensure_docker
  ensure_node22
  install_terraform
  install_awscli
  install_gh
  if [[ "${TOOLS_ONLY}" != "1" ]]; then
    sync_workspace
  fi
  print_summary
}

# ---- local models (Ollama) -------------------------------------------------

ollama_host_base() {
  load_env
  local url="${JANUS_OLLAMA_BASE_URL:-http://127.0.0.1:11434/v1}"
  # Strip trailing /v1 for native Ollama API.
  echo "${url%/v1}"
}

list_local_ollama_tags() {
  # Union of config/local-models.yaml (pull: true) and JANUS_LOCAL_OLLAMA_MODELS.
  # Previously env replaced YAML entirely, which skipped newly uncommented models.
  local cfg="${ROOT}/config/local-models.yaml"
  local py="python3"
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    py="${ROOT}/.venv/bin/python"
  elif [[ -x "${VENV}/bin/python" ]]; then
    py="${VENV}/bin/python"
  fi
  local tags=""
  tags="$(
    {
      if [[ -f "${cfg}" ]]; then
        "${py}" - "${cfg}" <<'PY' 2>/dev/null || true
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    sys.exit(0)
data = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
for row in (data.get("ollama") or {}).get("models") or []:
    tag = (row or {}).get("tag")
    if tag and (row.get("pull", True) is not False):
        print(tag)
PY
      fi
      if [[ -n "${JANUS_LOCAL_OLLAMA_MODELS:-}" ]]; then
        echo "${JANUS_LOCAL_OLLAMA_MODELS}" | tr ',;' ' ' | xargs -n1 echo
      fi
    } | awk 'NF && !seen[$0]++'
  )"
  if [[ -z "${tags}" ]]; then
    echo "nemotron35lightning:latest"
  else
    printf '%s\n' "${tags}"
  fi
}

ensure_ollama() {
  section "Local models (Ollama)"
  load_env
  local base
  base="$(ollama_host_base)"

  # Compose reaches the host via host.docker.internal. That fails if Ollama only
  # listens on 127.0.0.1 — bind all interfaces for local Docker stacks.
  local want_host="${JANUS_OLLAMA_LISTEN:-0.0.0.0:11434}"

  start_ollama_for_compose() {
    if have systemctl && systemctl cat ollama.service >/dev/null 2>&1; then
      if sudo -n true 2>/dev/null; then
        sudo mkdir -p /etc/systemd/system/ollama.service.d
        printf '%s\n' '[Service]' "Environment=\"OLLAMA_HOST=${want_host}\"" \
          | sudo tee /etc/systemd/system/ollama.service.d/janus-docker.conf >/dev/null
        sudo systemctl daemon-reload
        sudo systemctl restart ollama
        return 0
      fi
    fi
    # Manual / user-started ollama (common on this host).
    pkill -x ollama 2>/dev/null || true
    sleep 1
    nohup env OLLAMA_HOST="${want_host}" ollama serve >/tmp/janus-ollama.log 2>&1 &
    return 0
  }

  if ! curl -sf --max-time 2 "${base}/api/tags" >/dev/null 2>&1; then
    if have ollama; then
      warn "Ollama not responding at ${base} — starting with OLLAMA_HOST=${want_host}"
      start_ollama_for_compose
    else
      die "Ollama is not installed or not reachable at ${base}. Install Ollama, then re-run."
    fi
  else
    # Reachable on loopback, but Compose needs a non-loopback listen address.
    if ss -ltn "( sport = :11434 )" 2>/dev/null | grep -q '127.0.0.1:11434'; then
      if ! ss -ltn "( sport = :11434 )" 2>/dev/null | grep -qE '0\.0\.0\.0:11434|\*:11434|\[::\]:11434'; then
        warn "Ollama is loopback-only; rebinding to ${want_host} so Docker can reach it"
        start_ollama_for_compose
      fi
    fi
  fi

  local i=0
  until curl -sf --max-time 1 "${base}/api/tags" >/dev/null 2>&1; do
    i=$((i + 1))
    if [[ "${i}" -gt 45 ]]; then
      die "Ollama did not become ready. Check: ollama serve  (log: /tmp/janus-ollama.log)"
    fi
    sleep 1
  done
  ok "Ollama reachable at ${base}"

  # Confirm Docker-bridge path when possible (best-effort).
  if have docker && docker info >/dev/null 2>&1; then
    if ! ss -ltn "( sport = :11434 )" 2>/dev/null | grep -qE '0\.0\.0\.0:11434|\*:11434'; then
      warn "Ollama may still be loopback-only — local models can stay hidden in Compose"
    else
      ok "Ollama listening for Compose (OLLAMA_HOST=${want_host})"
    fi
  fi

  local tag present
  present="$(curl -sf --max-time 5 "${base}/api/tags" \
    | python3 -c 'import sys,json; print("\n".join(m["name"] for m in json.load(sys.stdin).get("models",[])))' \
    2>/dev/null || true)"

  while IFS= read -r tag; do
    [[ -z "${tag}" ]] && continue
    if printf '%s\n' "${present}" | grep -qxF "${tag}" \
      || printf '%s\n' "${present}" | grep -qxF "${tag%:latest}"; then
      ok "Model present: ${tag}"
      continue
    fi
    if have ollama; then
      log "Pulling ${tag} (may take a long time)…"
      if ollama pull "${tag}"; then
        ok "Pulled ${tag}"
      else
        warn "Failed to pull ${tag} — catalog may hide it until the pull succeeds"
      fi
    else
      warn "Missing ${tag} and \`ollama\` CLI not on PATH — pull it manually"
    fi
  done < <(list_local_ollama_tags)
}

cmd_start() {
  ensure_docker
  load_env
  section "Start Janus stack"
  if [[ ! -f "${ROOT}/.env" && -f "${ROOT}/.env.example" ]]; then
    cp "${ROOT}/.env.example" "${ROOT}/.env"
    ok "created .env from .env.example"
    load_env
  fi

  ensure_ollama

  stop_host_listeners
  # Compose must reach host Ollama via host.docker.internal (see docker-compose.yml).
  export JANUS_OLLAMA_COMPOSE_URL="${JANUS_OLLAMA_COMPOSE_URL:-http://host.docker.internal:11434/v1}"
  make -C "${ROOT}" stack-up
  # Rebind/ensure of Ollama can leave an already-running gateway with stale OFFLINE
  # health for local deployments — recreate it so probes see the host again.
  log "Refreshing gateway so local model health is current"
  "${COMPOSE[@]}" --profile full up -d --force-recreate --no-deps gateway >/dev/null
  ok "Stack starting"
  log "Web  http://localhost:${JANUS_WEB_PORT}"
  log "API  http://localhost:${JANUS_API_PORT}"
  log "Local models: config/local-models.yaml + registry/environments/local.yaml"
  log "Status: ./install.sh status"
}

cmd_stop() {
  load_env
  section "Stop Janus"

  stop_host_listeners

  if have docker && docker info >/dev/null 2>&1; then
    log "Stopping Docker Compose stack (profile full)"
    if "${COMPOSE[@]}" --profile full down; then
      ok "Compose stack stopped"
    else
      warn "Compose down reported an error — check docker compose ps"
    fi
  else
    warn "Docker not reachable; skipped Compose down"
  fi

  ok "Stop complete"
  log "Postgres data volume kept. To wipe it: ./install.sh uninstall --purge"
}

probe() {
  local name="$1" url="$2"
  if curl -sf --max-time 2 "${url}" >/dev/null 2>&1; then
    ok "${name}  ${url}"
  else
    warn "${name}  not ready  ${url}"
  fi
}

cmd_status() {
  load_env
  section "Status"

  if have docker && docker info >/dev/null 2>&1; then
    "${COMPOSE[@]}" --profile full ps -a 2>/dev/null || "${COMPOSE[@]}" ps -a
  else
    warn "Docker not reachable"
  fi

  echo
  log "Configured ports — web ${JANUS_WEB_PORT}, api ${JANUS_API_PORT}, gateway ${JANUS_GATEWAY_PORT}"
  if have ss; then
    ss -ltn "sport = :${JANUS_WEB_PORT}" "sport = :${JANUS_API_PORT}" \
      "sport = :${JANUS_GATEWAY_PORT}" "sport = :${JANUS_POSTGRES_PORT}" \
      "sport = :${JANUS_REDIS_PORT}" 2>/dev/null \
      | awk 'NR==1 || /LISTEN/' || true
  fi

  echo
  probe "web"     "http://127.0.0.1:${JANUS_WEB_PORT}/"
  probe "api"     "http://127.0.0.1:${JANUS_API_PORT}/healthz"
  probe "gateway" "http://127.0.0.1:${JANUS_GATEWAY_PORT}/healthz"

  if pgrep -f 'uvicorn api_app\.main:app' >/dev/null 2>&1 \
    || pgrep -f 'uvicorn gateway_app\.main:app' >/dev/null 2>&1; then
    log "Host-mode uvicorn processes are running (make run-api / run-gateway)"
  fi
}

cmd_uninstall() {
  local purge=0 yes=0
  for arg in "$@"; do
    case "${arg}" in
      --purge) purge=1 ;;
      --yes|-y) yes=1 ;;
      *) die "Unknown uninstall option: ${arg}" ;;
    esac
  done

  load_env
  section "Uninstall"

  cmd_stop

  local remove_images=0
  if [[ "${yes}" -eq 1 ]]; then
    remove_images=1
  else
    read -r -p "Remove Janus Docker images (janus/api:dev, janus/gateway:dev, janus/web:dev)? [y/N] " reply || true
    case "${reply}" in
      y|Y|yes|YES) remove_images=1 ;;
    esac
  fi

  if [[ "${remove_images}" -eq 1 ]]; then
    for img in janus/api:dev janus/gateway:dev janus/web:dev; do
      if docker image inspect "${img}" >/dev/null 2>&1; then
        docker rmi "${img}" >/dev/null 2>&1 && ok "Removed ${img}" || warn "Could not remove ${img}"
      else
        skip "Image ${img} not present"
      fi
    done
  else
    skip "Kept Janus Docker images"
  fi

  if [[ "${purge}" -eq 1 ]]; then
    if [[ "${yes}" -eq 1 ]]; then
      "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
      ok "Removed Compose volumes (including postgres data)"
    else
      read -r -p "Destroy postgres data volume? This deletes all local Janus data. [y/N] " reply || true
      case "${reply}" in
        y|Y|yes|YES)
          "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
          ok "Removed Compose volumes (including postgres data)"
          ;;
        *)
          skip "Kept postgres data volume"
          ;;
      esac
    fi
  else
    log "Database volume kept. Use --purge to destroy it."
  fi

  log ".env was not deleted. Remove manually if desired: rm .env"
  log "Host tools (terraform/aws/gh/node under ~/.local) were not removed."
  ok "Uninstall complete"
}

main() {
  local cmd="${1:-install}"

  # Backward compatible: ./install.sh --tools-only
  if [[ "${cmd}" == --* ]]; then
    case "${cmd}" in
      -h|--help) usage; exit 0 ;;
      --tools-only) cmd_install "$@"; return ;;
      *) die "Unknown flag: ${cmd} (try ./install.sh help)" ;;
    esac
  fi

  shift || true

  case "${cmd}" in
    install|bootstrap) cmd_install "$@" ;;
    start|up)          cmd_start "$@" ;;
    ensure-models|pull-models) ensure_ollama "$@" ;;
    stop|down)         cmd_stop "$@" ;;
    status|health)     cmd_status "$@" ;;
    uninstall|remove)  cmd_uninstall "$@" ;;
    help|-h|--help)    usage ;;
    *)
      usage >&2
      die "Unknown command: ${cmd}"
      ;;
  esac
}

main "$@"
