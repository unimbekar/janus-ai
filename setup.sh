#!/usr/bin/env bash
# =============================================================================
# Janus Intelligence — one-step setup (AWS Marketplace / self-serve)
# =============================================================================
#
# Download the product package, then from the repository root:
#
#   chmod +x setup.sh
#   ./setup.sh
#
# The wizard asks a few questions (or use flags for a silent run), installs
# tools, configures the environment, and starts Janus.
#
# Modes
#   local   Docker Compose on this machine (demo / eval / air-gapped prep)
#   aws     Deploy into your AWS account (ECS Fargate + Aurora)
#   tools   Install CLIs only (Terraform, AWS CLI, Node, uv) — no stack start
#
# Non-interactive examples
#   ./setup.sh --local --yes
#   ./setup.sh --aws --yes --aws-region us-east-1 --aws-account 123456789012
#   ./setup.sh --tools --yes
#
# Never paste AWS access keys into chat, tickets, or git. Use `aws configure`
# or IAM roles. See docs/aws-deploy.md and docs/marketplace.md.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

BIN_DIR="${JANUS_BIN_DIR:-${HOME}/.local/bin}"
OPT_DIR="${HOME}/.local"
NODE22="${HOME}/.local/node-v22"
VENV_DIR="${JANUS_VENV:-${ROOT}/.venv}"

MODE=""
ASSUME_YES=0
START_STACK=1
RUN_SMOKE=1
APPLY_TERRAFORM=0
AWS_REGION_DEFAULT="us-east-1"
AWS_ACCOUNT_ID_ARG=""
AWS_REGION_ARG=""
WEB_PORT_ARG=""
API_PORT_ARG=""
GATEWAY_PORT_ARG=""
BIND_ARG=""
SKIP_DOCKER_CHECK=0

