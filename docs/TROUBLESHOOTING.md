# 🔧 mindkeep · Troubleshooting

> **出问题了怎么办**。按 **症状** 分类，每条遵循：
> **症状** → **可能原因** → **诊断命令** → **修复命令**。
>
> 为什么「这样设计」的问题请看 [FAQ.md](./FAQ.md)。

## 目录

- [🧰 首先跑一遍 doctor](#-首先跑一遍-doctor)
- [📦 安装相关](#-安装相关)
- [▶️ 运行相关](#️-运行相关)
- [🗄 数据 / 数据库相关](#-数据--数据库相关)
- [🔌 集成相关（Python API / Jupyter / pytest / Docker）](#-集成相关python-api--jupyter--pytest--docker)
- [🚦 CI 相关](#-ci-相关)
- [🆘 兜底：实在修不好](#-兜底实在修不好)

---

## 🧰 首先跑一遍 doctor

大多数问题 **先跑这条**：

```bash
mindkeep doctor
```

它会检查：Python 版本、data_dir 可写、SQLite 编译选项、PATH、当前项目解析、WAL 是否健康。
看到 ❌ 的那一行，对应下文某个 section。

---

## 📦 安装相关

### ❌ `mindkeep: command not found` / `'mindkeep' is not recognized`

**症状**：装完后执行 `mindkeep --version` 报 not found。

**可能原因**：

1. 安装器写入的 PATH 只对**新 shell**生效，当前 shell 没 reload。
2. pipx / pip `--user` 的 scripts 目录不在 PATH 里。
3. Windows 下装到了 `%APPDATA%\Python\PythonXY\Scripts\` 却没加 PATH。

**诊断命令**：

```bash
# Linux/macOS
python -m mindkeep --version          # 绕过 PATH 验证包本身装上了
python -m mindkeep doctor             # 打印它期望的 PATH

which mindkeep || echo "not on PATH"
echo $PATH | tr ':' '\n' | grep -E 'pipx|\.local/bin'
```

```powershell
# Windows
python -m mindkeep --version
python -m mindkeep doctor
$env:Path -split ';' | Select-String 'Python|Scripts|pipx'
```

**修复命令**：

```bash
# Linux / macOS — 打开新 shell 或手动 source
source ~/.zshrc      # 或 ~/.bashrc
# 或手动导出
export PATH="$HOME/.local/bin:$PATH"       # pipx / pip --user

# pipx 自动修正
pipx ensurepath
```

```powershell
# Windows — 开新 PowerShell 窗口（安装器只改未来 shell）
# 或手动把 Scripts 目录加进 PATH：
$scripts = (python -c "import sysconfig; print(sysconfig.get_path('scripts'))")
[Environment]::SetEnvironmentVariable("Path", "$env:Path;$scripts", "User")
```

---

### ❌ Windows 装完后找不到 `mindkeep.exe`

**症状**：`pip install` / `pipx install` 成功，`mindkeep` 还是 not recognized。

**可能原因**：Python 的 `Scripts\` 目录没进 PATH（Microsoft Store 版 Python 尤其常见）。

**诊断命令**：

```powershell
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
Get-ChildItem "$(python -c 'import sysconfig; print(sysconfig.get_path(''scripts''))')\mindkeep*"
```

**修复命令**：

```powershell
# 方案 A：用 pipx（自动加 PATH）
python -m pip install --user pipx
python -m pipx ensurepath
# 关掉并重开 PowerShell
pipx install git+https://github.com/AllenS0104/mindkeep.git

# 方案 B：绕过 PATH 直接调用模块
python -m mindkeep doctor
python -m mindkeep show --kind facts
```

---

### ❌ `iwr ... | iex` 报 `UnauthorizedAccess` / `execution of scripts is disabled`

**症状**：Windows 一键安装脚本被 ExecutionPolicy 拦下。

**可能原因**：当前用户 ExecutionPolicy = `Restricted`。

**诊断命令**：

```powershell
Get-ExecutionPolicy -List
```

**修复命令**：

```powershell
# 只改当前会话，安全、不需要管理员
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 | iex

# 或：先下载再运行（见 README「Verify before running」）
iwr https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.ps1 -OutFile install.ps1
.\install.ps1
```

---

### ❌ `pipx install` 失败（企业代理 / 离线环境）

**症状**：`pipx install git+https://...` 卡在 clone 或超时。

**可能原因**：代理拦 git 协议 / 无外网 / pip 找不到 index。

**诊断命令**：

```bash
curl -I https://github.com
git ls-remote https://github.com/AllenS0104/mindkeep.git 2>&1 | head
pip config list
```

**修复命令**：

```bash
# 方案 A：配置代理（把 proxy 换成你们的）
export HTTPS_PROXY=http://proxy.corp:8080
export HTTP_PROXY=http://proxy.corp:8080
pipx install git+https://github.com/AllenS0104/mindkeep.git

# 方案 B：用发布的 wheel，完全离线
# 从 https://github.com/AllenS0104/mindkeep/releases 下载 .whl 传到目标机器
pip install --user ./mindkeep-0.2.0-py3-none-any.whl
# 或通过 pipx 指定本地 wheel
pipx install ./mindkeep-0.2.0-py3-none-any.whl

# 方案 C：企业镜像（Artifactory / Nexus）
pip install --user --index-url https://nexus.corp/simple/ mindkeep
```

---

### ❌ `curl | bash` 下载到一半断开 / 安装中断

**症状**：网络不稳，`curl | bash` 中途连接断开。

**可能原因**：`install.sh` 用 `main()` 包裹全部逻辑 + `exit` 收尾，下载不完整时
`bash` 解释到半截函数定义就 abort，**不会执行任何真实命令** —— 这是故意的防护。

**诊断命令**：

```bash
# 看下下载完整没
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | wc -c
# 应该和 GitHub 页面上 raw 文件大小一致
```

**修复命令**：

```bash
# 最简单：重跑
curl -fsSL https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh | bash

# 网络差：先落盘，校验后运行
curl -fsSL -o install.sh https://raw.githubusercontent.com/AllenS0104/mindkeep/main/install.sh
# 对照 Releases 的 SHA256SUMS
sha256sum install.sh
bash install.sh
```

---

## ▶️ 运行相关

### ⚠️ `doctor` 报 ❌ **Data dir not writable**

**症状**：`mindkeep doctor` 对 data_dir 那一行红叉。

**可能原因**（按常见程度排序）：

1. 目录权限被搞坏（曾经用 root / sudo 跑过）。
2. `MINDKEEP_HOME` 指到一个 **OneDrive / iCloud / Dropbox 同步目录** —— 云客户端锁着文件。
3. 目录在只读挂载（Docker bind-mount `:ro`、公司桌面策略）。
4. 杀毒软件实时扫描中，短暂持锁。

**诊断命令**：

```bash
mindkeep where
# 查实际路径；假设是 ~/.local/share/mindkeep
ls -la ~/.local/share/mindkeep
touch ~/.local/share/mindkeep/.probe && rm ~/.local/share/mindkeep/.probe
```

```powershell
mindkeep where
Get-Acl "$env:APPDATA\mindkeep" | Format-List
# 检查是否在 OneDrive 里
(Get-Item "$env:APPDATA\mindkeep").FullName
```

**修复命令**：

```bash
# Linux/macOS：修权限
chown -R $USER ~/.local/share/mindkeep
chmod -R u+rwX ~/.local/share/mindkeep

# 所有平台：把 data_dir 搬到非同步目录
export MINDKEEP_HOME="$HOME/.mindkeep-local"    # Linux/macOS
# Windows:
[Environment]::SetEnvironmentVariable("MINDKEEP_HOME", "C:\mindkeep-local", "User")
```

**根治建议**：永远不要把 `data_dir` 放在实时同步的云盘里，会和 SQLite WAL 打架（见 [FAQ Q8](./FAQ.md#-q8-怎么跨机器同步记忆)）。

---

### ⚠️ `list` 返回 "No projects yet"，但我明明用过

**症状**：曾经写过记忆，`mindkeep list` 空空如也。

**可能原因**：

1. `MINDKEEP_HOME` 被改了（shell 启动脚本、当前 session 临时覆盖、VSCode 自带 env）。
2. 当前 `cwd` 的 project id 和之前不一样（改了 git remote / 换了路径 / 装了 git 从无 remote 变有 remote）。
3. 数据确实没落盘（极早版本的 bug；现在 WAL + flush 不会）。

**诊断命令**：

```bash
mindkeep where                                   # 看当前 data_dir 和 project id
env | grep mindkeep                              # Linux/macOS
ls "$(mindkeep where --data-dir)/projects/"      # 看实际有哪些 db 文件
```

```powershell
mindkeep where
Get-ChildItem Env: | Where-Object Name -Like 'mindkeep*'
Get-ChildItem "$(mindkeep where --data-dir)\projects\"
```

**修复命令**：

```bash
# 如果 data_dir 被改了，恢复默认
unset MINDKEEP_HOME                              # Linux/macOS
# Windows PowerShell
Remove-Item Env:MINDKEEP_HOME

# 如果 project id 变了，旧 db 还在磁盘上，按 id 导出再导入新项目
mindkeep export --project <旧12位id> ./old.json
mindkeep import ./old.json
```

---

### ⚠️ `show` 里 value 被 `...` 截断

**症状**：长 ADR rationale / session summary 被截断显示。

**可能原因**：CLI 默认为了美观做了截断（不是数据被截断）。

**修复命令**：

```bash
mindkeep show --kind adrs --full          # 显示完整 value
mindkeep show --kind adrs --key ADR-0007  # 只显示一条，自动不截断
mindkeep export ./dump.json               # 直接看原始 JSON
```

如果 **写入时就被截断**，是你开了 `SizeLimiter`（或默认 10 000 字符上限）。
在 `MemoryStore.open(filters=[SizeLimiter(max_chars=50_000)])` 里调大。

---

### ⚠️ GitHub token 写进去没被脱敏

**症状**：`show` 出来看到 `gho_xxxxx...` 明文。

**可能原因**：

1. Token 格式不匹配内置规则（见 [FAQ Q11](./FAQ.md#-q11-secretsredactor-能-100-拦截所有密钥吗)）。
2. 写入时 filter 被关掉了（`filters=[]`）。
3. 这条记录是 **老版本** 脱敏规则升级前写的。

**诊断命令**：

```python
from mindkeep.security import SecretsRedactor
r = SecretsRedactor()
print(r.apply("fact", "value", "my token is ghp_1234567890abcdef1234567890abcdef1234"))
# 应该看到 [REDACTED:github_token]
```

**修复命令**：

```bash
# 清掉受污染的记录，重新写
mindkeep clear --kind facts --key leaked_field

# 如果是公司自研 token 格式没命中，加 custom_patterns（见 FAQ Q12）

# 撤销 GitHub token（记忆泄露 = 实际泄露）
# https://github.com/settings/tokens → revoke
```

---

## 🗄 数据 / 数据库相关

### ❌ `sqlite3.OperationalError: database is locked`

**症状**：运行时抛 `database is locked`。

**可能原因**：

1. 多个进程同时 **写** 同一项目 DB（v1 不支持，见 [FAQ Q15](./FAQ.md#-q15-多个-agent-同时写同一项目会冲突吗)）。
2. 上一次崩溃留下僵尸 `-shm` / `-wal` 文件，没有清理但也没进程持有。
3. DB 文件放在 NFS / SMB / 云同步 上（锁语义不可靠）。
4. 杀毒软件把 DB 文件开了短时读锁。

**诊断命令**：

```bash
DB=$(mindkeep where --db-file)
echo "DB path: $DB"
ls -la "$DB" "$DB-wal" "$DB-shm" 2>/dev/null
# Linux/macOS — 看还有没有进程打开
lsof "$DB" 2>/dev/null
fuser "$DB" 2>/dev/null
```

```powershell
# Windows — 用 handle.exe (Sysinternals) 或 Get-Process 粗查
$db = (mindkeep where --db-file)
Get-Item "$db*" | Format-Table Name, Length, LastWriteTime
# 看谁在用：
# handle.exe $db
```

**修复命令**：

```bash
# 关掉所有 agent / CLI 进程，然后：
mindkeep doctor                           # 自动 checkpoint + 清理

# 手动 checkpoint（没有进程在用时）
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);"

# 核选项：备份 + 重建
cp "$DB" "$DB.bak"
sqlite3 "$DB" ".recover" | sqlite3 "$DB.new"
mv "$DB.new" "$DB"
```

---

### ❌ `mindkeep import` 失败 / schema mismatch

**症状**：`import` 抛 `InvalidSchemaError` 或字段未识别警告。

**可能原因**：

1. JSON 是用 **更新版本** mindkeep 导出的，本地版本还不认识新字段。
2. JSON 被手改过，破坏了必须字段（`id`、`created_at`）。
3. JSON 是用 **更旧版本** 导出的，缺 v1 新增字段。

**诊断命令**：

```bash
# 看 JSON 里的 schema version
jq '.schema_version, .exported_by_version' dump.json

# 看你本地版本
mindkeep --version
```

**修复命令**：

```bash
# 先升级本地版本
mindkeep upgrade
mindkeep import dump.json

# 如果对方版本更老，让对方用 export --schema-version=vX 导出匹配版本
# 或者手动补字段
jq '.facts |= map(. + {tags: (.tags // [])})' dump.json > fixed.json
mindkeep import fixed.json --lenient      # 放宽未知字段的校验
```

---

### ❌ `-wal` 文件异常大（几百 MB）

**症状**：`<id>.db-wal` 比 `<id>.db` 本体大很多。

**可能原因**：

1. 进程长期没退出，也没触发 checkpoint（PRAGMA 默认是 1000 页，但超长事务会抑制）。
2. 自定义 `flush_interval` 设得很大，写多读少，WAL 一直累积。

**诊断命令**：

```bash
DB=$(mindkeep where --db-file)
ls -lh "$DB" "$DB-wal" "$DB-shm"
```

**修复命令**：

```bash
# 方案 A：优雅关闭当前进程 —— close() 触发 checkpoint

# 方案 B：运行时强制 checkpoint
python -c "
from mindkeep import MemoryStore
with MemoryStore.open() as s:
    s.checkpoint(truncate=True)
"

# 方案 C：所有进程关闭后手动
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);"
```

---

### ❌ `meta.json` 丢失 / 被删了

**症状**：`<id>.meta.json` 被误删，`list` 里项目显示成 hash 而不是可读名字。

**可能原因**：同步冲突、手动清理、杀毒隔离。

**影响**：**低**。meta.json 只保存 display_name / origin 等非关键元数据；DB 本身完整，数据不会丢。

**修复命令**：

```bash
# 下次在对应 cwd 里打开项目，meta.json 会自动重新生成
cd /path/to/that/repo
mindkeep where         # 自动写回 meta.json
```

---

## 🔌 集成相关（Python API / Jupyter / pytest / Docker）

### ⚠️ Jupyter：kernel 崩溃后数据还在吗？

**症状**：Jupyter 里用 `MemoryStore.open()` 没 `close()` 就杀了 kernel / 关了浏览器。

**可能原因**：不算 bug —— 你只是绕过了显式关闭。

**结果**：

- `FlushScheduler` 每 30s 自动 commit → **最多丢 30 秒未 commit 的写** ([FAQ Q6](./FAQ.md#-q6-cli--agent-进程突然关闭会丢数据吗最多丢多少秒))。
- `atexit` 在 kernel 干净 shutdown 时也会触发。
- SIGKILL kernel 时上面两条都跳过 → 依靠 WAL replay 保证没损坏。

**最佳实践**：

```python
# 用 context manager，kernel 死了也最多丢 30s
with MemoryStore.open() as store:
    store.remember_fact("a", "b")
    # ...

# 或长跑时手动 checkpoint
store = MemoryStore.open()
try:
    store.remember_fact("a", "b")
    store.commit()         # 显式落盘
finally:
    store.close()
```

---

### ⚠️ pytest 跑测试时污染了真实记忆

**症状**：跑项目的单测后，`mindkeep list` 多出一堆 test fixture 数据。

**可能原因**：测试代码直接 `MemoryStore.open()`，写进了真正的 `data_dir`。

**修复命令**：

```python
# conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDKEEP_HOME", str(tmp_path))
    yield
    # tmp_path 自动清理
```

```bash
# 已经被污染？删掉测试项目
mindkeep list       # 找到那些可疑的 id
mindkeep clear --project <id> --all --yes
```

---

### ⚠️ Docker 容器里数据丢了

**症状**：容器重启后记忆全没。

**可能原因**：`data_dir` 在容器内层 FS 里，容器销毁 = 数据销毁。

**修复命令**：

```bash
# 挂载 volume 到 data_dir
docker run \
  -e MINDKEEP_HOME=/data/mindkeep \
  -v mindkeep_data:/data/mindkeep \
  my-agent-image

# 或 bind-mount 到宿主机目录
docker run \
  -e MINDKEEP_HOME=/data/mindkeep \
  -v "$HOME/.local/share/mindkeep:/data/mindkeep" \
  my-agent-image
```

注意：**不要** 在容器内外同时写同一 volume（SQLite 锁不保证跨 namespace，见 [FAQ Q15](./FAQ.md#-q15-多个-agent-同时写同一项目会冲突吗)）。

---

### ⚠️ `load_project_memory()` 没正常关闭

**症状**：集成代码里用了 `load_project_memory()`（见 `integration.py`），进程退出时有 warning。

**修复**：

```python
# 推荐：context manager
from mindkeep.integration import load_project_memory
with load_project_memory() as store:
    ...

# 或手动 close
store = load_project_memory()
try:
    ...
finally:
    store.close()
```

即使忘了关，`atexit` 兜底会 commit；warning 只是提示习惯问题。

---

## 🚦 CI 相关

### ❌ GitHub Actions workflow 跑不起来

**症状**：`.github/workflows/ci.yml` 起不来，报 Python 版本不兼容或权限错误。

**可能原因**：

1. runner 用了 Python < 3.11（本仓库 CI 要求 3.11+，虽然运行时支持 3.9+）。
2. `GITHUB_TOKEN` 默认只读；如果 workflow 需要写 release / push commit，要显式声明 permissions。
3. Cache key 冲突导致拿到错版本的 data_dir。

**诊断命令**：

```bash
cat .github/workflows/ci.yml
gh run list --limit 5
gh run view --log-failed
```

**修复命令**：

```yaml
# .github/workflows/ci.yml 关键段
jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read          # 正常测试够用
      # contents: write       # 需要推 release artifact 时加
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"    # 或 matrix: ["3.9","3.10","3.11","3.12"]
      - run: pip install -e ".[dev]"
      - run: pytest -q
```

---

### ⚠️ CI 里 `mindkeep clear` 卡在确认提示

**症状**：CI job 卡住几分钟后超时。

**可能原因**：`clear` 默认交互式确认。

**修复**：

```bash
mindkeep clear --all --yes                # 跳过确认
# 或 export mindkeep_NO_CONFIRM=1
```

---

## 🆘 兜底：实在修不好

1. **收集诊断信息**：
   ```bash
   mindkeep --version
   mindkeep doctor
   mindkeep where
   python --version
   python -c "import sqlite3; print(sqlite3.sqlite_version, sqlite3.version)"
   uname -a                                           # Linux/macOS
   # Windows: [System.Environment]::OSVersion.VersionString
   ```
2. **保留证据**：
   ```bash
   cp -r "$(mindkeep where --data-dir)" /tmp/mindkeep-dump-$(date +%s)
   ```
3. **完全重置（核选项）**：
   ```bash
   # 先备份！
   mindkeep export ./full-backup.json
   rm -rf "$(mindkeep where --data-dir)"
   mindkeep doctor           # 重新初始化
   ```
4. **开 issue**：把第 1 步的输出 + 最小复现贴到
   https://github.com/AllenS0104/mindkeep/issues

---

## 还没列到的症状？

- 为什么这样设计 / 边界 / 取舍 → [FAQ.md](./FAQ.md)
- 底层契约 / 崩溃语义 / ADR → [ARCHITECTURE.md](../ARCHITECTURE.md)
- 基本用法 → [README.md](../README.md)
