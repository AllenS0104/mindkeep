# mindkeep 用户手册

> 本地优先、零依赖、崩溃安全的 Agent 长期记忆层。
> 本文件是 **参考文档 (Reference)** 与 **食谱 (Cookbook)** 的合体：既可按章节线性读完，也可随时 `Ctrl+F` 查某个 API。

---

## 目录

1. [快速概览 — 4 类记忆](#1-快速概览--4-类记忆)
2. [CLI 参考](#2-cli-参考)
   - [`mindkeep where`](#21-where)
   - [`mindkeep list`](#22-list)
   - [`mindkeep show`](#23-show)
   - [`mindkeep clear`](#24-clear)
   - [`mindkeep export`](#25-export)
   - [`mindkeep import`](#26-import)
   - [`mindkeep doctor`](#27-doctor)
   - [`mindkeep upgrade`](#28-upgrade)
   - [`--version` / `--help`](#29-version---help)
   - [退出码总览](#210-退出码总览)
3. [Python API 参考](#3-python-api-参考)
   - [便捷函数](#31-便捷函数)
   - [`MemoryStore.open()`](#32-memorystoreopen)
   - [`MemoryStore` 实例方法](#33-memorystore-实例方法)
   - [`Filter` Protocol](#34-filter-protocol)
   - [`SecretsRedactor` / `SizeLimiter`](#35-secretsredactor--sizelimiter)
4. [食谱 (Cookbook)](#4-食谱-cookbook)
   - [🧠 记录项目技术栈](#41--记录项目技术栈)
   - [📐 记录架构决策 ADR](#42--记录架构决策-adr)
   - [🎨 设置跨项目用户偏好](#43--设置跨项目用户偏好)
   - [🔄 会话总结与下次恢复](#44--会话总结与下次恢复)
   - [🏷️ 按 tag 组织与检索](#45-️-按-tag-组织与检索)
   - [📤 把项目记忆分享给队友](#46--把项目记忆分享给队友)
   - [🛡️ 自定义敏感信息过滤](#47-️-自定义敏感信息过滤)
   - [🔌 集成到已有 agent 框架](#48--集成到已有-agent-框架)
5. [Tag 命名规范](#5-tag-命名规范建议)
6. [数据模型 FAQ](#6-数据模型-faq)
7. [高级用法](#7-高级用法)
8. [与其他方案对比](#8-与其他方案对比)
9. [下一步](#9-下一步)

---

## 1. 快速概览 — 4 类记忆

mindkeep 把 agent 能"记住"的东西拆成 **4 个独立表**。挑错了会用得别扭，挑对了非常顺手。

### Fact — 事实

| 项 | 说明 |
| --- | --- |
| **是什么** | 关于当前项目的、可以用一句话说清的客观事实。 |
| **什么时候用** | Agent 首次扫描代码后记录技术栈、目录约定、构建命令等 |
| **作用域** | 仅当前项目 |
| **典型例子** | `"后端使用 FastAPI 0.104，Python 3.11"`<br>`"测试命令: pytest -q tests/"`<br>`"数据库迁移由 Alembic 管理，脚本在 alembic/versions/"` |
| **Python API** | `store.add_fact(content, tags=["stack"])` |
| **CLI 查看** | `mindkeep show --kind facts` |

### ADR — 架构决策 (Architecture Decision Record)

| 项 | 说明 |
| --- | --- |
| **是什么** | 一条完整决策：**选择了什么、为什么、考虑过的替代方案** |
| **什么时候用** | 需要保留"为什么当初选 X 而不选 Y" 的历史时 |
| **作用域** | 仅当前项目；自动分配递增 `number` |
| **典型例子** | `title="使用 SQLite 而非 PostgreSQL"`<br>`decision="本地单文件 SQLite"`<br>`rationale="单用户场景；零配置；WAL 够快"` |
| **Python API** | `save_decision(store, title, decision, rationale, tags=...)` |
| **CLI 查看** | `mindkeep show --kind adrs` |

### Preference — 用户/环境偏好

| 项 | 说明 |
| --- | --- |
| **是什么** | 用户个人口味或长期生效的环境配置 |
| **什么时候用** | 要**跨项目**共享；用户说"我一直喜欢用 pnpm" |
| **作用域** | **全局** — 所有项目共用同一个 `preferences.db` |
| **典型例子** | `"package_manager" → "pnpm"`<br>`"response_language" → "Chinese"`<br>`"commit_style" → "Conventional Commits"` |
| **Python API** | `store.set_preference(key, value)` / `store.get_preference(key)` |
| **CLI 查看** | `mindkeep show --kind preferences` |

### Session Summary — 会话摘要

| 项 | 说明 |
| --- | --- |
| **是什么** | 一次长会话结束前的**结论摘要**（不是逐字记录） |
| **什么时候用** | 长会话收尾、下次 agent 打开项目时恢复上下文 |
| **作用域** | 仅当前项目，按 `ended_at` 倒序 |
| **典型例子** | `"重构了认证模块，改用 JWT；剩余: 补单元测试"` |
| **Python API** | `store.add_session_summary(summary, started_at, ended_at, turn_count=..)` |
| **CLI 查看** | `mindkeep show --kind sessions` |

> 📌 **一句话决策树**：要跨项目？→ **Preference**。要保留"为什么"？→ **ADR**。客观事实？→ **Fact**。会话收尾？→ **Session**。

---

## 2. CLI 参考

全部子命令：

```
mindkeep <command> [options]

Commands:
  where     打印 data_dir 与当前项目 id
  list      列出所有已记忆的项目
  show      查看某项目的 facts/adrs/preferences/sessions
  clear     删除某项目的数据
  export    导出项目为 JSON
  import    从 JSON 合并或替换项目
  doctor    环境健康检查
  upgrade   升级 mindkeep 本身
```

所有错误写到 stderr，退出码 ≠ 0。详见 [退出码总览](#210-退出码总览)。

---

### 2.1 `where`

**概要**：打印 data_dir、当前 cwd、项目 id 解析结果。适合 debug "我的记忆存哪了"。

**参数**：无

**示例**：

```bash
$ mindkeep where
data_dir: C:\Users\alice\AppData\Local\mindkeep
cwd: C:\Users\alice\code\my-project
project_id: 8f3a2b1c4d5e
display_name: my-project
id_source: cwd_hash
origin: C:\Users\alice\code\my-project
```

**进阶 — 切到另一个目录看它的 id**：

```bash
$ cd C:\temp\other-repo; mindkeep where
```

**组合 — 快速打开项目 DB 所在目录**：

```powershell
PS> explorer (mindkeep where | Select-String "data_dir" -Raw).Split(": ",2)[1]
```

**退出码**：`0` 成功。

---

### 2.2 `list`

**概要**：一行一项目地列出 `data_dir` 下所有已知项目的统计。

**参数**：无

**示例**：

```bash
$ mindkeep list
project_hash | display_name | facts_count | adrs_count | prefs_count | last_sessions | db_size | updated_at
-------------+--------------+-------------+------------+-------------+---------------+---------+--------------------------
8f3a2b1c4d5e | my-project   | 12          | 3          | 5           | 2             | 56.0KB  | 2026-04-24T14:30:00+00:00
a0b1c2d3e4f5 | other-repo   | 4           | 0          | 5           | 0             | 24.0KB  | 2026-04-22T09:11:00+00:00
```

**空态**：

```bash
$ mindkeep list
No projects yet. Agents will populate memory as they work.
```

> ⚠️ `prefs_count` 在每一行显示的是**全局** preferences 总数（所有项目共享同一张表），所以多行显示相同数字是正常的。

**退出码**：`0`。

---

### 2.3 `show`

**概要**：展开一个项目的 facts / adrs / preferences / sessions。

**参数**：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--project <hash\|name>` | str | 当前 cwd | 用 12 位 hash 或 display_name 定位项目 |
| `--kind {facts,adrs,preferences,sessions,all}` | str | `all` | 要展示的类型 |
| `--tag <tag>` | str | 无 | 仅显示包含该 tag 的行 (facts/adrs) |
| `--limit <n>` | int | `20` | 每类最多显示多少行 |
| `--full` / `--no-truncate` | flag | off | 完整展示（不截断 60 字符）— 会失去列对齐 |

**基础示例**：

```bash
$ mindkeep show
project: 8f3a2b1c4d5e
== facts ==
id | key               | value                                          | tags     | updated_at
---+-------------------+------------------------------------------------+----------+--------------------------
1  | fact-ab12cd34ef56 | 后端使用 FastAPI 0.104，Python 3.11            | stack    | 2026-04-24T14:29:00+00:00
2  | fact-ff00aa11bb22 | 测试命令: pytest -q tests/                      | stack    | 2026-04-24T14:29:10+00:00

== adrs ==
number | title                            | status   | decision                  | tags         | updated_at
-------+----------------------------------+----------+---------------------------+--------------+--------------------------
1      | 使用 SQLite 而非 PostgreSQL      | accepted | 本地单文件 SQLite         | architecture | 2026-04-24T14:29:30+00:00

== preferences ==
key              | value | scope     | updated_at
-----------------+-------+-----------+--------------------------
package_manager  | pnpm  | global    | 2026-04-20T09:12:00+00:00

== sessions ==
(no rows)
```

**进阶 — 只看架构类 ADR、展开完整 decision 文本**：

```bash
$ mindkeep show --kind adrs --tag architecture --full
```

**组合 — 按 display_name 定位另一个项目**：

```bash
$ mindkeep show --project my-project --kind facts --limit 5
```

**退出码**：`0` 成功；`1` 项目找不到；`2` 存储故障；`3` 参数非法。

---

### 2.4 `clear`

**概要**：删除某项目的若干类数据。**默认要求交互确认**；用 `--yes` 跳过。

**参数**：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--project <hash\|name>` | str | 当前 cwd | 目标项目 |
| `--kind <kind>` | `facts\|adrs\|preferences\|sessions` | 全部 | 可多次传入限定到多种 |
| `--yes` | flag | off | 跳过 `[y/N]` 确认 |

**基础示例（带确认）**：

```bash
$ mindkeep clear --kind sessions
About to clear [sessions] from project 8f3a2b1c4d5e. Continue? [y/N]: y
cleared 2 rows from 8f3a2b1c4d5e
```

**进阶 — 一次清掉多类，无需确认**：

```bash
$ mindkeep clear --kind facts --kind sessions --yes
cleared 14 rows from 8f3a2b1c4d5e
```

**危险 — 全清**：

```bash
$ mindkeep clear --yes
cleared 23 rows from 8f3a2b1c4d5e
```

> ⚠️ `--kind preferences` 会清掉**所有项目**共用的偏好。谨慎。

**退出码**：`0` 成功；`1` 用户取消或找不到项目。

---

### 2.5 `export`

**概要**：把一个项目的所有表打包成 JSON。适合备份、迁移、分享。

**参数**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--project <hash\|name>` | str | 默认当前 cwd |
| `out` | 位置参数 | 输出 JSON 路径；不存在的父目录会自动创建 |

**示例**：

```bash
$ mindkeep export ./backup/my-project.json
exported project 8f3a2b1c4d5e → backup\my-project.json
```

**导出文件结构**：

```json
{
  "meta": {
    "project_hash": "8f3a2b1c4d5e",
    "schema_version": 1,
    "meta_row": { "display_name": "my-project", "..." : "..." }
  },
  "facts": [ { "id": 1, "key": "...", "value": "...", "tags": "stack", "..." : "..." } ],
  "adrs":  [ { "id": 1, "number": 1, "title": "...", "decision": "...", "..." : "..." } ],
  "preferences": [ { "id": 1, "key": "package_manager", "value": "pnpm", "..." : "..." } ],
  "sessions":    [ { "id": 1, "session_id": "sess-...", "summary": "...", "..." : "..." } ]
}
```

**退出码**：`0` 成功；`2` 存储故障。

---

### 2.6 `import`

**概要**：把 `export` 产出的 JSON 合并或替换到一个项目 DB 里。

**参数**：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--project <hash\|name>` | str | 当前 cwd | 目标项目（不存在会自动创建） |
| `in` | 位置参数 | — | 输入 JSON 路径 |
| `--merge` | flag | **默认** | 保留现有行，追加新行 |
| `--replace` | flag | off | 先清空目标项目所有业务表，再导入 |

> `--merge` 和 `--replace` 互斥。

**示例 — 合并（默认）**：

```bash
$ mindkeep import ./backup/my-project.json
imported 20 rows into 8f3a2b1c4d5e (mode=merge, skipped=0)
```

**示例 — 覆盖（慎用）**：

```bash
$ mindkeep import --replace ./backup/my-project.json
imported 20 rows into 8f3a2b1c4d5e (mode=replace, skipped=0)
```

**跨项目迁移**：

```bash
# 从老项目导出
$ mindkeep export --project old-repo out.json
# 导入到新项目
$ cd C:\code\new-repo; mindkeep import ../old-repo-backup.json
```

**出错情形**：未知列会 `warning` 跳过；主键冲突（合并模式）会 `skipped++` 并继续。

**退出码**：`0` 成功；`1` 文件读不到；`3` JSON 非法。

---

### 2.7 `doctor`

**概要**：体检报告。逐项打印 ✅/❌/⚠️。

**参数**：无。（预留 `--fix` 尚未实现。）

**示例**：

```bash
$ mindkeep doctor
mindkeep doctor
----------------------------------------
✅ Python version: 3.11.6 (>= 3.9)
✅ mindkeep installed: 0.2.0
✅ CLI on PATH: C:\Users\alice\AppData\Roaming\Python\Python311\Scripts\mindkeep.exe
✅ Data dir writable: C:\Users\alice\AppData\Local\mindkeep
✅ SQLite WAL mode supported
✅ Filters loaded: SecretsRedactor OK
✅ Current project: id=8f3a2b1c4d5e source=cwd_hash display_name=my-project
✅ Known projects: 2 DB file(s) in C:\Users\alice\AppData\Local\mindkeep
----------------------------------------
All checks passed 🎉
```

**退出码**：`0` 无 ❌（有 ⚠️ 也算通过）；`1` 有 ❌。

---

### 2.8 `upgrade`

**概要**：自动识别 pip / pipx 安装方式，升级自身。

**参数**：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--source <str>` | `git+https://github.com/AllenS0104/mindkeep.git` 或 `$mindkeep_UPGRADE_SOURCE` | 可传 `pypi` / git URL / 本地路径 |
| `--pre` | off | 允许 pre-release |
| `--dry-run` | off | 打印要执行的命令后退出 |
| `--yes` / `-y` | off | 跳过确认 |

**示例 — 预览**：

```bash
$ mindkeep upgrade --dry-run
current mindkeep version: 0.2.0
install mode detected: pipx
command: pipx install --force git+https://github.com/AllenS0104/mindkeep.git
dry-run: no changes made.
```

**示例 — 从 PyPI 升级**：

```bash
$ mindkeep upgrade --source pypi --yes
```

**示例 — 用自定义私有仓库**：

```bash
$ $env:mindkeep_UPGRADE_SOURCE = "git+https://git.internal.corp/ai/mindkeep.git"
$ mindkeep upgrade --yes
```

**退出码**：`0` 升级成功或 dry-run 结束；`1` 用户取消或命令找不到；其它 = 底层 pip/pipx 返回码。

---

### 2.9 `--version` / `--help`

```bash
$ mindkeep --version
mindkeep 0.2.0

$ mindkeep --help
usage: mindkeep [-h] [--version] <command> ...
...

$ mindkeep show --help        # 每个子命令都有独立 --help
```

---

### 2.10 退出码总览

| 码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 用户错误 / 项目找不到 / 取消 / 文件 I/O 失败 |
| `2` | 存储失败（SQLite error — 锁、损坏） |
| `3` | 参数或数据校验失败（未知 kind、非法 JSON、Filter 拒绝写入） |

---

## 3. Python API 参考

### 3.1 便捷函数

这三个函数定义在 `mindkeep.integration`，是 agent 集成的**推荐入口**。

#### `load_project_memory(...)`

```python
from pathlib import Path
from mindkeep.integration import load_project_memory
from mindkeep.memory_api import MemoryStore

def load_project_memory(
    cwd: Path | None = None,
    *,
    auto_flush: bool = True,
    data_dir: Path | None = None,
) -> MemoryStore: ...
```

- 自动挂载 `SecretsRedactor` + `SizeLimiter`（如 `mindkeep.security` 可导入）
- `auto_flush=True` 时启动后台线程每 30s 自动 `commit()`
- **返回的 store 需要 `close()`**（或用 `with` 语法）

#### `save_decision(store, title, decision, rationale="", tags=None) -> int`

`add_adr(status="accepted")` 的薄封装。返回新 rowid。

#### `recall(store, topic=None, *, fact_limit=100, session_limit=10) -> dict`

聚合快照。返回 dict 固定 4 个键：

```python
{
    "facts": [...],            # list[dict]，newest first
    "adrs": [...],             # list[dict]，number 升序
    "preferences": {...},      # dict[str, str]，全部偏好
    "recent_sessions": [...],  # list[dict]，newest first
}
```

传 `topic` 时会按 tag 精确过滤 `facts` 和 `adrs`（preferences / sessions 不带 tag，不受影响）。

---

### 3.2 `MemoryStore.open()`

```python
@classmethod
def open(
    cls,
    cwd: Path | None = None,
    data_dir: Path | None = None,
    filters: Sequence[Filter] | None = None,
    auto_flush_interval: float | None = None,
) -> "MemoryStore":
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `cwd` | `Path.cwd()` | 决定项目 id（稳定哈希当前工作目录） |
| `data_dir` | OS 约定的应用数据目录（或 `$MINDKEEP_HOME`） | SQLite 文件根目录 |
| `filters` | `None` | Filter 列表；按顺序作用于每次写入 |
| `auto_flush_interval` | `None` | 设为浮点秒数即启动后台 commit 线程；`None` 表示关闭 |

**直接用法**（不走 integration）：

```python
from mindkeep.memory_api import MemoryStore
from mindkeep.security import SecretsRedactor, SizeLimiter

with MemoryStore.open(
    filters=[SecretsRedactor(), SizeLimiter(max_chars=20_000)],
    auto_flush_interval=30.0,
) as store:
    store.add_fact("hello")
```

---

### 3.3 `MemoryStore` 实例方法

所有方法汇总：

| 方法 | 签名（摘要） | 说明 |
| --- | --- | --- |
| `project_id` | `property → ProjectId` | 当前项目标识 |
| `db_path` | `property → Path` | 底层 SQLite 文件路径 |
| `add_fact` | `(content, tags=None, source=None) -> int` | 追加事实，返回 rowid |
| `list_facts` | `(tag=None, limit=100) -> list[dict]` | 按 updated_at 倒序 |
| `add_adr` | `(title, decision, rationale, status="accepted", supersedes=None, tags=None) -> int` | 自动递增 `number` |
| `list_adrs` | `(status=None) -> list[dict]` | 按 `number` 升序 |
| `set_preference` | `(key, value, scope="project") -> None` | **upsert**；存于全局 DB |
| `get_preference` | `(key, default=None) -> str \| None` | 查一条 |
| `list_preferences` | `(*, prefix=None) -> list[dict]` | 按 updated_at 倒序 |
| `add_session_summary` | `(summary, started_at, ended_at, turn_count=0) -> int` | 返回 rowid |
| `recent_sessions` | `(limit=10) -> list[dict]` | 按 ended_at 倒序 |
| `clear` | `(kinds: list[str] \| None = None) -> int` | `None` = 清所有业务表 |
| `commit` | `() -> None` | 强制 SQLite commit |
| `close` | `() -> None` | 幂等关闭 |
| `closed` | `property → bool` | 是否已关 |
| `__enter__` / `__exit__` | — | context manager 支持 |

**行对象字段速查**：

```python
# facts 行
{"id": 1, "key": "fact-abc...", "value": "...", "tags": "stack,backend",
 "source": "agent", "confidence": 1.0,
 "created_at": "2026-04-24T14:29:00+00:00", "updated_at": "..."}

# adrs 行
{"id": 1, "number": 1, "title": "...", "status": "accepted",
 "context": "<rationale 存这里>", "decision": "...",
 "alternatives": "", "consequences": "", "supersedes": None,
 "tags": "architecture", "created_at": "...", "updated_at": "..."}

# preferences 行
{"id": 1, "key": "package_manager", "value": "pnpm", "scope": "global",
 "created_at": "...", "updated_at": "..."}

# session_summaries 行
{"id": 1, "session_id": "sess-ab12...", "summary": "...",
 "files_touched": "", "refs": '{"turn_count": 37}',
 "started_at": "...", "ended_at": "...", "created_at": "..."}
```

> 📝 **ADR 的 `rationale` 存在 `context` 列**（历史 schema 命名）；读回时请看 `row["context"]`。

---

### 3.4 `Filter` Protocol

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Filter(Protocol):
    def apply(self, kind: str, field: str, value: str) -> str: ...
```

| 入参 | 取值 |
| --- | --- |
| `kind` | `"fact"`, `"adr"`, `"session"`, `"preference"` |
| `field` | `"content"`, `"decision"`, `"rationale"`, `"summary"`, `"value"` |
| `value` | 即将落库的字符串 |

- 返回重写后的字符串；返回类型必须是 `str`，否则会抛 `TypeError`
- `raise` 任意异常 = 拒绝写入；CLI 会以退出码 `3` 上报

**最小自定义 Filter**：

```python
class UpperCaseValues:
    name = "upper"
    def apply(self, kind: str, field: str, value: str) -> str:
        return value.upper() if field == "value" else value
```

---

### 3.5 `SecretsRedactor` / `SizeLimiter`

```python
from mindkeep.security import SecretsRedactor, SizeLimiter
```

#### SecretsRedactor

```python
SecretsRedactor(
    enabled_rules: Iterable[str] | None = None,     # None = 全开
    custom_patterns: Mapping[str, str] | None = None,  # 额外规则
)
```

内置规则：`pem_private_key`, `jwt`, `github_fine_grained_pat`, `github_token`, `aws_access_key`, `google_api_key`, `slack_token`, `openai_key`, `azure_storage_key`, `aws_secret_key`（上下文感知）, `kv_secret`（`password=...` / `api_key=...` 通用扫描）。

命中后**就地替换**为 `[REDACTED:<rule>]`。对已经替换过的文本再跑一次是 no-op（idempotent）。

规则名打错会 `ValueError`（有意为之 — 不让 typo 静默放行）。

#### SizeLimiter

```python
SizeLimiter(max_chars: int = 10_000)
```

超过就裁掉，并在末尾追加 `...[truncated N chars]`。`max_chars <= 0` 直接抛错。

---

## 4. 食谱 (Cookbook)

每道菜都是**可直接复制运行**的 Python + CLI 验证。用前确保已 `pip install mindkeep`。

### 4.1 🧠 记录项目技术栈

**场景**：Agent 第一次进入一个项目，扫描完 `package.json` / `pyproject.toml`，把关键事实落盘。

```python
from mindkeep.integration import load_project_memory

with load_project_memory() as store:
    store.add_fact("后端使用 FastAPI 0.104，Python 3.11",
                   tags=["stack", "backend"])
    store.add_fact("前端使用 React 18 + Vite + TypeScript",
                   tags=["stack", "frontend"])
    store.add_fact("测试命令: pytest -q tests/",
                   tags=["stack", "test"])
    store.add_fact("启动命令: uvicorn app.main:app --reload",
                   tags=["stack", "runtime"])
```

**CLI 验证**：

```bash
$ mindkeep show --kind facts --tag stack
== facts ==
id | key               | value                          | tags              | updated_at
---+-------------------+--------------------------------+-------------------+---------------
4  | fact-...          | 启动命令: uvicorn app.main...  | stack,runtime     | 2026-04-24T...
3  | fact-...          | 测试命令: pytest -q tests/     | stack,test        | 2026-04-24T...
2  | fact-...          | 前端使用 React 18 + Vite...    | stack,frontend    | 2026-04-24T...
1  | fact-...          | 后端使用 FastAPI 0.104...      | stack,backend     | 2026-04-24T...
```

---

### 4.2 📐 记录架构决策 ADR

**场景**：架构师决定"用 SQLite 不用 Postgres"，要把理由写下来。

```python
from mindkeep.integration import load_project_memory, save_decision

with load_project_memory() as store:
    adr_id: int = save_decision(
        store,
        title="使用 SQLite 而非 PostgreSQL",
        decision="本地单文件 SQLite + WAL 模式，不起独立数据库进程",
        rationale=(
            "1) 单用户本地场景，无并发需求；"
            "2) 零配置 — 安装后立即可用；"
            "3) WAL 模式下崩溃恢复由 SQLite 自己保证；"
            "4) 考虑过 Postgres，否决原因是需要额外进程、配置、备份流程。"
        ),
        tags=["architecture", "storage"],
    )
    print(f"ADR saved, rowid={adr_id}")
```

**CLI 验证**：

```bash
$ mindkeep show --kind adrs --full
== adrs ==
number | title                            | status   | decision
-------+----------------------------------+----------+---------------------------------------
1      | 使用 SQLite 而非 PostgreSQL      | accepted | 本地单文件 SQLite + WAL 模式，...
```

---

### 4.3 🎨 设置跨项目用户偏好

**场景**：用户说"我一直用 pnpm，别帮我 `npm install`"。Preference 是**全局**的，换个项目也生效。

```python
from mindkeep.integration import load_project_memory

with load_project_memory() as store:
    store.set_preference("package_manager", "pnpm")
    store.set_preference("response_language", "Chinese")
    store.set_preference("commit_style", "Conventional Commits")

    # 读回
    assert store.get_preference("package_manager") == "pnpm"
    assert store.get_preference("missing_key", default="n/a") == "n/a"
```

**验证偏好真的跨项目**：

```bash
$ cd C:\temp\totally-different-repo
$ mindkeep show --kind preferences
== preferences ==
key                | value                 | scope   | updated_at
-------------------+-----------------------+---------+--------------
commit_style       | Conventional Commits  | project | 2026-04-24T...
response_language  | Chinese               | project | 2026-04-24T...
package_manager    | pnpm                  | project | 2026-04-24T...
```

> 💡 `scope` 列当前固定写入 `"project"`（历史 schema 残留）；**真正决定作用域的是 DB 文件位置**，preferences 始终写入全局 `preferences.db`。

---

### 4.4 🔄 会话总结与下次恢复

**场景**：一次长会话结束前，agent 写一条摘要。下次打开项目时先 `recall()` 拉出来。

```python
from datetime import datetime, timezone
from mindkeep.integration import load_project_memory, recall

started = datetime.now(timezone.utc).isoformat(timespec="seconds")

with load_project_memory() as store:
    # ... 做完一大堆工作 ...
    ended = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.add_session_summary(
        summary=(
            "重构认证模块: 把 session cookie 换成 JWT；"
            "新增 /api/auth/refresh 端点；"
            "TODO: 给 refresh 路径补单元测试"
        ),
        started_at=started,
        ended_at=ended,
        turn_count=37,
    )

# ── 下一次会话启动 ──
with load_project_memory() as store:
    snapshot = recall(store, session_limit=3)
    for s in snapshot["recent_sessions"]:
        print(f"[{s['ended_at']}] {s['summary']}")
```

**CLI 验证**：

```bash
$ mindkeep show --kind sessions --limit 3 --full
```

---

### 4.5 🏷️ 按 tag 组织与检索

**场景**：Agent 想拿到"所有跟 security 相关的事实和决策"。

```python
from mindkeep.integration import load_project_memory, recall

with load_project_memory() as store:
    store.add_fact("密码用 bcrypt cost=12 哈希", tags=["security", "auth"])
    store.add_fact("所有 API 路由强制 HTTPS", tags=["security", "network"])

    snap = recall(store, topic="security")
    print(f"facts: {len(snap['facts'])}  adrs: {len(snap['adrs'])}")
    for f in snap["facts"]:
        print(" -", f["value"])
```

**CLI 等价**：

```bash
$ mindkeep show --kind facts --tag security
$ mindkeep show --kind adrs  --tag security
```

---

### 4.6 📤 把项目记忆分享给队友

**场景**：你想把当前项目的 ADR + facts 同步给队友，让他家的 agent 也能拥有同样的背景。

**你这边（发送方）**：

```bash
$ mindkeep export ./my-project-memory.json
exported project 8f3a2b1c4d5e → my-project-memory.json
# 邮件 / Slack 把这个 JSON 发给队友
```

**队友那边（接收方）**：

```bash
$ cd C:\code\my-project      # 进入对应仓库
$ mindkeep import ./my-project-memory.json
imported 20 rows into 7c4b9a2e1f03 (mode=merge, skipped=0)
```

> ⚠️ `project_hash` 基于 cwd 路径；你俩的 cwd 不同，hash 就不同 — **但没关系**，`import` 会写入到"当前 cwd 对应的项目"。

**如果想完全替换队友本地的该项目记忆**：

```bash
$ mindkeep import --replace ./my-project-memory.json
```

---

### 4.7 🛡️ 自定义敏感信息过滤

**场景**：公司内部 token 是 `CORP-XXXXXXXXXX` 格式，内置规则没覆盖。

```python
from mindkeep.memory_api import MemoryStore
from mindkeep.security import SecretsRedactor, SizeLimiter

redactor = SecretsRedactor(
    custom_patterns={
        "corp_token": r"CORP-[A-Z0-9]{10}",
        "internal_url": r"https?://[a-z0-9.-]+\.corp\.internal(?:/\S*)?",
    },
)

with MemoryStore.open(
    filters=[redactor, SizeLimiter(max_chars=5_000)],
    auto_flush_interval=30.0,
) as store:
    store.add_fact(
        "调试 log: token=CORP-A1B2C3D4E5，目标 https://svc.corp.internal/v1/foo",
        tags=["debug"],
    )

    # 验证：落库时敏感信息已被替换
    print(store.list_facts(tag="debug")[0]["value"])
    # → "调试 log: token=[REDACTED:kv_token]，目标 [REDACTED:internal_url]"
```

**只启用部分内置规则（其它都关）**：

```python
redactor = SecretsRedactor(
    enabled_rules=["github_token", "openai_key", "kv_secret"],
)
```

打错规则名会立即 `ValueError` — 刻意设计，不让手滑放过敏感内容。

---

### 4.8 🔌 集成到已有 agent 框架

**场景**：你有一个基于 LangGraph / 自研 / 无论什么的 agent 运行时，想把 mindkeep 接进去。核心三个时机：**启动加载、写决策、会话收尾**。

```python
# 伪代码：一个最小的 agent runtime 集成
from datetime import datetime, timezone
from mindkeep.integration import load_project_memory, save_decision, recall


class MyAgentRuntime:
    def __init__(self) -> None:
        self.store = load_project_memory()  # auto_flush=True 默认开启
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def on_start(self) -> str:
        """会话开始：把已有记忆塞进 system prompt。"""
        snap = recall(self.store, session_limit=3)
        lines = ["## 项目背景（来自长期记忆）"]
        for f in snap["facts"][:10]:
            lines.append(f"- {f['value']}")
        lines.append("\n## 用户偏好")
        for k, v in snap["preferences"].items():
            lines.append(f"- {k}: {v}")
        lines.append("\n## 最近会话摘要")
        for s in snap["recent_sessions"]:
            lines.append(f"- [{s['ended_at']}] {s['summary']}")
        return "\n".join(lines)

    def on_architecture_decision(
        self, title: str, decision: str, rationale: str
    ) -> int:
        """Agent 做出架构决策时调用。"""
        return save_decision(self.store, title, decision, rationale,
                             tags=["architecture"])

    def on_shutdown(self, final_summary: str, turn_count: int) -> None:
        """会话收尾。"""
        ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.add_session_summary(
            summary=final_summary,
            started_at=self.started_at,
            ended_at=ended_at,
            turn_count=turn_count,
        )
        self.store.close()


# 使用
runtime = MyAgentRuntime()
try:
    system_prompt = runtime.on_start()
    # ... 跑 agent 循环 ...
    runtime.on_architecture_decision(
        "选用 Redis 做任务队列",
        "Redis Streams + Consumer Groups",
        "已有 Redis；需要的是轻量队列，不需要 Kafka 级别的持久化",
    )
finally:
    runtime.on_shutdown("跑通了端到端 demo；剩余 TODO: 容错重试", turn_count=52)
```

**关键要点**：

1. `load_project_memory()` 开一个 store，整个会话复用 — **不要每次工具调用都开新 store**。
2. 退出前必须 `close()`（会触发最后一次 flush）。用 `try / finally` 或 context manager。
3. 多进程 / 多 agent 并发写同一项目是安全的（SQLite WAL + 进程内锁），参见 [§7 并发](#7-高级用法)。

---

## 5. Tag 命名规范（建议）

不是强制的，但全 squad 统一命名能让 `--tag` 过滤真正有用。

| Tag | 用途 |
| --- | --- |
| `architecture` | 架构决策、模块划分 |
| `stack` | 技术栈信息（语言、框架、版本） |
| `security` | 安全相关：认证、加密、CVE 规避 |
| `user-pref` | 用户显式表达的偏好 |
| `bug` | 已修复或待跟踪的 bug |
| `deployment` | 部署、CI/CD、环境配置 |
| `learning` | 从犯错中学到的经验 |
| `performance` | 性能基线、优化点 |
| `api` | 对外接口契约 |
| `workflow` | 开发流程约定 |

写法：小写、短横线分隔、一条记录可以多个 tag（`tags=["stack", "backend"]`）。

---

## 6. 数据模型 FAQ

### Fact vs ADR — 怎么选？

- **Fact = 事实陈述**。"测试命令是 pytest -q" → 客观、无需解释。
- **ADR = 决策记录**。需要保留"**为什么**选 X 不选 Y"。一年后有人问"干嘛用 SQLite?"，答案在 ADR 而不是 Fact。

> 简单判断：如果你想写 "因为 / 不选 X 是因为 Y"，那就是 ADR。

### Preference vs Fact — 怎么选？

- **Preference 跨项目**：`package_manager=pnpm` 在所有项目都生效。
- **Fact 只当前项目**：`项目使用 pnpm` 仅此项目；换项目可能是 npm。

> 用户显式说"我一直 / 我总是 / 我喜欢" → Preference。观察到这个项目当前用 X → Fact。

### 什么时候写 session summary？

- **不是每次都写**。几轮简单问答不值得。
- **写的时机**：长会话（10+ 轮）结束、有显著决策产出、下次打开明显要接着干。
- **摘要要点**：做了什么 + 悬而未决的 TODO + 关键决策的 ADR 编号。

### ADR 可以修改吗？

API 层只提供 `add_adr`，不直接支持"改 ADR"。**惯例**：新写一条 `supersedes=<旧 number>` 的 ADR，把旧的状态逻辑上置为 superseded（目前需要 `clear` + `export/import` 手动改 `status`；自动化 API 在 roadmap）。

### Fact 的 `key` 是什么？

自动生成的 `fact-<12hex>`，**不是**你能用来查询的业务 key。查 facts 请用 `tag` 或 `list_facts()` 线性扫描。

---

## 7. 高级用法

### 环境变量

| 变量 | 作用 |
| --- | --- |
| `MINDKEEP_HOME` | 覆盖 `data_dir` 默认路径（优先于 OS 默认） |
| `mindkeep_UPGRADE_SOURCE` | `upgrade` 子命令默认安装源 |

```bash
# POSIX
export MINDKEEP_HOME="$HOME/my-memory"

# PowerShell
$env:MINDKEEP_HOME = "D:\data\mindkeep"
```

### 并发写入

- **底层**：SQLite 在 WAL 模式下允许"多读 + 单写"，跨进程安全。
- **ADR 分配 `number`**：进程内有 `threading.Lock` 保护 "read max → insert" 序列；多进程同时 `add_adr` 时，如果极端巧合命中同一 `number`，SQLite 的 `IntegrityError` 会冒泡 — 你可以捕获后重试。
- **Preferences**：用的是 `INSERT ... ON CONFLICT(key) DO UPDATE`，天然幂等无竞态。
- **建议**：多 agent 共享同一项目时，每个 agent 各自 `load_project_memory()` 开自己的 store；**不要**在进程间共享 `MemoryStore` 实例。

### 自动 flush 配置

```python
# 默认 30s（integration.load_project_memory 的默认值）
load_project_memory(auto_flush=True)

# 关闭自动 flush — 依赖显式 commit() 或 close()
load_project_memory(auto_flush=False)

# 细粒度：直接走 MemoryStore.open
MemoryStore.open(auto_flush_interval=5.0)   # 每 5 秒 flush
MemoryStore.open(auto_flush_interval=None)  # 禁用
```

> 自动 flush 只做 `commit()` — 它不会缩短 `add_*` 调用本身的耗时，只是保证 WAL 里的数据被持久化，防进程崩溃丢失。

### 排错小抄

| 症状 | 诊断命令 |
| --- | --- |
| "我写的 fact 去哪了？" | `mindkeep where` → 确认 data_dir 和 project_id |
| "SecretsRedactor 没拦住" | 检查是否用 `load_project_memory`（默认挂 Filter），而不是裸 `MemoryStore.open()` |
| "项目名乱码 / 是 hash" | 旧版 sidecar 缺 `display_name`；新版 open 一次会自动补写 |
| "clear 后数据还在" | 别忘了 `--yes` 或在提示时输入 `y` |

---

## 8. 与其他方案对比

| 维度 | mindkeep | 裸 JSON 文件 | LangChain Memory | 云端向量库 (Pinecone 等) |
| --- | --- | --- | --- | --- |
| 依赖 | 零（stdlib + SQLite） | 零 | LangChain 全家桶 | 网络 + SDK + 账号 |
| 本地运行 | ✅ | ✅ | ✅ | ❌ |
| 崩溃安全 | ✅ WAL | ❌ 易损坏 | 取决于后端 | ✅（云端） |
| 并发写 | ✅ 多进程安全 | ❌ 需自管锁 | 取决于后端 | ✅ |
| 结构化模型 | ✅ 四类 | 自己设计 | 偏对话历史 | 主要靠 vector + metadata |
| 跨项目共享 | ✅ Preferences | 手动 | 无概念 | ✅ |
| 语义检索 | ❌ | ❌ | ✅ | ✅ |
| 数据出境 | ❌ | ❌ | 看配置 | ✅ 上云 |
| 定位 | **本地、零依赖、结构化、崩溃安全** | 玩具 | 对话上下文管理 | 大规模知识库 |

**诚实的限制**：mindkeep **不做语义检索**。查找靠 tag 过滤和线性扫描；超过数万行时建议配合外部向量库使用（后者做语义召回 → 落回 mindkeep 做结构化写入）。

---

## 9. 下一步

- **FAQ** — [docs/FAQ.md](./FAQ.md)
- **故障排查** — [docs/TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
- **架构细节** — [ARCHITECTURE.md](../ARCHITECTURE.md)
- **Agent 集成协议** — [.github/agents/memory-protocol.md](../.github/agents/memory-protocol.md)
- **贡献指南** — [CONTRIBUTING.md](../CONTRIBUTING.md)

> 发现文档里写得不对、跑不通、不清楚？欢迎提 issue，让下一个阅读的人少踩坑。