if [[ -t 1 ]] && [[ "${NO_COLOR:-}" != "1" ]]; then
  C_RESET=$'\033[0m' C_BLUE=$'\033[0;34m' C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m' C_RED=$'\033[0;31m' C_DIM=$'\033[2m' C_BOLD=$'\033[1m'
else
  C_RESET='' C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_DIM='' C_BOLD=''
fi

log()     { printf '%s%-5s%s %s\n' "${C_BLUE}" "INFO" "${C_RESET}" "$*"; }
ok()      { printf '%s%-5s%s %s\n' "${C_GREEN}" "OK" "${C_RESET}" "$*"; }
skip()    { printf '%s%-5s%s %s\n' "${C_DIM}" "SKIP" "${C_RESET}" "$*"; }
warn()    { printf '%s%-5s%s %s\n' "${C_YELLOW}" "WARN" "${C_RESET}" "$*" >&2; }
die()     { printf '%s%-5s%s %s\n' "${C_RED}" "ERROR" "${C_RESET}" "$*" >&2; exit 1; }
section() { printf '\n%s==> %s%s\n' "${C_BLUE}" "$*" "${C_RESET}"; }
banner()  {
  printf '\n%s' "${C_BOLD}"
  cat <<'EOF'
     ┌──────────────────────────────────────────┐
     │         Janus Intelligence setup         │
     │   One AI interface. Every model.         │
     └──────────────────────────────────────────┘
EOF
  printf '%s\n' "${C_RESET}"
}

have() { command -v "$1" >/dev/null 2>&1; }

version_ge() {
  printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1 | grep -qx "$2"
}

usage() {
  cat <<EOF
Janus Intelligence — one-step setup (AWS Marketplace / self-serve)

  chmod +x setup.sh && ./setup.sh

Modes
  local   Docker Compose on this machine (demo / eval)
  aws     Deploy into your AWS account (ECS Fargate + Aurora)
  tools   Install CLIs only — do not start the stack

Examples
  ./setup.sh
  ./setup.sh --local --yes
  ./setup.sh --aws --yes --aws-region us-east-1 --aws-account 123456789012
  ./setup.sh --aws --apply          # plan + apply (costs money)
  ./setup.sh --tools --yes

Flags
  --local                 Docker Compose on this machine
  --aws                   Prepare / deploy into your AWS account
  --tools                 Install host tools only
  --yes, -y               Accept defaults; minimal prompts
  --no-start              Do not run make stack-up (local)
  --no-smoke              Skip smoke tests after local start
  --apply                 After AWS prep, run terraform apply
  --aws-account ID        12-digit AWS account id
  --aws-region REGION     Default us-east-1
  --web-port N            Published web port (default 3000)
  --api-port N            Published API port (default 8080)
  --gateway-port N        Published gateway port (default 8081)
  --bind ADDR             Bind address (default 0.0.0.0)
  -h, --help              Show this help

Never paste AWS access keys into chat, tickets, or git.
See docs/aws-deploy.md and docs/marketplace.md.
EOF
}

is_tty() { [[ -t 0 ]] && [[ "${ASSUME_YES}" != "1" ]]; }

ask() {
  local prompt="$1"
  local default="${2:-}"
  if ! is_tty; then
    REPLY="${default}"
    return
  fi
  if [[ -n "${default}" ]]; then
    read -r -p "${prompt} [${default}]: " REPLY || true
    REPLY="${REPLY:-${default}}"
  else
    read -r -p "${prompt}: " REPLY || true
  fi
}

confirm() {
  local prompt="$1"
  local default="${2:-y}"
  # --yes accepts the default answer (so destructive prompts with default "n" stay no).
  if [[ "${ASSUME_YES}" == "1" ]] || ! is_tty; then
    [[ "${default}" == "y" || "${default}" == "Y" ]]
    return
  fi
  local hint="y/N"
  [[ "${default}" == "y" || "${default}" == "Y" ]] && hint="Y/n"
  read -r -p "${prompt} [${hint}]: " REPLY || true
  REPLY="${REPLY:-${default}}"
  [[ "${REPLY}" == "y" || "${REPLY}" == "Y" || "${REPLY}" == "yes" ]]
}

choose_mode() {
  if [[ -n "${MODE}" ]]; then
    return
  fi
  if ! is_tty; then
    MODE="local"
    return
  fi
  section "Where should Janus run?"
  cat <<EOF
  1) Local machine  — Docker Compose (fastest eval / demo)
  2) AWS account    — ECS Fargate + Aurora (your VPC)
  3) Tools only     — Install CLIs; do not start anything
EOF
  ask "Choose 1, 2, or 3" "1"
  case "${REPLY}" in
    1|local|Local) MODE="local" ;;
    2|aws|AWS)     MODE="aws" ;;
    3|tools|Tools) MODE="tools" ;;
    *) die "Invalid choice: ${REPLY}" ;;
  esac
}

arch_pair() {
  case "$(uname -m)" in
    aarch64 | arm64) echo "arm64 aarch64" ;;
    x86_64 | amd64) echo "amd64 x86_64" ;;
    *) die "Unsupported architecture: $(uname -m). Need x86_64 or arm64." ;;
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
  local rc="${HOME}/.bashrc"
  local marker="# Janus Intelligence PATH"
  if [[ -f "${rc}" ]] && ! grep -qF "${marker}" "${rc}" 2>/dev/null; then
    cat >>"${rc}" <<EOF

${marker}
export PATH="\${HOME}/.local/bin:\${PATH}"
[[ -x "\${HOME}/.local/node-v22/bin/node" ]] && export PATH="\${HOME}/.local/node-v22/bin:\${PATH}"
EOF
    ok "added ~/.local/bin to PATH in ~/.bashrc (new shells)"
  fi
}

need_curl() {
  have curl || die "curl is required. Install it (e.g. sudo apt install curl) and re-run."
  have unzip || die "unzip is required. Install it (e.g. sudo apt install unzip) and re-run."
}

port_free() {
  local port="$1"
  if have ss; then
    ! ss -ltn "sport = :${port}" 2>/dev/null | grep -q ":${port}"
  elif have lsof; then
    ! lsof -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 0
  fi
}

