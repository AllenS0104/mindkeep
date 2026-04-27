#!/usr/bin/env bash
# mindkeep one-shot installer for macOS / Linux
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash -s -- --upgrade
#   bash install.sh --method pipx --source git+https://github.com/AllenS0104/mindkeep.git

set -euo pipefail

# ---------- defaults ----------
SOURCE_DEFAULT="git+https://github.com/AllenS0104/mindkeep.git"
SOURCE="$SOURCE_DEFAULT"
METHOD="auto"      # pipx | pip | auto
UPGRADE=0
QUIET=0

# ---------- colors ----------
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  C_RESET=$'\033[0m'
  C_DIM=$'\033[2m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'
  C_BOLD=$'\033[1m'
else
  C_RESET=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

log()  { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }
info() { [ "$QUIET" -eq 1 ] || printf '%sℹ️  %s%s\n' "$C_BLUE" "$*" "$C_RESET"; }
ok()   { [ "$QUIET" -eq 1 ] || printf '%s✅ %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf '%s⚠️  %s%s\n' "$C_YELLOW" "$*" "$C_RESET" >&2; }
err()  { printf '%s❌ %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; }

usage() {
  cat <<EOF
${C_BOLD}mindkeep installer${C_RESET}

Usage:
  install.sh [--source <url|path>] [--method pipx|pip|auto] [--upgrade] [--quiet] [--help]

Options:
  --source <url|path>   Package source. Default: ${SOURCE_DEFAULT}
  --method <mode>       Install method: pipx | pip | auto (default: auto)
  --upgrade             Upgrade if already installed
  --quiet               Reduce output
  -h, --help            Show this help

Examples:
  install.sh
  install.sh --upgrade
  install.sh --method pip --source ./dist/mindkeep-0.2.0-py3-none-any.whl
EOF
}

# ---------- arg parse ----------
main() {
while [ $# -gt 0 ]; do
  case "$1" in
    --source)   SOURCE="${2:-}"; shift 2 ;;
    --source=*) SOURCE="${1#*=}"; shift ;;
    --method)   METHOD="${2:-}"; shift 2 ;;
    --method=*) METHOD="${1#*=}"; shift ;;
    --upgrade)  UPGRADE=1; shift ;;
    --quiet)    QUIET=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) err "Unknown argument: $1"; usage; exit 2 ;;
  esac
done

case "$METHOD" in
  pipx|pip|auto) ;;
  *) err "Invalid --method: $METHOD (expected pipx|pip|auto)"; exit 2 ;;
esac

# ---------- platform ----------
OS="$(uname -s 2>/dev/null || echo unknown)"
info "Platform: ${OS}"
info "Source:   ${SOURCE}"
info "Method:   ${METHOD}"

# ---------- python detection ----------
find_python() {
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' >/dev/null 2>&1; then
        echo "$cand"
        return 0
      fi
    fi
  done
  return 1
}

if ! PY="$(find_python)"; then
  err "Python >= 3.9 not found."
  case "$OS" in
    Darwin) warn "Install via: brew install python" ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        warn "Install via: sudo apt-get install -y python3 python3-pip python3-venv"
      elif command -v dnf >/dev/null 2>&1; then
        warn "Install via: sudo dnf install -y python3 python3-pip"
      elif command -v pacman >/dev/null 2>&1; then
        warn "Install via: sudo pacman -S python python-pip"
      else
        warn "Please install Python 3.9+ via your package manager."
      fi
      ;;
    *) warn "Please install Python 3.9+ manually." ;;
  esac
  exit 1
fi
PY_VER="$("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
ok "Python: ${PY} (${PY_VER})"

# ---------- pipx detection / install ----------
has_pipx() {
  if command -v pipx >/dev/null 2>&1; then
    PIPX_CMD="pipx"; return 0
  fi
  if "$PY" -m pipx --version >/dev/null 2>&1; then
    PIPX_CMD="$PY -m pipx"; return 0
  fi
  return 1
}

