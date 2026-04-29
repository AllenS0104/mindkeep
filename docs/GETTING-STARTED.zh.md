# mindkeep 上手指南

> 🌐 **Languages**: [English](GETTING-STARTED.md) · **中文**

一页式端到端指南：**安装 → 使用 → 排错**。如果你以前用过 `agent-memory`，mindkeep 就是它在 v0.2.0 的更名版本，API 完全兼容，仅有命名变更。

> 想看更深入的内容？参见 [`INSTALL.md`](INSTALL.md)（4 种安装方式 + 离线安装）、[`USAGE.md`](USAGE.md)（CLI/API 完整参考 + 8 个实战 cookbook）、[`FAQ.md`](FAQ.md)、[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)、[`AUTOLOAD-SETUP.md`](AUTOLOAD-SETUP.md)。

---

## mindkeep 是什么？

一个**面向 AI 编码代理的、按项目隔离、防崩溃的长期记忆库**。可以把它理解成一个小型 SQLite 数据库——每个项目一份——记录：

- **Facts（事实）**——希望 agent 始终记住的简短陈述（例如 _"本仓库用 pnpm 而非 npm"_）。
- **ADRs（架构决策记录）**——带上下文的架构决定（_"选 PostgreSQL 而非 MySQL，因为……"_）。
- **Preferences（偏好）**——跨项目跟随你的用户级口味设置。
- **Sessions（会话）**——每个会话的可选滚动笔记。

特性：

- **纯 Python wheel，零运行时依赖**，Python ≥ 3.9，MIT 协议。
- **WAL 模式 SQLite** + 30 秒 flush 调度器 + `atexit`/`SIGTERM` 钩子 → 防崩溃。
- **按项目隔离**，project_id = `sha256(git-remote || abs-path)[:12]` → 任何目录都能用，包括没有 `.git` 的空文件夹。
- **密钥脱敏器**内置 11 种模式（PEM、JWT、GitHub PAT、AWS、OpenAI、Slack 等），写入前自动清洗敏感字符串。

---

## 1. 安装

### 推荐方式：pipx（独立 venv，自动加入 PATH）

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash

# Windows PowerShell
iwr -UseBasicParsing https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex
```

两个安装脚本会：

1. 检查 Python ≥ 3.9。
2. 缺 `pipx` 就自动 bootstrap。
3. 执行 `pipx install mindkeep`。
4. 必要时把 `pipx` 的 bin 目录加进 `PATH`。
5. 最后跑 `mindkeep doctor` 验证。

### 手动安装

```bash
pipx install mindkeep      # 推荐
# 或：
pip install --user mindkeep
```

### 验证

```bash
mindkeep --version          # → 0.2.0（或更新）
mindkeep doctor             # 全绿
mindkeep where              # 打印当前目录的 data_dir + project_id
```

如果安装后 `mindkeep` 找不到，是你的 shell 还没刷新 PATH。开个新终端，或运行 `pipx ensurepath` 后重启。

### 升级 / 卸载

```bash
pipx upgrade mindkeep
mindkeep upgrade           # 自动识别 pipx vs pip 并重装
pipx uninstall mindkeep
```

---

## 2. 使用

### CLI 速通

```bash
# 记一条事实
mindkeep fact add "本仓库通过 'make release' 发布，不走 CI"

# 记一条架构决策
mindkeep adr add "选 Pydantic v2 而非 attrs 做运行时校验"

# 召回（autoload 风格）
mindkeep show --kind facts
mindkeep show --kind adrs
mindkeep show --kind sessions --limit 3

# 搜索
mindkeep show --kind facts --grep deploy