pick_free_port() {
  local preferred="$1"
  local candidate="${preferred}"
  local i=0
  while ! port_free "${candidate}"; do
    candidate=$((preferred + 10 + i))
    i=$((i + 1))
    if [[ "${i}" -gt 40 ]]; then
      echo "${preferred}"
      return
    fi
  done
  echo "${candidate}"
}

ensure_docker() {
  section "Docker"
  if [[ "${SKIP_DOCKER_CHECK}" == "1" ]]; then
    skip "Docker check skipped"
    return 0
  fi
  if ! have docker; then
    warn "Docker is not installed."
    if confirm "Attempt to install Docker Engine via the official convenience script? (needs sudo)" "y"; then
      need_curl
      curl -fsSL https://get.docker.com | sudo sh
      sudo usermod -aG docker "${USER}" || true
      warn "Added ${USER} to the docker group. You may need to log out/in, or run: newgrp docker"
    else
      die "Install Docker Desktop or Docker Engine, then re-run ./setup.sh"
    fi
  fi
  if ! docker info >/dev/null 2>&1; then
    if have systemctl; then
      warn "Starting Docker daemon…"
      sudo systemctl start docker || true
    fi
  fi
  docker info >/dev/null 2>&1 || die "Docker daemon is not reachable. Start Docker, then re-run."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (docker compose)."
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo ok), compose $(docker compose version --short 2>/dev/null || echo ok)"
}

ensure_uv() {
  section "uv (Python package manager)"
  if have uv; then
    skip "uv $(uv --version | awk '{print $2}') already on PATH"
    return
  fi
  need_curl
  log "installing uv into ${OPT_DIR}"
  curl -fsSL https://astral.sh/uv/install.sh | UV_INSTALL_DIR="${OPT_DIR}" sh
  ensure_path
  have uv || die "uv installed but not on PATH; export PATH=${BIN_DIR}:\$PATH"
  ok "uv $(uv --version)"
}

ensure_python_venv() {
  section "Python environment"
  ensure_uv
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    ok "using ${VENV_DIR} ($("${VENV_DIR}/bin/python" --version 2>&1))"
    return
  fi
  log "creating ${VENV_DIR} with Python 3.12"
  if ! uv python find 3.12 >/dev/null 2>&1; then
    log "installing Python 3.12 via uv…"
    uv python install 3.12
  fi
  uv venv --python 3.12 "${VENV_DIR}"
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  ok "created ${VENV_DIR} ($(python --version 2>&1))"
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
  need_curl
  local tf_arch aws_arch
  read -r tf_arch aws_arch <<<"$(arch_pair)"
  local ver="v22.14.0"
  local tarball="node-${ver}-linux-${tf_arch}.tar.xz"
  local url="https://nodejs.org/dist/${ver}/${tarball}"
  local tmp
  tmp="$(mktemp -d)"
  log "downloading Node ${ver} (${tf_arch})"
  if curl -fsSL --retry 3 --retry-delay 2 -o "${tmp}/${tarball}" "${url}"; then
    mkdir -p "${NODE22}"
    tar -xJf "${tmp}/${tarball}" -C "${tmp}"
    local extracted
    extracted="$(echo "${tmp}"/node-v22*)"
    cp -a "${extracted}/." "${NODE22}/"
    rm -rf "${tmp}"
    export PATH="${NODE22}/bin:${PATH}"
    ok "node $("${NODE22}/bin/node" -v) → ${NODE22}"
    return
  fi
  rm -rf "${tmp}"
  if have docker; then
    log "Node download failed; falling back to Docker image extract"
    mkdir -p "${NODE22}"
    docker run --rm --user root -v "${NODE22}:/out" node:22-bookworm-slim \
      bash -c 'tar -C /usr/local -cf - bin/node bin/npm bin/npx bin/corepack lib include share | tar -C /out -xf -'
    export PATH="${NODE22}/bin:${PATH}"
    ok "node $("${NODE22}/bin/node" -v) → ${NODE22}"
    return
  fi
  die "Need Node.js 20.12+ (22 recommended). Install from https://nodejs.org and re-run."
}

