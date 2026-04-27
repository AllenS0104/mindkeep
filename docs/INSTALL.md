# 📥 Installation Guide

> Cross-project on-disk memory store for AI coding agents — **Python ≥ 3.9, stdlib-only runtime, MIT**.
> This guide gets you from *nothing installed* to a working `mindkeep` CLI in **under 5 minutes**.

---

## 1. TL;DR — Pick Your Path

| You are on… | Best path | One-liner |
|---|---|---|
| 💻 **Windows** (laptop / dev box) | One-shot PowerShell script | `iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 \| iex` |
| 🐧 **macOS / Linux / WSL** | One-shot bash script | `curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh \| bash` |
| 🏢 **CI, container image, air-gapped, or "no curl-pipe-sh please"** | `pipx` from a pinned wheel in GitHub Releases | `pipx install https://github.com/AllenS0104/mindkeep/releases/latest/download/mindkeep-0.2.0-py3-none-any.whl` |

> ✅ **All three paths yield the same result**: a `mindkeep` executable on your `PATH`, plus a `python -m mindkeep` fallback.
> When in doubt, pick the row that matches your environment and jump to the matching section below.

---

## 2. Prerequisites

### 2.1 Python ≥ 3.9

`mindkeep` has **zero runtime dependencies**, but it needs a modern CPython.

**Check what you have:**

```bash
# macOS / Linux / WSL
python3 --version    # expect: Python 3.9.x or newer
```

```powershell
# Windows (py launcher is preferred)
py -3 --version
python --version
```

**If missing or too old:**

