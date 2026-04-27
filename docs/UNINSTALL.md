# Uninstalling mindkeep

A complete, cross-platform guide to removing **mindkeep** cleanly — without losing data you wanted to keep.

> Covers Windows (PowerShell), macOS, and Linux. Every command block is labeled by platform. Read §1 first — 90% of regrets come from skipping it.

---

## Table of contents

1. [Before you uninstall (decision tree)](#1-before-you-uninstall)
2. [Back up all project memory](#2-back-up-all-project-memory)
3. [Uninstall the package (by install method)](#3-uninstall-the-package)
4. [Clean up data](#4-clean-up-data)
5. [Restore PATH](#5-restore-path)
6. [Verify the uninstall is clean](#6-verify-the-uninstall-is-clean)
7. [Complete reinstall (new machine / major upgrade)](#7-complete-reinstall)
8. [Troubleshooting](#8-troubleshooting)
9. [Enterprise exit procedure (compliance)](#9-enterprise-exit-procedure)

---

## 1. Before you uninstall

Answer these three questions. **Do not skip them.**

### Q1 — Do you want to keep your memory data?

`mindkeep` stores SQLite databases for every project it has touched. Uninstalling the Python package **does not** delete this data (on purpose). Two paths:

- **Keep / archive** → §2 (export JSON backups), then §3 (uninstall package). Data directory stays intact, ready for future reinstall.
- **Wipe everything** → skip §2, do §3 then §4 (delete data directory).

> ⚠️  **Destructive action ahead.** Deleting the data directory is **irreversible**. Facts, ADRs, sessions, and preferences for every project are gone. Always take a backup first unless you are 100% sure.

### Q2 — Do you want to uninstall the Python package, or only wipe data?

| Goal | Do §3 | Do §4 |
| --- | :---: | :---: |
| Remove the tool entirely | ✅ | ✅ |
| "Factory reset" but keep the tool | ❌ | ✅ |
| Stop using memory but keep data for later | ✅ | ❌ |

### Q3 — Do you want to revert the PATH change made by the installer?

The one-shot installers modify your **User PATH** so `mindkeep` resolves from any shell:

- **Windows**: `install.ps1` appends `sysconfig.get_path('scripts','nt_user')` and (when pipx is used) `%USERPROFILE%\.local\bin` to your **User PATH** via `[Environment]::SetEnvironmentVariable("Path", ..., "User")`.
- **macOS / Linux**: `install.sh` appends `export PATH="…:$PATH"` lines to `~/.zshrc` / `~/.bashrc` / `~/.bash_profile` / `~/.config/fish/config.fish` (whichever matches your shell). Each line is preceded by the marker comment `# Added by mindkeep installer`.

If those directories contain **only** mindkeep shims, reverting is safe. If they contain other user-installed scripts you rely on (e.g. `pipx`-installed tools, `pip --user` tools), **leave PATH alone** — uninstalling the package is enough.

---

## 2. Back up all project memory

> Recommended **before** §3 and **required** before §4.

`mindkeep` does not ship a `--json-ids` flag. The portable way to enumerate projects is to read the `*.meta.json` sidecars directly from the data directory.

### Find the data directory

```powershell
# Windows / macOS / Linux  (works anywhere mindkeep is on PATH)
mindkeep where
```

Output is two lines: the `data_dir` and the current project's hash. If you need it in a script, the per-OS defaults (see [ARCHITECTURE.md §3.1](../ARCHITECTURE.md)) are:

| OS | Default path | Env override |
| --- | --- | --- |
| Windows | `%APPDATA%\mindkeep\` (≈ `C:\Users\<u>\AppData\Roaming\mindkeep`) | `$env:MINDKEEP_HOME` |
| macOS | `~/Library/Application Support/mindkeep/` | `MINDKEEP_HOME` |
| Linux | `$XDG_DATA_HOME/mindkeep/` (fallback `~/.local/share/mindkeep/`) | `MINDKEEP_HOME` |

### Export every project to JSON (portable Python one-liner)

The `export` subcommand takes a single `--project <hash>`. Below is a loop that backs up **every** project.

**Windows (PowerShell)**

```powershell
$backupDir = "$HOME\mindkeep-backup-$(Get-Date -Format yyyyMMdd)"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$dataDir  = (mindkeep where | Select-String 'data_dir:').ToString().Split(':',2)[1].Trim()

Get-ChildItem -Path $dataDir -Filter '*.meta.json' |
  Where-Object { $_.Name -ne 'preferences.meta.json' } |
  ForEach-Object {
    $meta = Get-Content $_.FullName -Raw | ConvertFrom-Json
    $hash = $meta.project_hash
    if ($hash) {
      mindkeep export --project $hash "$backupDir\$hash.json"
    }
  }
Write-Host "Backups written to $backupDir"
```

**macOS / Linux (bash/zsh)**

```bash
backup_dir="$HOME/mindkeep-backup-$(date +%Y%m%d)"
mkdir -p "$backup_dir"
data_dir="$(mindkeep where | awk -F': ' '/data_dir/ {print $2}')"

for meta in "$data_dir"/*.meta.json; do
  [ -e "$meta" ] || continue
  case "$(basename "$meta")" in preferences.meta.json) continue ;; esac
  hash="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("project_hash",""))' "$meta")"
  [ -n "$hash" ] && mindkeep export --project "$hash" "$backup_dir/$hash.json"
done
echo "Backups written to $backup_dir"
```

Each JSON dump contains facts, ADRs, preferences, and sessions for one project. They can be restored any time with `mindkeep import --project <hash> <file>.json`.

> **Tip**: also `git add` your backup directory to a private repo for extra safety before §4.

---

## 3. Uninstall the package

Pick the section that matches **how you installed**. If you are unsure, run the detector:

```powershell
# Windows / macOS / Linux
pipx list 2>$null | Select-String mindkeep   # pipx?   (PowerShell)
pipx list 2>/dev/null | grep mindkeep        # pipx?   (bash/zsh)
pip show mindkeep                            # pip?    (any shell)
```

Exactly one of the commands should report it. If both match, you have a leftover from a previous install — run **both** uninstall paths below to clean up.

### 3a. pipx (recommended default)

```powershell
# Windows / macOS / Linux
pipx uninstall mindkeep
```

This removes the virtualenv under `~/.local/pipx/venvs/mindkeep/` and the shim in `~/.local/bin/` (or `%USERPROFILE%\.local\bin\` on Windows).

### 3b. pip --user

```powershell
# Windows / macOS / Linux
pip uninstall -y mindkeep
```

This removes the package from the per-user `site-packages`. The script shim in `sysconfig.get_path('scripts','nt_user')` / `sysconfig.get_path('scripts','posix_user')` is removed automatically.

### 3c. Editable install (`pip install -e .`)

```powershell
# Windows
pip uninstall -y mindkeep
# optionally remove the checkout:
Remove-Item -Recurse -Force C:\Users\<you>\mindkeep
```

```bash
# macOS / Linux
pip uninstall -y mindkeep
# optionally remove the checkout:
rm -rf ~/mindkeep
```

> Deleting the checkout is optional. Keep it if you want to inspect or resurrect the install later — `git clone` can always recreate it.

### 3d. Installed via the one-shot installer

`install.ps1` / `install.sh` are thin wrappers around pipx (preferred) or pip `--user` (fallback). Follow 3a if you used defaults, 3b if you passed `--method pip`. There is nothing extra to undo from the installer itself — the only side effect besides the Python package is the PATH change, handled in §5.

---

## 4. Clean up data

### 4a. Clear a single project

```powershell
# Windows / macOS / Linux — --yes skips the interactive confirmation
mindkeep clear --yes --project <hash>
```

Use `mindkeep list` to find hashes.

### 4b. Wipe everything

> ⚠️  **Destructive.** Every project's facts, ADRs, sessions, and the global preferences DB are erased. No undo. Make sure §2 is done.

```powershell
# Windows
Remove-Item -Recurse -Force $env:APPDATA\mindkeep
```

```bash
# macOS
rm -rf ~/Library/Application\ Support/mindkeep
```

```bash
# Linux
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/mindkeep"
```

If you set `MINDKEEP_HOME` to a custom location, delete that directory instead:

```powershell
# Windows
if ($env:MINDKEEP_HOME) { Remove-Item -Recurse -Force $env:MINDKEEP_HOME }
```

```bash
# macOS / Linux
[ -n "${MINDKEEP_HOME:-}" ] && rm -rf "$MINDKEEP_HOME"
```

### 4c. What's actually in the data directory

For context — so you know what you're deleting:

| File pattern | Purpose | Per project? |
| --- | --- | --- |
| `<hash>.db` | SQLite database — facts, adrs, sessions, meta | ✅ one per project |
| `<hash>.db-wal` | SQLite write-ahead log (live writes) | ✅ |
| `<hash>.db-shm` | SQLite shared-memory index for the WAL | ✅ |
| `<hash>.meta.json` | Sidecar: project_hash, display_name, updated_at | ✅ |
| `preferences.db` + `.db-wal` + `.db-shm` | **Global** cross-project preferences | ❌ shared |

Nothing else should live in this directory. Any `*.json` file that is not a `.meta.json` sidecar is safe to delete.

---

## 5. Restore PATH

### Windows

The installer appended to your **User PATH** (HKCU, not HKLM — no admin needed to revert).

```powershell
# 1. See what's there
[Environment]::GetEnvironmentVariable("Path", "User") -split ';'

# 2. Identify the directories the installer added. Typically:
$scripts = (python -c "import sysconfig;print(sysconfig.get_path('scripts','nt_user'))").Trim()
$pipxBin = Join-Path $env:USERPROFILE ".local\bin"
"Candidates to remove:"; $scripts; $pipxBin

# 3. Remove them (ONLY if you don't have other tools installed there).
$old = [Environment]::GetEnvironmentVariable("Path", "User")
$new = ($old -split ';' | Where-Object { $_ -and $_ -ne $scripts -and $_ -ne $pipxBin }) -join ';'
[Environment]::SetEnvironmentVariable("Path", $new, "User")
```

> ⚠️  Removing `…\Python3x\Scripts\` also removes **every** `pip --user` tool from PATH. If you use any other such tools, leave this entry alone.

Open a new terminal for the change to take effect.

### macOS / Linux

The installer wrote lines into your shell rc file, always preceded by:

```
# Added by mindkeep installer
```

Remove them with your editor, or with `sed`:

```bash
# Detect your rc file (zsh → ~/.zshrc, bash on Linux → ~/.bashrc, bash on macOS → ~/.bash_profile)
rc="$HOME/.zshrc"   # adjust for your shell

# Preview what would be removed:
grep -nB1 -A1 "Added by mindkeep installer" "$rc"

# Remove the marker comment and the line that follows it:
sed -i.bak '/# Added by mindkeep installer/,+1d' "$rc"
# macOS note: the default BSD sed uses `-i ''` instead of `-i`:
# sed -i '' '/# Added by mindkeep installer/,+1d' "$rc"

# Reload:
exec "$SHELL" -l
```

The `.bak` file lets you recover if you removed too much. For fish, edit `~/.config/fish/config.fish` with the same `sed` trick.

---

## 6. Verify the uninstall is clean

Run all four. All should indicate absence.

```powershell
# Windows / macOS / Linux
mindkeep --version            # ⇒ "command not found" / "not recognized"
python -m mindkeep --version  # ⇒ "No module named 'mindkeep'"
pip show mindkeep             # ⇒ "Package(s) not found"
pipx list 2>$null                 # ⇒ does not mention mindkeep
```

Then confirm the data directory is gone (only if you did §4b): use `Test-Path $env:APPDATA\mindkeep` on Windows, or `[ ! -e <path> ] && echo clean` on macOS/Linux with the per-OS path from §2.

If any check still reports presence, see §8.

---

## 7. Complete reinstall

For moving to a new machine or doing a clean major upgrade:

1. Do §2 (export all projects).
2. Do §3 + §4 + §5 on the old machine.
3. Install fresh per [INSTALL.md](../INSTALL.md) (or re-run `install.ps1` / `install.sh`).
4. Restore projects one at a time:

```bash
# macOS / Linux
for f in ~/mindkeep-backup-*/*.json; do
  hash="$(basename "$f" .json)"
  mindkeep import --project "$hash" "$f"
done
```

```powershell
# Windows
Get-ChildItem $HOME\mindkeep-backup-*\*.json | ForEach-Object {
  $hash = $_.BaseName
  mindkeep import --project $hash $_.FullName
}
```

---

## 8. Troubleshooting

### `pipx uninstall` fails or hangs

Manually delete the venv and shim:

```bash
# macOS / Linux
rm -rf ~/.local/pipx/venvs/mindkeep
rm -f  ~/.local/bin/mindkeep
```

```powershell
# Windows
Remove-Item -Recurse -Force $env:USERPROFILE\.local\pipx\venvs\mindkeep
Remove-Item -Force        $env:USERPROFILE\.local\bin\mindkeep.exe
```

### `pip` reports "package not found" but `mindkeep --version` still works

You have a **stale shim** on PATH — usually from a previous install method. Find which shim is winning:

```powershell
# Windows
Get-Command mindkeep | Format-List *

# macOS / Linux
which -a mindkeep
```

Delete the reported executable file(s), then re-check §6.

### Data directory refuses to delete ("file in use")

The `*.db-wal` / `*.db-shm` files are locked by a live SQLite connection. Stop every process using the store — editor plugins, background agents, running `mindkeep` invocations, IDE integrations — then retry. Use `handle.exe mindkeep` (Sysinternals) on Windows or `lsof | grep mindkeep` on macOS/Linux to find offending PIDs.

### Command still runs but package is gone

Your shell has cached a hashed path. Run `hash -r` (bash/zsh) or open a fresh terminal.

---

## 9. Enterprise exit procedure

For compliance teams that need proof that memory data was destroyed (e.g., offboarding, SOC 2 evidence, DSR fulfilment):

### Step 1 — Pre-deletion inventory

Capture a cryptographic fingerprint of the data directory before destroying it:

```bash
# macOS / Linux
data_dir="$(mindkeep where | awk -F': ' '/data_dir/ {print $2}')"
find "$data_dir" -type f -print0 | sort -z | xargs -0 shasum -a 256 \
  > ~/mindkeep-preuninstall-manifest.txt
```

```powershell
# Windows
$dataDir = (mindkeep where | Select-String 'data_dir:').ToString().Split(':',2)[1].Trim()
Get-ChildItem -Recurse -File $dataDir |
  ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash, $_.FullName } |
  Set-Content $HOME\mindkeep-preuninstall-manifest.txt
```

### Step 2 — Perform §3 + §4b + §5.

### Step 3 — Post-deletion attestation

```bash
# macOS / Linux — pick the per-OS default path or $MINDKEEP_HOME
[ ! -e "$data_dir" ] && printf 'ATTEST: %s absent at %s\n' "$data_dir" "$(date -u +%FT%TZ)" \
  | tee ~/mindkeep-postuninstall-attest.txt
```

```powershell
# Windows
if (-not (Test-Path $dataDir)) {
  "ATTEST: $dataDir absent at $((Get-Date).ToUniversalTime().ToString('o'))" |
    Tee-Object $HOME\mindkeep-postuninstall-attest.txt
}
```

Together, the pre-deletion manifest and post-deletion attestation satisfy "right to erasure" / "memory data permanently destroyed" audits. Sign them with your corporate PGP key if required, and retain per your data-retention policy.

---

## Questions / issues

Open an issue at <https://github.com/AllenS0104/mindkeep/issues> and include the output of `mindkeep doctor` (if the command is still available) plus your OS version.