install_terraform() {
  section "Terraform"
  local tf_arch aws_arch current want
  read -r tf_arch aws_arch <<<"$(arch_pair)"
  if have terraform; then
    current="$(terraform version -json 2>/dev/null | python -c 'import json,sys; print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null \
      || terraform version | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
    if [[ -n "${current}" ]] && version_ge "${current}" "1.5.0"; then
      skip "terraform ${current} already on PATH"
      return
    fi
  fi
  need_curl
  want="$(curl -fsSL --retry 2 https://checkpoint-api.hashicorp.com/v1/check/terraform 2>/dev/null \
    | python -c 'import json,sys; print(json.load(sys.stdin)["current_version"])' 2>/dev/null || true)"
  [[ -z "${want}" ]] && want="1.11.4"
  local zip="terraform_${want}_linux_${tf_arch}.zip"
  local tmp
  tmp="$(mktemp -d)"
  log "downloading Terraform ${want}"
  curl -fsSL --retry 3 --retry-delay 2 -o "${tmp}/${zip}" \
    "https://releases.hashicorp.com/terraform/${want}/${zip}"
  unzip -qo "${tmp}/${zip}" -d "${tmp}"
  install -m 0755 "${tmp}/terraform" "${BIN_DIR}/terraform"
  rm -rf "${tmp}"
  ok "terraform $(terraform version | head -n1)"
}

install_awscli() {
  section "AWS CLI v2"
  if have aws; then
    local current
    current="$(aws --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
    if [[ -n "${current}" ]] && version_ge "${current}" "2.15.0"; then
      skip "aws ${current} already on PATH"
      return
    fi
  fi
  need_curl
  local tf_arch aws_arch
  read -r tf_arch aws_arch <<<"$(arch_pair)"
  local tmp
  tmp="$(mktemp -d)"
  log "downloading AWS CLI v2 (${aws_arch})"
  curl -fsSL --retry 3 --retry-delay 2 -o "${tmp}/awscliv2.zip" \
    "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip"
  unzip -qo "${tmp}/awscliv2.zip" -d "${tmp}"
  "${tmp}/aws/install" --update --install-dir "${OPT_DIR}/aws-cli" --bin-dir "${BIN_DIR}" \
    || "${tmp}/aws/install" --install-dir "${OPT_DIR}/aws-cli" --bin-dir "${BIN_DIR}"
  rm -rf "${tmp}"
  ok "aws $(aws --version 2>&1)"
}

sync_workspace() {
  section "Install Janus application dependencies"
  ensure_python_venv
  ensure_node22
  (cd "${ROOT}" && uv sync)
  if [[ -x "${NODE22}/bin/npm" ]]; then
    (cd "${ROOT}/apps/web" && PATH="${NODE22}/bin:${PATH}" npm install)
  else
    (cd "${ROOT}/apps/web" && npm install)
  fi
  ok "Python workspace + web dependencies ready"
}

upsert_env() {
  local key="$1" val="$2" file="${ROOT}/.env"
  if grep -qE "^#?${key}=" "${file}"; then
    sed -i -E "s|^#?${key}=.*|${key}=${val}|" "${file}"
  else
    printf '\n%s=%s\n' "${key}" "${val}" >>"${file}"
  fi
}