ensure_pipx() {
  if has_pipx; then
    ok "pipx available: ${PIPX_CMD}"
    return 0
  fi
  info "pipx not found, installing..."
  if [ "$OS" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    if brew install pipx; then
      brew link --overwrite pipx >/dev/null 2>&1 || true
    else
      warn "brew install pipx failed, falling back to pip --user"
    fi
  fi
  if ! has_pipx; then
    "$PY" -m pip install --user --upgrade pipx
    "$PY" -m pipx ensurepath >/dev/null 2>&1 || true
  fi
  if has_pipx; then
    ok "pipx installed: ${PIPX_CMD}"
    return 0
  fi
  return 1
}

# ---------- install ----------
install_with_pipx() {
  # shellcheck disable=SC2086
  if $PIPX_CMD list 2>/dev/null | grep -q '^ *package mindkeep'; then
    if [ "$UPGRADE" -eq 1 ]; then
      info "Upgrading via pipx..."
      # shellcheck disable=SC2086
      $PIPX_CMD upgrade mindkeep || $PIPX_CMD install --force "$SOURCE"
    else
      info "Already installed. Use --upgrade to update. Reinstalling to ensure latest source..."
      # shellcheck disable=SC2086
      $PIPX_CMD install --force "$SOURCE"
    fi
  else
    info "Installing via pipx..."
    # shellcheck disable=SC2086
    $PIPX_CMD install "$SOURCE"
  fi
}

install_with_pip() {
  info "Installing via pip --user..."
  "$PY" -m pip install --user --upgrade "$SOURCE"
}

case "$METHOD" in
  pipx)
    ensure_pipx || { err "Failed to install pipx"; exit 1; }
    install_with_pipx
    ;;
  pip)
    install_with_pip
    ;;
  auto)
    if ensure_pipx; then
      install_with_pipx
    else
      warn "pipx unavailable, falling back to pip --user"
      install_with_pip
    fi
    ;;
esac

# ---------- PATH handling ----------
SCRIPTS_DIR="$("$PY" -c "import sysconfig;print(sysconfig.get_path('scripts','posix_user'))" 2>/dev/null || echo "")"
PIPX_BIN_DIR="${PIPX_BIN_DIR:-$HOME/.local/bin}"

path_contains() {
  case ":$PATH:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

detect_rc() {
  local sh_name
  sh_name="$(basename "${SHELL:-/bin/bash}")"
  case "$sh_name" in
    zsh)  echo "$HOME/.zshrc" ;;
    fish) echo "$HOME/.config/fish/config.fish" ;;
    bash)
      if [ "$OS" = "Darwin" ] && [ -f "$HOME/.bash_profile" ]; then
        echo "$HOME/.bash_profile"
      else
        echo "$HOME/.bashrc"
      fi
      ;;
    *) echo "$HOME/.profile" ;;
  esac
}

append_path_to_rc() {
  local dir="$1" rc="$2" sh_name
  sh_name="$(basename "${SHELL:-/bin/bash}")"
  mkdir -p "$(dirname "$rc")" 2>/dev/null || true
  touch "$rc" 2>/dev/null || true
  if grep -Fq "$dir" "$rc" 2>/dev/null; then
    return 0
  fi
  if [ "$sh_name" = "fish" ]; then
    printf '\n# Added by mindkeep installer\nset -gx PATH %s $PATH\n' "$dir" >> "$rc"
  else
    printf '\n# Added by mindkeep installer\nexport PATH="%s:$PATH"\n' "$dir" >> "$rc"
  fi
  warn "Added ${dir} to PATH in ${rc}"
  warn "Run: source ${rc}    (or open a new shell)"
}

ensure_path_dir() {
  local dir="$1"
  [ -n "$dir" ] || return 0
  [ -d "$dir" ] || return 0
  if path_contains "$dir"; then
    return 0
  fi
  local rc
  rc="$(detect_rc)"
  append_path_to_rc "$dir" "$rc"
  export PATH="$dir:$PATH"
}

ensure_path_dir "$SCRIPTS_DIR"
ensure_path_dir "$PIPX_BIN_DIR"

# ---------- verify ----------
info "Verifying installation..."
VERIFIED=0
if command -v mindkeep >/dev/null 2>&1; then
  if mindkeep --version >/dev/null 2>&1; then
    ok "mindkeep --version: $(mindkeep --version 2>&1 | head -n1)"
    VERIFIED=1
  fi
fi
if [ "$VERIFIED" -eq 0 ]; then
  if "$PY" -m mindkeep --version >/dev/null 2>&1; then
    ok "python -m mindkeep --version: $("$PY" -m mindkeep --version 2>&1 | head -n1)"
    VERIFIED=1
  fi
fi

if [ "$VERIFIED" -eq 0 ]; then
  warn "Could not invoke mindkeep. You may need to open a new shell."
  warn "Try:  source $(detect_rc)    or    exec \"\$SHELL\" -l"
  exit 1
fi

# doctor (non-fatal)
if command -v mindkeep >/dev/null 2>&1; then
  log ""
  info "Running: mindkeep doctor"
  mindkeep doctor || warn "doctor reported issues (non-fatal)"
fi

log ""
ok "Done. Enjoy mindkeep! 🎉"
}

# Call main with all args. Wrapping everything in main() means that if this
# script is delivered via `curl | bash` and the download is truncated, bash
# will reach EOF before seeing the `main "$@"` invocation (or before main's
# closing `}`), and will abort with a parse error instead of partially
# executing the top half of the installer.
main "$@"