# JSON 导出 / 导入（可移植、可 diff 的备份）
mindkeep export > backup.json
mindkeep import backup.json
```

> ⚠️ 子命令是**flag 风格**：`mindkeep show --kind facts`。`mindkeep show facts` 只会打印帮助。

### 数据存在哪？

```bash
mindkeep where
```

输出对应平台的数据目录：

| 操作系统 | 默认 `data_dir` |
|---|---|
| Windows | `%APPDATA%\mindkeep\` |
| macOS | `~/Library/Application Support/mindkeep/` |
| Linux | `$XDG_DATA_HOME/mindkeep/`（兜底 `~/.local/share/mindkeep/`）|

可以用 `MINDKEEP_HOME=/some/path` 覆盖。

### Python API

```python
from mindkeep import MemoryStore

store = MemoryStore()                              # 自动检测当前项目
store.add_fact("使用 pnpm，不用 npm", tags=["build"])
store.add_adr("选用 PostgreSQL", context="MySQL 的 JSONB 不够用")

for f in store.recall_facts(query="pnpm"):
    print(f.text)
```

store 在多进程并发下安全（WAL 模式），且对短生命周期的 agent 运行友好（30 秒定时 flush + 退出 flush）。

### 项目级 vs 全局

- **项目级**：facts / ADRs / sessions，按 `project_id` 隔离，每个项目一个 SQLite 文件。
- **全局**：preferences 单独存在 `preferences.db`，跨项目跟随你。

---

## 3. 接入 AI agent（自动加载）

mindkeep 的核心价值在于：召回**不需要你记得手动调**。完整方案见 [`AUTOLOAD-SETUP.md`](AUTOLOAD-SETUP.md)。简短版：

把下面这段贴进你 AI 工具的全局指令文件（`~/.copilot/AGENTS.md`、`~/.claude/claude.md`、`~/.cursor/rules/global.mdc` 等）：

```markdown
## 🧠 mindkeep autoload

每次会话开始、回应用户之前：

1. 运行 `mindkeep show --kind facts` 和 `mindkeep show --kind adrs`。
2. 任一返回了内容，就打印一行：`🧠 mindkeep recall: N facts, M ADRs loaded`。
3. 把结果当作权威项目上下文。
4. 如果 `mindkeep` 不在 PATH 上，静默跳过。

只在以下情况跳过 autoload：cwd 是 `$HOME` / `%USERPROFILE%` 或临时目录。
**不要**要求 `.git` 或 `pyproject.toml`——mindkeep 在任何目录都能用。