configure_env_local() {
  section "Local configuration (.env)"
  [[ -f "${ROOT}/.env.example" ]] || die "Missing .env.example — is this the Janus repository root?"

  local web api gw bind
  web="${WEB_PORT_ARG:-$(pick_free_port 3000)}"
  api="${API_PORT_ARG:-$(pick_free_port 8080)}"
  gw="${GATEWAY_PORT_ARG:-$(pick_free_port 8081)}"
  bind="${BIND_ARG:-0.0.0.0}"

  if is_tty; then
    log "Suggested ports (in-use ports were auto-bumped when possible)."
    ask "Web UI port" "${web}"; web="${REPLY}"
    ask "API port" "${api}"; api="${REPLY}"
    ask "Gateway port" "${gw}"; gw="${REPLY}"
    ask "Bind address (0.0.0.0 = all interfaces, 127.0.0.1 = this machine only)" "${bind}"
    bind="${REPLY}"
  fi

  if [[ -f "${ROOT}/.env" ]]; then
    if ! confirm ".env already exists. Update published ports / bind settings?" "n"; then
      skip "keeping existing .env"
      return
    fi
  else
    cp "${ROOT}/.env.example" "${ROOT}/.env"
    ok "created .env from .env.example"
  fi

  upsert_env JANUS_WEB_PORT "${web}"
  upsert_env JANUS_API_PORT "${api}"
  upsert_env JANUS_GATEWAY_PORT "${gw}"
  upsert_env JANUS_BIND_ADDRESS "${bind}"
  upsert_env JANUS_CORS_ALLOW_ORIGINS "[\"http://localhost:${web}\"]"
  upsert_env JANUS_API_URL "http://localhost:${api}"
  upsert_env JANUS_GATEWAY_URL "http://localhost:${gw}"

  ok "ports → web ${web}, api ${api}, gateway ${gw}, bind ${bind}"
}

start_local_stack() {
  section "Start local stack"
  if [[ "${START_STACK}" != "1" ]]; then
    skip "start skipped (--no-start)"
    return
  fi
  ensure_docker
  # shellcheck disable=SC1091
  [[ -f "${VENV_DIR}/bin/activate" ]] && source "${VENV_DIR}/bin/activate"
  ensure_path

  # Pull / ensure Ollama tags from config/local-models.yaml (and .env extras).
  if [[ -x "${ROOT}/install.sh" ]]; then
    section "Local models (via install.sh)"
    if "${ROOT}/install.sh" ensure-models; then
      ok "local Ollama models ensured"
    else
      warn "ensure-models failed — mock models still work; fix Ollama and re-run: ./install.sh ensure-models"
    fi
  else
    warn "install.sh missing — skipping Ollama model pulls"
  fi

  export JANUS_OLLAMA_COMPOSE_URL="${JANUS_OLLAMA_COMPOSE_URL:-http://host.docker.internal:11434/v1}"
  make -C "${ROOT}" stack-up
  # Refresh gateway so local-model health probes see a reachable Ollama.
  docker compose --profile full up -d --force-recreate --no-deps gateway >/dev/null 2>&1 || true
  ok "stack is up"

  local web=3000 bind="localhost" bind_cfg=""
  if [[ -f "${ROOT}/.env" ]]; then
    web="$(awk -F= '/^JANUS_WEB_PORT=/{print $2}' "${ROOT}/.env" | tail -n1)"
    web="${web:-3000}"
    bind_cfg="$(awk -F= '/^JANUS_BIND_ADDRESS=/{print $2}' "${ROOT}/.env" | tail -n1 || true)"
    if [[ -n "${bind_cfg}" && "${bind_cfg}" != "0.0.0.0" ]]; then
      bind="${bind_cfg}"
    fi
  fi

  if [[ "${RUN_SMOKE}" == "1" ]]; then
    section "Smoke test"
    if make -C "${ROOT}" smoke-chat; then
      ok "smoke-chat passed"
    else
      warn "smoke-chat failed — stack may still be starting; retry: make smoke-chat"
    fi
  fi

  cat <<EOF

${C_GREEN}${C_BOLD}Janus is ready${C_RESET}

  Open:     ${C_BOLD}http://${bind}:${web}${C_RESET}
  Create a workspace, then send a chat message.
  Out of the box answers come from a mock model (no API key required).
  Local Ollama models come from config/local-models.yaml (pulled above).

  Useful:
    ./install.sh stop           # stop Compose + host-mode processes
    ./install.sh status
    ./install.sh ensure-models  # re-pull tags from config/local-models.yaml
    make smoke-product          # knowledge + agents smoke
    docs/UI.md                  # UI walkthrough
    docs/ui-mockups/            # screenshots for decks

EOF
}

