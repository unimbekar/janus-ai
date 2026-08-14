#!/usr/bin/env bash
# Install host tools for Janus: Terraform, AWS CLI, GitHub CLI, Node 22, and
# the Python/web workspace.
#
# Idempotent. Installs into $HOME/.local — no root required for Terraform/AWS/gh.
# Uses the same Python 3.12 environment as the `venv` alias (dgx-ai-lab).
#
#   ./install.sh              # tools + Janus workspace deps
#   ./install.sh --tools-only # Terraform, AWS CLI, gh, Node — skip uv/npm sync
#
# Afterward:
#   venv                      # Python 3.12 (dgx-ai-lab)
#   aws configure --profile janus
#   See docs/aws-deploy.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

if [[ -t 1 ]] && [[ "${NO_COLOR:-}" != "1" ]]; then
  C_RESET=$'\033[0m' C_BLUE=$'\033[0;34m' C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m' C_RED=$'\033[0;31m' C_DIM=$'\033[2m'
else
  C_RESET='' C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_DIM=''
fi

log()  { printf '%s%-5s%s %s\n' "${C_BLUE}" "INFO" "${C_RESET}" "$*"; }
ok()   { printf '%s%-5s%s %s\n' "${C_GREEN}" "OK" "${C_RESET}" "$*"; }
skip() { printf '%s%-5s%s %s\n' "${C_DIM}" "SKIP" "${C_RESET}" "$*"; }
warn() { printf '%s%-5s%s %s\n' "${C_YELLOW}" "WARN" "${C_RESET}" "$*" >&2; }
die()  { printf '%s%-5s%s %s\n' "${C_RED}" "ERROR" "${C_RESET}" "$*" >&2; exit 1; }
section() { printf '\n%s==> %s%s\n' "${C_BLUE}" "$*" "${C_RESET}"; }

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \?//'
}

have() { command -v "$1" >/dev/null 2>&1; }

version_ge() {
  # True if $1 >= $2 (dotted numeric versions).
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
  # Official installer: prefix under ~/.local, symlinks in ~/.local/bin
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
  make stack-up

  # AWS deploy
  #   docs/aws-deploy.md
EOF
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tools-only) TOOLS_ONLY=1 ;;
      -h | --help) usage; exit 0 ;;
      *) die "Unknown flag: $1 (try --help)" ;;
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

main "$@"
