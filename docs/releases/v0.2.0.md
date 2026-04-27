# mindkeep v0.2.0 — initial release (rebranded from agent-memory)

Same crash-safe per-project memory engine, fresh identity.

## What changed from agent-memory v0.1.5

- Package renamed: `agent_memory` → `mindkeep`
- CLI renamed: `agent-memory` → `mindkeep`
- Data dir: `%APPDATA%\agent-memory` → `%APPDATA%\mindkeep` (no auto-migration; legacy users on Windows can move the folder; macOS/Linux: `~/.local/share/agent-memory` → `~/.local/share/mindkeep`)
- Env var: `AGENT_MEMORY_HOME` → `MINDKEEP_HOME`
- Public APIs unchanged: `MemoryStore`, `add_fact` / `remember_fact` / `list_facts` / `recall_facts`, `add_adr` / `remember_adr` / `list_adrs` / `recall_adrs`, etc.

Legacy v0.1.x source, tags, and release artifacts have been archived privately at `AllenS0104/mindkeep-archive`.

Tests: **159 passing** (identical suite to agent-memory v0.1.5).

## 🔐 SHA256 checksums

```
31d8a16d7e72bf944201c692f7f2e28909a055df03c7e96e4c6a8abd0e77a735  mindkeep-0.2.0-py3-none-any.whl
317ad2748f99dc4a9ab920d1d7ec1ad6971429287527257bbe9e9400784f0de3  mindkeep-0.2.0.tar.gz
c50ab7d9093877043c7d57d40a6011e5be9439ac26d7e673b22fcf15492e4891  install.ps1
dd305adeac6e56d046af681654d88835c94ad1fdba012b733a756139aec9af50  install.sh
```

### Verify before running

**Windows (PowerShell)**
```powershell
iwr https://github.com/AllenS0104/mindkeep/releases/latest/download/install.ps1 -OutFile install.ps1
iwr https://github.com/AllenS0104/mindkeep/releases/latest/download/SHA256SUMS -OutFile SHA256SUMS
(Get-FileHash -Algorithm SHA256 install.ps1).Hash.ToLower()
# Compare with the SHA256SUMS line for install.ps1
```

**macOS / Linux**
```bash
curl -fsSL -o install.sh https://github.com/AllenS0104/mindkeep/releases/latest/download/install.sh
curl -fsSL -o SHA256SUMS https://github.com/AllenS0104/mindkeep/releases/latest/download/SHA256SUMS
shasum -a 256 -c SHA256SUMS --ignore-missing
# install.sh: OK
```

## 📦 Install

**Windows (PowerShell)**
```powershell
iwr https://github.com/AllenS0104/mindkeep/releases/latest/download/install.ps1 -OutFile install.ps1
.\install.ps1
mindkeep --version    # → mindkeep 0.2.0
```

**macOS / Linux**
```bash
curl -fsSL https://github.com/AllenS0104/mindkeep/releases/latest/download/install.sh | bash
mindkeep --version    # → mindkeep 0.2.0
```

**From wheel (any OS, air-gapped friendly)**
```bash
pip install --user https://github.com/AllenS0104/mindkeep/releases/latest/download/mindkeep-0.2.0-py3-none-any.whl
```

## 🔗 Links

- Install guide: [docs/INSTALL.md](./docs/INSTALL.md)
- Usage: [docs/USAGE.md](./docs/USAGE.md)
- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)