bootstrap_tf_state() {
  local account="$1" region="$2"
  local bucket="janus-tfstate-${account}"
  section "Terraform remote state (${bucket})"
  if aws s3api head-bucket --bucket "${bucket}" 2>/dev/null; then
    skip "bucket ${bucket} already exists"
  else
    if [[ "${region}" == "us-east-1" ]]; then
      aws s3api create-bucket --bucket "${bucket}" --region "${region}" >/dev/null
    else
      aws s3api create-bucket --bucket "${bucket}" --region "${region}" \
        --create-bucket-configuration LocationConstraint="${region}" >/dev/null
    fi
    aws s3api put-bucket-versioning --bucket "${bucket}" \
      --versioning-configuration Status=Enabled
    aws s3api put-bucket-encryption --bucket "${bucket}" \
      --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
    ok "created ${bucket}"
  fi
  if aws dynamodb describe-table --table-name janus-tf-locks --region "${region}" >/dev/null 2>&1; then
    skip "DynamoDB janus-tf-locks already exists"
  else
    aws dynamodb create-table \
      --table-name janus-tf-locks \
      --attribute-definitions AttributeName=LockID,AttributeType=S \
      --key-schema AttributeName=LockID,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST \
      --region "${region}" >/dev/null
    ok "created DynamoDB janus-tf-locks"
  fi
  warn "If versions.tf still has the S3 backend commented out, uncomment it and set bucket = \"${bucket}\" before terraform init -reconfigure. See docs/aws-deploy.md."
}