当用户要求"记住"什么的时候，使用：
- `mindkeep fact add "..."`
- `mindkeep adr add "..."`
```

贴完后，在任何目录开个新会话（哪怕是空文件夹）——它应该在第一轮就打出召回行。

---

## 4. 常见问答

### 安装 / 配置

**Q. 安装完 `mindkeep` 找不到。**
`pipx` 的 bin 目录没在 `PATH` 里。运行 `pipx ensurepath` 后重启终端。Windows 上若仍未刷新，注销后重新登录。

**Q. 我以前装了 `agent-memory`，怎么迁移？**
```bash
pipx uninstall agent-memory
pipx install mindkeep
# 迁移历史数据：
#   Linux:   mv "$XDG_DATA_HOME/agent-memory" "$XDG_DATA_HOME/mindkeep"
#   macOS:   mv "~/Library/Application Support/agent-memory" "~/Library/Application Support/mindkeep"
#   Windows: Move-Item "$env:APPDATA\agent-memory" "$env:APPDATA\mindkeep"
mindkeep doctor
```
环境变量也跟着改名：`AGENT_MEMORY_HOME` → `MINDKEEP_HOME`，等等。

**Q. 内网 / 离线环境怎么装？**
从 GitHub Releases 下载 wheel + `SHA256SUMS`，校验后 `pipx install ./mindkeep-0.2.0-py3-none-any.whl`。完整企业流程见 `INSTALL.md`。

### 使用

**Q. 为什么是 `mindkeep show --kind facts` 而不是 `mindkeep show facts`？**
CLI 用 flag 风格的子命令，这样同一个 `show` 命令能定位到不同 kind 而不歧义。位置参数形式从来没支持过。

**Q. 在没 `.git` 的空文件夹也能用吗？**
能。project_id = `sha256(git-remote || abs-path)[:12]`，没 git remote 就用路径算。所以 autoload **不该**要求项目标记文件。

**Q. facts 会跨项目共享吗？**
不会——facts/ADRs/sessions 严格按 project_id 隔离。只有 `preferences` 是全局的。

**Q. 怎么清空某个项目的记忆？**
```bash
mindkeep clear --kind facts --confirm
mindkeep clear --kind adrs --confirm
# 或者全清：
mindkeep clear --all --confirm
```

### 安全

**Q. mindkeep 会记下我的 API key / 密码吗？**
内置 `SecretsRedactor` 在写入前会清洗 11 种模式——PEM 私钥、JWT、GitHub PAT（classic + fine-grained）、AWS access & secret key、Google API key、Slack token、OpenAI key、Azure storage key，外加通用的 `password|token|api_key=…` 扫描。它不能替代谨慎使用，但能挡住绝大多数常见情况。

**Q. SQLite 文件加密了吗？**
没加密。文件放在你的用户级数据目录下，依赖文件系统权限。如果需要静态加密，请用全盘加密，或把 `MINDKEEP_HOME` 指向加密分区。

**Q. 怎么校验 release 产物？**
每个 release 都附带 `SHA256SUMS` 文件，覆盖 wheel、sdist、安装脚本。Linux/macOS 跑 `sha256sum -c SHA256SUMS`，Windows 用 PowerShell 等价命令。

### 排错

**Q. `mindkeep doctor` 报 "WAL not supported"。**
你的文件系统（某些网络挂载、某些沙箱路径）不支持 WAL。把 `MINDKEEP_HOME` 移到本地磁盘。

**Q. 出现 `database is locked` 错误。**
基本上都是瞬时：另一进程正在写。mindkeep 会自动重试。如果持续，检查是否有僵尸进程占着 `*.db-wal`。最后手段：`mindkeep doctor --repair`。

**Q. autoload 跑了但从不打召回行。**
要么数据为空（`mindkeep show --kind facts` 返回空——这是正确的，agent 应静默跳过该行），要么你的 agent 指令文件没被加载。检查工具实际读取的文件路径，并用 `cat ~/.copilot/AGENTS.md | grep mindkeep` 确认。

**Q. 我看到了别的项目的 facts。**
autoload 的跳过规则没盖住你 shell 的 home 目录。在 agent 指令里把 `$HOME`（Windows 是 `%USERPROFILE%`）加进跳过列表。

### 内部机制

**Q. `project_id` 怎么算？**
`sha256(canonical_origin)[:12]`，其中 `canonical_origin` 是小写化的 `git config remote.origin.url`（有则用），否则是目录的绝对路径。只要 git remote 不变，工作副本路径改名也稳定。

**Q. 磁盘上的目录结构？**
```
$MINDKEEP_HOME/
├── projects/<project_id>/store.db        # WAL 模式 SQLite，每项目一个
├── projects/<project_id>/.meta.json      # 原子重命名元数据
└── preferences.db                        # 全局偏好
```

**Q. 能在多台机器间同步 mindkeep 吗？**
能——把 `MINDKEEP_HOME` 指向同步目录（Dropbox、OneDrive、syncthing）。但要注意：SQLite 配合云同步可能 race，明确传输优先用 `mindkeep export` / `import`。

---

## 下一步去哪

- 完整 CLI 参考 + 8 个 cookbook → [`USAGE.md`](USAGE.md)
- 4 种安装方式 + 离线安装 → [`INSTALL.md`](INSTALL.md)
- 22 题深度 FAQ → [`FAQ.md`](FAQ.md)
- 19 个症状的排错手册 → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- Agent autoload 模式 → [`AUTOLOAD-SETUP.md`](AUTOLOAD-SETUP.md)
- 备份 / 卸载 / 合规退出 → [`UNINSTALL.md`](UNINSTALL.md)