| OS | Install command |
|---|---|
| Windows | `winget install -e --id Python.Python.3.12` (or [python.org](https://www.python.org/downloads/)) |
| macOS (Homebrew) | `brew install python@3.12` |
| Ubuntu / Debian | `sudo apt-get install -y python3 python3-pip python3-venv` |
| Fedora / RHEL | `sudo dnf install -y python3 python3-pip` |
| Arch | `sudo pacman -S python python-pip` |

> ⚠️ **Windows tip**: If `python` opens the Microsoft Store, you probably have the "App execution alias" enabled but no real Python. Install from [python.org](https://www.python.org/downloads/) **with** the *"Add python.exe to PATH"* checkbox ticked, or install via `winget` which handles PATH for you.

### 2.2 Shell support

The installers detect your shell and adapt:

| Shell | Status | Notes |
|---|---|---|
| **PowerShell 5.1** (built-in on Win 10/11) | ✅ Supported | Default path for `iwr \| iex`. |
| **PowerShell 7+** (`pwsh`) | ✅ Supported | Same script, better Unicode. |
| **git-bash / MSYS2** on Windows | ✅ Supported | Use `install.sh`, not `.ps1`. |
| **bash / zsh / fish** | ✅ Supported | `install.sh` writes to `.bashrc` / `.zshrc` / `config.fish` as appropriate. |

### 2.3 Network allowlist

The installers and `pipx` only talk to these domains. Cleared these with your proxy / firewall team once and you're set:

```
github.com                    # source tarballs, releases
raw.githubusercontent.com     # install.ps1 / install.sh
codeload.github.com           # git archive downloads
files.pythonhosted.org        # pipx / pip dependencies (pipx itself, if missing)
pypi.org                      # pip index
objects.githubusercontent.com # release asset storage
```

> ℹ️ If only `pypi.org` is allowed, use **Method 3 (offline wheel)** — it does not need GitHub.

---

## 3. Method 1 — One-Shot Script (Recommended)

The one-shot scripts do everything: check Python, install `pipx` if missing, install `mindkeep`, fix your `PATH`, then run `mindkeep doctor` to verify.

### 3.1 Windows (PowerShell)

The simplest install, using defaults:

```powershell
# Download install.ps1 in memory and execute it — installs from the main branch.
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex
```

**Passing parameters via `iwr | iex`** requires the `&` invoke operator because `iex` doesn't forward args natively:

```powershell
# Upgrade an existing install, quietly.
& ([scriptblock]::Create((iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1).Content)) -Upgrade -Quiet
```

Or — the **recommended shape for scripted / CI environments** — download first, then run:

```powershell
# Download, review, then execute with parameters. Always the safer option.
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 -OutFile install.ps1
.\install.ps1 -Method pipx -Upgrade
```

**PowerShell parameter reference:**

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `-Source <url\|path>` | string | `git+https://github.com/AllenS0104/mindkeep.git` | Install source. Also accepts a local `.whl` path or PyPI name spec (`mindkeep==0.2.0`). |
| `-Method <pipx\|pip\|auto>` | enum | `auto` | `auto` prefers `pipx`, falls back to `pip --user`. |
| `-Upgrade` | switch | off | Force-upgrade if already installed. |
| `-Quiet` | switch | off | Suppress progress output (still prints warnings/errors). |

### 3.2 macOS / Linux (bash)

Defaults — install latest `main` via `pipx`:

```bash
# One-shot. Installer picks pipx if available, pip --user otherwise.
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash
```

Pass flags through `bash -s --`:

```bash
# Upgrade, force pipx method, quiet mode.
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh \
  | bash -s -- --method pipx --upgrade --quiet
```

**Bash flag reference:**

| Flag | Default | Purpose |
|---|---|---|
| `--source <url\|path>` | `git+https://github.com/AllenS0104/mindkeep.git` | Override install source (git URL, local wheel, release URL). |
| `--method <pipx\|pip\|auto>` | `auto` | Force a specific installer backend. |
| `--upgrade` | off | Upgrade if already installed. |
| `--quiet` | off | Reduce output. |
| `-h`, `--help` | — | Print usage. |

### 3.3 What the installer actually does

Both scripts perform the same 5 steps:

1. **Detect Python ≥ 3.9** (`py -3` on Windows, `python3`/`python` elsewhere). Bail with a platform-specific install hint if missing.
2. **Ensure `pipx` is available** (via `brew install pipx` on macOS, else `python -m pip install --user pipx` + `pipx ensurepath`).
3. **Install `mindkeep`** from `--source` using the chosen method (`pipx install` or `pip install --user`).
4. **Fix `PATH`** — appends the user scripts dir (`%APPDATA%\Python\...\Scripts` / `~/.local/bin`) to your shell rc or the User `PATH` env var.
5. **Verify** — runs `mindkeep --version` and `mindkeep doctor`; falls back to `python -m mindkeep --version` if the shim isn't on PATH yet.

### 3.4 🔐 "Download first, then run" — The responsible way

`curl | bash` and `iwr | iex` execute arbitrary remote code. For production machines, shared boxes, or anything holding credentials:

```bash
# Download, inspect, then run.
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh -o install.sh
less install.sh                 # read it
bash install.sh                 # or: bash install.sh --upgrade
```

```powershell
# Windows equivalent.
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 -OutFile install.ps1
notepad install.ps1             # read it
.\install.ps1
```

Every [GitHub Release](https://github.com/AllenS0104/mindkeep/releases) also ships a `SHA256SUMS` file for integrity checks:

```bash
curl -fsSL -o SHA256SUMS https://github.com/AllenS0104/mindkeep/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```

---

## 4. Method 2 — pipx (Engineer's Choice)

[`pipx`](https://pipx.pypa.io/) installs each Python CLI into its **own isolated virtualenv** and symlinks the entry point onto your `PATH`. This is the officially recommended way to install Python CLI tools — no dependency conflicts, no polluted site-packages, one-command uninstall.

### 4.1 Install `pipx` once

```bash
# macOS
brew install pipx && pipx ensurepath

# Linux (Debian/Ubuntu 23.04+)
sudo apt install pipx && pipx ensurepath

# Any platform (fallback)
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

```powershell
# Windows
py -3 -m pip install --user pipx
py -3 -m pipx ensurepath
# Open a new shell so PATH changes take effect.
```

### 4.2 Install `mindkeep`

```bash
# From the main branch (rolling)
pipx install git+https://github.com/AllenS0104/mindkeep.git

# Pinned to a tagged release (reproducible — recommended for teams / CI)
pipx install git+https://github.com/AllenS0104/mindkeep.git@v0.2.0

# From a prebuilt wheel on the Releases page (fastest, no git needed)
pipx install https://github.com/AllenS0104/mindkeep/releases/latest/download/mindkeep-0.2.0-py3-none-any.whl
```

### 4.3 Why pipx wins

- ✅ **Isolated venv** per CLI — upgrading one tool can never break another.
- ✅ **Automatic PATH management** via `pipx ensurepath`.
- ✅ **Clean uninstall**: `pipx uninstall mindkeep`.
- ✅ **Easy upgrade**: `pipx upgrade mindkeep`.
- ✅ **Air-gap friendly**: pipx reads the same wheels `pip` does.

---

## 5. Method 3 — pip wheel (Offline / Locked-Down Environments)

When you cannot reach `github.com` directly, or corporate policy forbids `pipx`:

### 5.1 Download the wheel once (on a machine with internet)

Grab `mindkeep-0.2.0-py3-none-any.whl` from the [Releases page](https://github.com/AllenS0104/mindkeep/releases/latest). The wheel is **pure Python, ~30 KB**, and works on any OS/arch with Python ≥ 3.9.

### 5.2 Install

```bash
# macOS / Linux / WSL
pip install --user ./mindkeep-0.2.0-py3-none-any.whl
```

```powershell
# Windows
py -3 -m pip install --user .\mindkeep-0.2.0-py3-none-any.whl
```

### 5.3 Fix "mindkeep: command not found" / "not recognized"

`pip install --user` drops the `mindkeep` script into your user scripts dir, which may not be on `PATH`. Find it:

```bash
# macOS / Linux — prints the dir, then adds it to your shell rc
python3 -c "import sysconfig; print(sysconfig.get_path('scripts','posix_user'))"
# Typical result: /home/you/.local/bin
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc   # or ~/.zshrc
source ~/.bashrc
```

```powershell
# Windows — prints the dir, then adds it to the User PATH permanently
py -3 -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))"
# Typical result: C:\Users\<you>\AppData\Roaming\Python\Python312\Scripts
$scripts = py -3 -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))"
[Environment]::SetEnvironmentVariable(
    "Path",
    ([Environment]::GetEnvironmentVariable("Path","User") + ";$scripts"),
    "User"
)
# Open a new shell.
```

> ℹ️ **Always-works fallback**: `python -m mindkeep ...` works even when the script shim isn't on PATH. Every `mindkeep <cmd>` can be rewritten as `python -m mindkeep <cmd>`.

---

## 6. Method 4 — From Source (Developers)

Use this if you plan to **contribute**, run the test suite, or hack on the internals:

```bash
git clone https://github.com/AllenS0104/mindkeep.git
cd mindkeep

# Editable install with dev extras (pytest, pytest-cov)
python3 -m pip install -e ".[dev]"

# Run the tests
pytest

# Run the CLI from your working tree
mindkeep --version
```

The `-e` (editable) flag means your edits to `src/mindkeep/` take effect immediately without reinstalling.

---

## 7. Platform Cheat Sheet

| Platform | Data dir (`MINDKEEP_HOME` overrides) | Scripts dir on PATH | Known gotchas |
|---|---|---|---|
| **Windows 10/11** | `%APPDATA%\mindkeep\` | `%APPDATA%\Python\Python3xx\Scripts` | Store-alias `python.exe` stub is not real Python. New PATH entries require a **new shell**. |
| **macOS** (Intel / Apple Silicon) | `~/Library/Application Support/mindkeep/` | `~/.local/bin` (pip) · `~/.local/bin` or `/opt/homebrew/bin` (pipx) | macOS ships `python3` but no `pip` on older versions — use `brew install python`. |
| **Linux** (glibc) | `$XDG_DATA_HOME/mindkeep/` → `~/.local/share/mindkeep/` | `~/.local/bin` | Debian/Ubuntu may require `python3-venv` for `pipx`. |
| **WSL2** | Same as Linux (inside WSL) | `~/.local/bin` | Keep WSL data dir inside WSL FS, **not** on `/mnt/c/...` — SQLite WAL fsync on DrvFS is slow. |
| **Docker / Alpine** | `/root/.local/share/mindkeep/` or set `MINDKEEP_HOME` | `/root/.local/bin` | `apk add python3 py3-pip` minimum. Prefer `python:3.12-slim` base for glibc + wheels. |

---

## 8. Enterprise / Proxy / Air-Gapped

### 8.1 Behind an HTTPS proxy

```bash
export HTTPS_PROXY=http://proxy.corp.example:8080
export HTTP_PROXY=http://proxy.corp.example:8080
export NO_PROXY=localhost,127.0.0.1,.corp.example

pipx install git+https://github.com/AllenS0104/mindkeep.git
```

```powershell
# Windows — persist for the current user
[Environment]::SetEnvironmentVariable("HTTPS_PROXY", "http://proxy.corp.example:8080", "User")
[Environment]::SetEnvironmentVariable("HTTP_PROXY",  "http://proxy.corp.example:8080", "User")
```

### 8.2 Internal PyPI mirror (Artifactory / Nexus / devpi)

```bash
# One-shot
pip install --index-url https://pypi.corp.example/simple/ \
            --trusted-host pypi.corp.example \
            mindkeep-0.2.0-py3-none-any.whl

# Or persist — writes ~/.config/pip/pip.conf (Linux/macOS) or %APPDATA%\pip\pip.ini (Windows)
pip config set global.index-url https://pypi.corp.example/simple/
pip config set global.trusted-host pypi.corp.example
```

Resulting `pip.conf` / `pip.ini`:

```ini
[global]
index-url = https://pypi.corp.example/simple/
trusted-host = pypi.corp.example
```

### 8.3 Air-gapped install (two-machine workflow)

On a **machine with internet**:

```bash
mkdir am-bundle && cd am-bundle
pip download --dest . --no-deps \
  "mindkeep @ git+https://github.com/AllenS0104/mindkeep.git@v0.2.0"
# Or just grab the release wheel:
curl -LO https://github.com/AllenS0104/mindkeep/releases/latest/download/mindkeep-0.2.0-py3-none-any.whl
```

Transfer the folder (USB / approved channel), then on the **air-gapped machine**:

```bash
pip install --user --no-index --find-links ./am-bundle mindkeep
```

> ℹ️ Because `mindkeep` has **zero runtime deps**, the bundle is just a single `~30 KB` wheel. No transitive surface to audit.

---

## 9. Upgrading

Pick the row that matches how you installed:

| How you installed | Upgrade command |
|---|---|
| One-shot script | Re-run the installer with `-Upgrade` (Windows) or `--upgrade` (bash). |
| Built-in updater | `mindkeep upgrade` — auto-detects `pipx` vs `pip` and runs the right command. |
| pipx directly | `pipx upgrade mindkeep` |
| pip --user | `pip install --user --upgrade "git+https://github.com/AllenS0104/mindkeep.git"` |
| Source checkout | `git pull && pip install -e ".[dev]"` |

> ✅ Your stored memories are in the **data dir**, not the package — upgrades never touch them. The on-disk format is versioned and backwards-compatible within `0.x`.

---

## 10. Verify Your Install

### 10.1 Version

```bash
mindkeep --version
# → mindkeep 0.2.0
```

### 10.2 `mindkeep doctor` — Health check

```bash
mindkeep doctor
```

`doctor` prints an 8-item report. Here's how to read each line:

| # | Check | ✅ Good | ⚠️ What to do if not |
|---|---|---|---|
| 1 | **Python version** | `3.9+` | Upgrade Python (see §2.1). |
| 2 | **Package import** | `mindkeep 0.2.0 OK` | Reinstall; likely a broken `--user` scripts dir. |
| 3 | **Data dir writable** | path + `rw` | Fix perms, or set `MINDKEEP_HOME` to a writable dir. |
| 4 | **SQLite WAL** | `journal_mode=wal` | Disk may not support WAL (rare — some FUSE mounts); move data dir to a local FS. |
| 5 | **Current project ID** | 12-hex-char hash | Only warns if `cwd` is not a repo — harmless. |
| 6 | **Secrets redactor** | `11 patterns loaded` | If missing, reinstall — indicates incomplete package. |
| 7 | **PATH shim** | `mindkeep → /path/to/shim` | Finish §5.3 PATH fix, or keep using `python -m mindkeep`. |
| 8 | **Flush scheduler** | `30s ok` | Rare; file a bug if failing. |

### 10.3 The `python -m mindkeep` fallback

If the `mindkeep` command is not found (new shell not opened, PATH not updated, corporate launcher policy), everything still works via:

```bash
python -m mindkeep --version
python -m mindkeep doctor
python -m mindkeep list
```

Every example in [USAGE.md](./USAGE.md) can be prefixed with `python -m ` instead of calling the shim.

---

## 11. Common Install Problems

| Symptom | Quick fix |
|---|---|
| `mindkeep: command not found` / `not recognized` | Open a **new** shell; if still missing, do the PATH fix in §5.3, or use `python -m mindkeep`. |
| `pip: error: externally-managed-environment` (Debian 12+, Ubuntu 24.04+) | Use `pipx` instead, or pass `--break-system-packages` (not recommended), or use a venv. |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Corporate MITM proxy. Install your company's root CA into the system trust store, then retry. |
| `Could not find a version that satisfies the requirement` | Wheel not cached locally and no network. Use §8.3 air-gapped flow. |
| `Microsoft Store opens when I run python` | Real Python not installed. Install from python.org **with** "Add to PATH", or `winget install -e --id Python.Python.3.12`. |
| `pipx: command not found` after install | Run `python3 -m pipx ensurepath`, then open a new shell. |
| `Permission denied` writing to data dir | Set `MINDKEEP_HOME=/path/you/own` or fix perms on the existing dir (§7). |
| Install works, but `doctor` says `PATH shim: MISSING` | Script shim never landed on PATH. See §5.3. |

> Detailed diagnosis, SIGKILL recovery, and corrupted-DB repair live in [TROUBLESHOOTING.md](./TROUBLESHOOTING.md).

---

## 12. Next Steps

You're installed. Now go store something:

- 📖 [USAGE.md](./USAGE.md) — CLI reference, Python API, recipes.
- 🏗 [ARCHITECTURE.md](../ARCHITECTURE.md) — how WAL + flush scheduler + secrets redactor fit together.
- ❓ [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) — when things misbehave.
- 🐛 [Issues](https://github.com/AllenS0104/mindkeep/issues) — file bugs or feature requests.

```bash
# First memory: a fact about this very install.
cd ~/your-project
mindkeep --version
python -c "from mindkeep import MemoryStore; \
  MemoryStore.open().remember_fact('install.method', 'pipx from release wheel')"
mindkeep show --kind facts
```

Happy remembering. 🧠