configure_aws() {
  section "AWS credentials"
  ensure_path
  install_awscli
  install_terraform

  local region account
  region="${AWS_REGION_ARG:-${AWS_REGION_DEFAULT}}"
  account="${AWS_ACCOUNT_ID_ARG:-}"

  if is_tty; then
    ask "AWS region" "${region}"; region="${REPLY}"
    if [[ -z "${account}" ]]; then
      ask "AWS account ID (12 digits)" ""
      account="${REPLY}"
    fi
  fi

  [[ -n "${region}" ]] || die "AWS region is required"
  if [[ -n "${account}" && ! "${account}" =~ ^[0-9]{12}$ ]]; then
    die "AWS account ID must be 12 digits"
  fi

  export AWS_DEFAULT_REGION="${region}"
  export AWS_REGION="${region}"

  if aws sts get-caller-identity >/dev/null 2>&1; then
    ok "already authenticated: $(aws sts get-caller-identity --query Arn --output text 2>/dev/null)"
  else
    section "Configure AWS CLI profile 'janus'"
    cat <<EOF
${C_DIM}You will be prompted for Access Key ID and Secret.
Keys stay on this machine under ~/.aws/ — never commit them.${C_RESET}
EOF
    if is_tty; then
      aws configure --profile janus
      export AWS_PROFILE=janus
    else
      die "Not logged in to AWS. Run: aws configure --profile janus && export AWS_PROFILE=janus"
    fi
    aws sts get-caller-identity >/dev/null || die "AWS authentication failed"
  fi

  local detected
  detected="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
  if [[ -z "${account}" ]]; then
    account="${detected}"
  fi
  if [[ -n "${detected}" && -n "${account}" && "${detected}" != "${account}" ]]; then
    warn "Caller account ${detected} ≠ provided ${account}"
    if ! confirm "Continue with caller account ${detected}?" "y"; then
      die "Aborting so you can fix credentials"
    fi
    account="${detected}"
  fi
  [[ "${account}" =~ ^[0-9]{12}$ ]] || die "Could not determine AWS account ID"
  export AWS_ACCOUNT_ID="${account}"
  ok "account ${account} · region ${region}"

  section "Terraform variables"
  local tfvars="${ROOT}/infra/aws/terraform.tfvars"
  if [[ -f "${tfvars}" ]]; then
    if confirm "infra/aws/terraform.tfvars exists. Overwrite account/region fields?" "n"; then
      cat >"${tfvars}" <<EOF
# Generated by setup.sh — do not put access keys here.
aws_account_id = "${account}"
aws_region     = "${region}"
environment    = "staging"
name_prefix    = "janus"
image_tag      = "latest"
acm_certificate_arn = ""
enable_gpu_eks = false
EOF
      ok "wrote ${tfvars}"
    else
      skip "keeping existing terraform.tfvars"
    fi
  else
    cp "${ROOT}/infra/aws/terraform.tfvars.example" "${tfvars}"
    sed -i -E "s/^aws_account_id.*/aws_account_id = \"${account}\"/" "${tfvars}"
    sed -i -E "s/^aws_region.*/aws_region     = \"${region}\"/" "${tfvars}"
    ok "wrote ${tfvars}"
  fi

  if confirm "Bootstrap remote Terraform state (S3 + DynamoDB lock) in this account?" "y"; then
    bootstrap_tf_state "${account}" "${region}"
  else
    skip "state bootstrap skipped — see docs/aws-deploy.md §3"
  fi

  if [[ "${APPLY_TERRAFORM}" == "1" ]] || confirm "Run terraform init + plan now?" "y"; then
    (
      cd "${ROOT}/infra/aws"
      terraform init
      terraform plan -out=tfplan
    )
    ok "plan written to infra/aws/tfplan"
    if [[ "${APPLY_TERRAFORM}" == "1" ]] || confirm "Apply this plan? (creates VPC, Aurora, ECS — costs money; 20–40 min)" "n"; then
      (
        cd "${ROOT}/infra/aws"
        terraform apply tfplan
        terraform output
      )
      ok "apply complete — next: push images (docs/aws-deploy.md §6) and run migrations (§7)"
    else
      log "Skipped apply. When ready:  cd infra/aws && terraform apply tfplan"
    fi
  fi

  cat <<EOF

${C_GREEN}AWS prep complete${C_RESET}

  Account:  ${account}
  Region:   ${region}
  Vars:     infra/aws/terraform.tfvars

  Full runbook:  docs/aws-deploy.md
  Marketplace:   docs/marketplace.md

  After apply:
    1. Build & push images to ECR (aws-deploy.md §6)
    2. Run alembic upgrade head against Aurora (§7)
    3. Open the ALB URL and create a workspace

EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --local) MODE="local" ;;
      --aws) MODE="aws" ;;
      --tools) MODE="tools" ;;
      --yes|-y) ASSUME_YES=1 ;;
      --no-start) START_STACK=0 ;;
      --no-smoke) RUN_SMOKE=0 ;;
      --apply) APPLY_TERRAFORM=1 ;;
      --aws-account) AWS_ACCOUNT_ID_ARG="$2"; shift ;;
      --aws-region) AWS_REGION_ARG="$2"; shift ;;
      --web-port) WEB_PORT_ARG="$2"; shift ;;
      --api-port) API_PORT_ARG="$2"; shift ;;
      --gateway-port) GATEWAY_PORT_ARG="$2"; shift ;;
      --bind) BIND_ARG="$2"; shift ;;
      --skip-docker-check) SKIP_DOCKER_CHECK=1 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown flag: $1 (try --help)" ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"
  banner

  [[ -f "${ROOT}/pyproject.toml" && -d "${ROOT}/apps/web" ]] \
    || die "Run ./setup.sh from the Janus repository root (pyproject.toml / apps/web missing)."

  ensure_path
  choose_mode
  log "mode = ${MODE}"

  case "${MODE}" in
    tools)
      need_curl
      ensure_uv
      ensure_python_venv
      ensure_node22
      install_terraform
      install_awscli
      if have docker; then ensure_docker; else warn "Docker not installed yet — install before local/aws deploy"; fi
      sync_workspace
      section "Done (tools only)"
      cat <<EOF
  export PATH="${BIN_DIR}:\$PATH"
  source ${VENV_DIR}/bin/activate
  ./setup.sh --local     # when you want the product running locally
  ./setup.sh --aws       # when you want AWS deploy prep
EOF
      ;;
    local)
      need_curl
      ensure_docker
      ensure_uv
      ensure_python_venv
      ensure_node22
      sync_workspace
      configure_env_local
      start_local_stack
      ;;
    aws)
      need_curl
      ensure_docker
      ensure_uv
      ensure_python_venv
      ensure_node22
      sync_workspace
      configure_aws
      ;;
    *) die "Unknown mode: ${MODE}" ;;
  esac
}

main "$@"
