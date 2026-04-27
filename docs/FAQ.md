# ❓ mindkeep · FAQ

> 面向 **用户 / 集成者 / 贡献者** 的常见问题。
> 本文回答「**为什么这样设计 / 如何选择 / 边界在哪**」；出问题的具体修复步骤请看
> [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)。

| 分类 | 问题范围 |
|------|---------|
| [🧠 核心概念](#-核心概念) | 是什么、跟别的系统区别、项目/记忆种类如何划分 |
| [💾 数据与持久化](#-数据与持久化) | 存在哪、丢不丢、同步、清理 |
| [🔐 安全与隐私](#-安全与隐私) | 脱敏、加密、分享风险 |
| [🤝 多 Agent 协作](#-多-agent-协作) | 并发写、跨 agent 可见性、跨项目干扰 |
| [🚀 生产与扩展](#-生产与扩展) | CI、编辑器集成、路线图、替代方案、协议 |

架构深解请参考 [ARCHITECTURE.md](../ARCHITECTURE.md)，快速上手看 [README.md](../README.md)。

---

## 🧠 核心概念

<details>
<summary><strong>Q1. mindkeep 到底是什么？跟 LangChain memory / 向量数据库（Chroma、Weaviate）有什么区别？</strong></summary>

**一句话**：一个 **面向 AI 编码 agent** 的、**按项目（repo）隔离** 的、**崩溃安全** 的本地长期记忆库，
只用标准库 + SQLite。

对比：

| 维度 | mindkeep | LangChain `ConversationMemory` | 向量 DB (Chroma / Weaviate / pgvector) |
|------|-------------|-------------------------------|---------------------------------------|
| 粒度 | 结构化：**Fact / ADR / Preference / Session** | 会话级 buffer | 任意 chunk + embedding |
| 隔离 | 按 repo（git remote 或 cwd hash）自动隔离 | 进程内，无隔离 | 需手动分 collection |
| 召回 | **关键字 / 标签** 精确查询（SQL `LIKE`、`tag=`） | LRU / summary | 语义相似度 |
| 持久化 | SQLite + WAL，跨进程崩溃安全 | 默认进程内存 | 需要独立服务 |
| 依赖 | 0（运行时）/ 仅 stdlib | langchain + 全家桶 | 服务端 + 客户端 + embedding 模型 |
| 适用 | 编码助手要记住 "本项目用 Postgres 15"、"偏好单引号" | 对话上下文管理 | RAG 文档检索 |

**什么时候选 mindkeep**：你要存的是「**事实 / 决策 / 偏好**」，数量在
1k～50k 条以内，查询是关键字/标签，不需要语义模糊匹配。
**什么时候不选**：你真正要的是 RAG（几万篇文档的语义检索）——那请用向量 DB。
两者可以组合：mindkeep 存结构化记忆，向量 DB 存文档 chunk。
</details>

<details>
<summary><strong>Q2. 「项目」是如何定义的？多个文件夹属于同一 git 仓库会共享记忆吗？</strong></summary>

查 `src/mindkeep/project_id.py`。算法：

1. 从 `cwd` 向上找 `.git` 目录 / 文件；
2. 找到则 `git config --get remote.origin.url`（3 秒超时），**规范化后** sha256 取前 12 hex；
3. 找不到 / 没有 remote，则用 **规范化后的 cwd 绝对路径** sha256 取前 12 hex。

**结论**：

- **同一个 repo 的任意子目录 / worktree**：只要 `remote.origin.url` 相同，**共享同一个记忆 DB**。
- **克隆到两个路径的同一个 repo**：**共享**（都走 remote hash）。
- **没有 remote 的本地 repo / 非 git 目录**：**按绝对路径隔离**，换路径就换 DB。
- Windows 路径大小写不敏感，`C:\Foo` 和 `c:\foo` 会 hash 到同一个 id。

用 `mindkeep where` 可以看当前解析到的 project id 和 origin。
详见 [ARCHITECTURE §5](../ARCHITECTURE.md)（Project Identification Contract）和 ADR-0005。
</details>

<details>
<summary><strong>Q3. 我没有 git 仓库，只有一个普通文件夹，能用吗？</strong></summary>

能。没有 `.git` 时会 fallback 到 `sha256(绝对路径)`。

**代价**：

- 换机器路径变了 → project id 变了 → 记忆「换壳」。
- 重命名文件夹 → 同上。

如果项目本身不大却想跨机器稳定，建议 `git init` 一下哪怕没有 remote；
或者用 `mindkeep export / import` 按 JSON 手动迁移。
</details>

<details>
<summary><strong>Q4. Fact、ADR、Preference、Session Summary 分别什么时候用？</strong></summary>

| 类型 | 语义 | 举例 | 作用域 |
|------|------|------|--------|
| **Fact** | 项目客观事实，key-value + 可选 tags | `stack = "Postgres 15 + FastAPI"`、`primary_key_type = "uuid"` | 本项目 |
| **ADR** | 架构决策记录（不可变历史） | `ADR-0007: 用 RSA-256 而非 HS256 做 JWT`，附 rationale | 本项目 |
| **Preference** | 风格/工具偏好 | `style.quote = "single"`、`test.framework = "pytest"` | `scope="user"` 跨项目 / `scope="project"` 当前项目 |
| **Session Summary** | 某次 agent 会话的摘要回放 | 「今天修了 auth bug #42，改了 3 个文件，下次记得跑 `pytest -k auth`」 | 本项目 |

**判断原则**：

- 会 **变** 的客观状态 → Fact（覆盖写）
- 做了就 **不该改** 的决策 + 原因 → ADR（追加写）
- 跟项目无关的 **个人口味** → Preference `scope="user"`
- **过程性叙事**（下次需要恢复上下文）→ Session Summary

API 对照：`store.remember_fact / remember_adr / set_preference / record_session_summary`。
</details>

---

## 💾 数据与持久化

<details>
<summary><strong>Q5. 数据存在哪？能手动编辑吗？</strong></summary>

布局：

```
<data_dir>/
├── projects/
│   ├── a1b2c3d4e5f6.db          ← SQLite，每项目一个
│   └── a1b2c3d4e5f6.meta.json   ← 元数据（display_name、origin、schema_version）
└── preferences.db               ← 跨项目偏好
```

`data_dir` 默认：

- Windows：`%APPDATA%\mindkeep\`
- macOS：`~/Library/Application Support/mindkeep/`
- Linux：`$XDG_DATA_HOME/mindkeep/`（通常 `~/.local/share/mindkeep/`）
- 可用环境变量 `MINDKEEP_HOME` 覆盖。

**能手动编辑吗**？能，但建议别直接改 `.db`：

- 安全路径：`mindkeep export > dump.json` → 编辑 JSON → `mindkeep import dump.json`
- 危险路径：`sqlite3 a1b2c3d4e5f6.db` 直接改表（要先关闭所有使用该 DB 的 agent 进程，否则 WAL 可能冲突）

用 `mindkeep where` 查你机器上的真实路径。
</details>

<details>
<summary><strong>Q6. CLI / agent 进程突然关闭会丢数据吗？最多丢多少秒？</strong></summary>

**不会丢已提交的数据**。具体边界：

| 场景 | 损失 |
|------|------|
| `Ctrl-C` (SIGINT) | 0 — SIGINT handler 触发最终 commit |
| SIGTERM（`kill <pid>`） | 0 — 同上 |
| `sys.exit()` / 正常退出 | 0 — `atexit` hook 兜底 |
| `SIGKILL` / `kill -9` | **最多 30 秒**（上一次 flush 到崩溃之间尚未调用 `commit()` 的 in-memory 写） |
| 单条 `remember_fact()` 调用中间崩溃 | 要么整条生效，要么整条没写（SQLite 事务） |

「30 秒」来源：`FlushScheduler` 每 30s 调 `store.commit()`（见 `scheduler.py`），
可在 `MemoryStore.open(flush_interval=5.0)` 改小。

即使是 SIGKILL 后启动下次进程，SQLite 会 **replay WAL**，不会出现半行数据。
见 [ARCHITECTURE §7](../ARCHITECTURE.md)（Crash-Safety Semantics）。
</details>

<details>
<summary><strong>Q7. 断电 / OS 崩溃（非进程崩溃）会怎样？</strong></summary>

我们的 PRAGMA 选择是 `journal_mode=WAL, synchronous=NORMAL`（见 ADR-0002）：

- **已 `commit()` 的事务**：断电后读回来的是这份。
- **commit 过程中断电**：事务要么完整要么无，不会出现损坏。
- **理论损失窗口**：`synchronous=NORMAL` 下，OS 级 fsync 可能延迟几毫秒 ~ 几秒。
  极端场景下「最后一两个事务」可能回滚到上一个 checkpoint——
  但 **绝不会出现数据库损坏**。

如果你真的处在「核电站控制系统」级别的场景，用
`MemoryStore.open(synchronous="FULL")`（性能下降 2–3 倍），或者直接换数据库。
对 AI 编码助手而言，NORMAL 是正确权衡。
</details>

<details>
<summary><strong>Q8. 怎么跨机器同步记忆？</strong></summary>

三种方式，按轻重排列：

1. **单项目迁移（推荐）**：`mindkeep export ./mem.json` → 传 → `mindkeep import ./mem.json`
   优点：人类可读、可 `git diff`、schema 兼容有校验。

2. **整个 data_dir 拷贝**：`rsync -av ~/.local/share/mindkeep/ newhost:~/.local/share/mindkeep/`
   适合搬家整机。**注意**：两端不能同时运行 agent 访问相同 DB，否则 WAL 冲突。

3. **放在云盘（Dropbox / iCloud / OneDrive）里**：**不推荐** —— 实时同步会和 SQLite WAL 打架，
   见 [TROUBLESHOOTING — Data dir not writable](./TROUBLESHOOTING.md#️-doctor-报--data-dir-not-writable)。
   如果一定要这么做，至少确保 **同一时刻只有一台机器开 agent**。

云同步（服务端）在路线图里但 v1 没有，见 Q20。
</details>

<details>
<summary><strong>Q9. 记忆会无限增长吗？如何清理旧数据？</strong></summary>

会增长，但 **慢**。参考量级：

- 一条 Fact / Preference ≈ 200 字节
- 一条 ADR ≈ 1–5 KB（含 rationale）
- 一条 Session Summary ≈ 2–20 KB（人写的摘要）

10k 条典型记录 DB ≈ 10–50 MB，对 SQLite 来说还算小。

清理手段（组合使用）：

```bash
# 按种类清空当前项目
mindkeep clear --kind sessions

# 按标签清（例子：清掉半年前 agent 自动打的 tag）
mindkeep clear --kind facts --tag auto --before 2024-06-01

# 完全 reset 当前项目
mindkeep clear --all --yes

# 导出 → 手动裁剪 JSON → 重新导入
mindkeep export ./bak.json
jq '.sessions |= (.[-100:])' bak.json > trimmed.json   # 只留最近 100 条 session
mindkeep clear --all --yes && mindkeep import ./trimmed.json
```

`SizeLimiter` filter 可以防止单条记录爆炸（默认 10 000 字符，超出截断并打 `[truncated N chars]` 标记）。
</details>

---

## 🔐 安全与隐私

<details>
<summary><strong>Q10. 我随手丢个异常堆栈进来，密钥 / token 会被写进记忆吗？</strong></summary>

默认 **不会**。`MemoryStore` 默认开启 `SecretsRedactor`，写入前过一遍 11 类模式的正则：

- PEM 私钥（RSA/EC/DSA/OPENSSH）
- JWT
- GitHub PAT（classic `ghp_/gho_/ghs_/ghu_/ghr_` + fine-grained `github_pat_...`）
- AWS Access Key (`AKIA...`)
- AWS Secret Key（**需上下文** `aws_secret*=...`，否则不动，见 Q11）
- Google API Key (`AIza...`)
- Slack Token (`xox[baprs]-...`)
- OpenAI Key (`sk-...` / `sk-proj-...`)
- Azure Storage Key（88 字符 base64 + `==`）
- 通用 `password=... / api_key=... / token=... / secret=... / auth=...` 扫描

匹配到即替换为 `[REDACTED:<kind>]`，对 agent 读写透明。见 `src/mindkeep/security.py`。
</details>

<details>
<summary><strong>Q11. SecretsRedactor 能 100% 拦截所有密钥吗？</strong></summary>

**不能，也不要当它是银弹**。它是 **深度防御的一层**，不是唯一一层。

已知限制：

1. **AWS Secret Key 是上下文敏感的**：只有形如 `aws_secret_access_key="..."` 才会被识别；
   一条裸露的 40 字符 base64 blob 太模糊，默认不碰（误伤风险更大）。
2. **自研 token 格式**（公司内部签发的）不在规则库内。
3. **经过 base64 / url-encode / gzip 封装的凭据**不会命中。
4. **多字节截断**：极长堆栈里的 key 如果被上游工具截掉一半，正则也匹配不上。

**负责任的做法**：

- 不要把整段 `.env` / `curl -H "Authorization: ..."` 贴进来。
- 为公司内部 token 加 `custom_patterns`（Q12）。
- 把记忆目录视作 **类似 shell history** 的敏感性，做好 OS 级权限与备份管理。
</details>

<details>
<summary><strong>Q12. 如何自定义过滤规则？</strong></summary>

两种：

```python
from mindkeep import MemoryStore
from mindkeep.security import SecretsRedactor, SizeLimiter

redactor = SecretsRedactor(
    custom_patterns={
        # 公司内部 token
        "acme_internal_token": r"acme_[A-Za-z0-9]{32}",
        # 员工工号（PII）
        "employee_id": r"\bE\d{7}\b",
    },
    # 也可以白名单只启用一部分规则
    # enabled_rules=["pem_private_key", "jwt", "acme_internal_token"],
)

with MemoryStore.open(filters=[redactor, SizeLimiter(max_chars=5_000)]) as store:
    ...
```

**注意**：自定义规则名不能和内置名冲突，否则构造时就 `ValueError` 抛出（刻意 fail-loud，防止 typo 漏规则）。
</details>

<details>
<summary><strong>Q13. 记忆数据本身加密吗？</strong></summary>

**不加密**。设计上依赖 **OS 文件权限 + 用户目录隔离** 作为兜底：

- `%APPDATA%\mindkeep\` / `~/Library/...` / `~/.local/share/...` 默认只有本用户可读。
- 物理机被拖走、磁盘未加密 → 记忆可被读。与浏览器历史、shell history 同级别风险。

**如果你需要加密**：

- 短期：把 `data_dir` 放在 FileVault / BitLocker / LUKS 加密卷里。
- 长期：路线图有 SQLCipher 集成议题，但不在 v1。

**刻意选择不加密的原因**：

- 标准库 SQLite 不带加密；引入 SQLCipher 破坏「stdlib-only 运行时」契约（ADR-0003）。
- 大多数使用场景（开发机个人记忆）OS 层兜底足够。
- 让用户自己做加密卷，粒度更合适。
</details>

<details>
<summary><strong>Q14. 把记忆分享给队友（比如 commit 到 git / 发 IM），会泄露什么？</strong></summary>

即使 SecretsRedactor 把密钥擦干净了，**剩下的东西依然是「项目情报」**，分享前请过一遍：

- **Fact**：技术栈、第三方服务名、内部库名、数据库表名、`_internal_host` ——
  竞争对手 / 攻击者可拿去做 reconnaissance。
- **ADR**：架构决策 + **为什么**。比单独暴露代码更有价值。
- **Session Summary**：agent 的操作回放，可能含 bug 描述、修复路径，**等于零日披露**。
- **Preference**：个人风格，一般安全。

建议：

1. 团队内部分享 → 私有 git / 私有 Confluence 可以。
2. 公开开源 → 请 `mindkeep export` 后 **人工过一遍 JSON**；必要时 `jq` 删除 `sessions` / 某些 tag。
3. 对 LLM provider 发送前，同样过一遍。
</details>

---

## 🤝 多 Agent 协作

<details>
<summary><strong>Q15. 多个 agent 同时写同一项目会冲突吗？</strong></summary>

**单进程多 agent 线程**：安全。`MemoryStore` 内有锁，SQLite 单连接串行化。

**多进程同一 DB**：WAL 允许「多读 + 单写」，短事务基本能行；
但 **长时间并发写** 有可能报 `database is locked`。v1 的官方立场是
**单进程 per project**。见 [README FAQ](../README.md#-faq)。

实用规则：

- 一个 CLI 调用 + 一个长跑 agent 同时在 → OK，SQLite 会自己排队。
- 两个长跑 agent 高频写 → 会偶发 lock，需要你自己加进程间协调（或上 v2 的 server 模式，见 Q20）。
- **永远不要** 把同一个 `.db` 放在 NFS / 网络盘里让两台机器共用，SQLite 在网络 FS 上锁语义不保证。
</details>

<details>
<summary><strong>Q16. 怎么让 @架构师 写的 ADR 被 @代码大咖 看到？</strong></summary>

**本来就能看到**。ADR 是按 **项目** 存的（不按 agent 身份），任何 agent 在同一 repo 开 `MemoryStore` 都读到同一份。

推荐协议：

```python
# 架构师
store.remember_adr(
    id="ADR-0007",
    title="Use RSA-256 for JWT",
    rationale="...",
    author="architect",
    tags=["auth", "security"],
)

# 代码大咖 A 开工前
for adr in store.recall_adrs(tag="auth"):
    print(adr.title, adr.rationale)
```

如果要做「身份可追溯」，把 `author=` 字段用起来，配合 `recall_adrs(author="architect")` 过滤。
</details>

<details>
<summary><strong>Q17. Preference 跨项目共享，多个项目会互相干扰吗？</strong></summary>

看 **scope**：

- `set_preference("style.quote", "single", scope="user")` → 写入 `preferences.db`，**所有项目**读得到。
- `set_preference("style.quote", "double", scope="project")` → 写入当前项目 DB。

**读取优先级（查 memory_api.py `get_preference`）**：**project > user**。
也就是说：项目覆盖用户默认，用户默认兜底。

命名建议：用点分隔的命名空间 (`style.quote`, `test.framework`)，避免扁平 key 碰撞。
</details>

---

## 🚀 生产与扩展

<details>
<summary><strong>Q18. 可以在 CI 里用吗？</strong></summary>

可以。注意点：

1. CI runner 是临时的，默认 `data_dir` 每次跑都是空的——这正合适「只读」用法。
2. 如果要「携带记忆」跑 CI（比如让 agent 知道上次决策），把 `data_dir` 放在 cache 里：
   ```yaml
   - uses: actions/cache@v4
     with:
       path: ~/.local/share/mindkeep
       key: mindkeep-${{ github.ref }}
   ```
3. CI 里 **写** 进去的记忆如何回流到开发者机器？—— `mindkeep export` 作为 artifact 上传，
   开发者用 `mindkeep import` 拉下来。
4. CI 里 **不要** 依赖交互式提示（`clear --yes` 跳过确认）。
5. 版本：本仓库 CI 要求 Python 3.11+（`.github/workflows/ci.yml`），但库本身 3.9+ 兼容。
</details>

<details>
<summary><strong>Q19. 能集成到 Cursor / Cline / Copilot CLI / Claude Code 之类工具里吗？</strong></summary>

可以，两条路：

1. **子进程模式**：这些 agent 框架大多允许执行 shell。让它在每次任务开始时
   `mindkeep show --kind adrs --kind facts --limit 20`，结束时
   `mindkeep ...` 写回。零集成成本。

2. **库模式**：在 agent 的自定义 Python 工具里：
   ```python
   from mindkeep import MemoryStore
   with MemoryStore.open() as store:
       ...
   ```
   `src/mindkeep/integration.py` 里有 `load_project_memory()` 等便捷函数，
   会自动处理 cwd 解析 + 清理钩子。

我们 **不提供** IDE 插件（Cursor / VSCode）—— 保持核心小，插件由社区做。
</details>

<details>
<summary><strong>Q20. 路线图：未来会加向量检索 / 加密 / 云同步吗？</strong></summary>

非承诺，但跟踪中的议题：

| 议题 | 状态 | 取舍 |
|------|------|------|
| 向量检索（语义召回） | 🟡 观望 | 要引入 embedding 模型 → 破坏 stdlib-only 契约，可能做成可选 extra |
| SQLCipher 加密 | 🟡 观望 | 破坏「零运行时依赖」，可能做成 `[encrypted]` extra |
| 多进程 server 模式 | 🔴 明确不做 v1 | 想要多进程并发写，请自己在前面包一层 HTTP |
| 云同步（官方服务） | 🔴 不做 | 保持离线、本地拥有数据的核心价值。用 export/import + 你自己的云 |
| Web UI | 🔴 不做 | 核心太小了，做 UI 会喧宾夺主 |
| MCP server 模式 | 🟢 讨论中 | 让 Claude Desktop / Cursor 直接通过 MCP 协议访问 |

有议题想 +1 请去 GitHub issue。
</details>

<details>
<summary><strong>Q21. 对比自己写一个 JSON 文件存储，什么时候该升级到 mindkeep？</strong></summary>

**JSON 文件够用的时候**：

- < 200 条记录，单一 agent，不需要并发写。
- 你愿意自己处理：原子写、崩溃时数据不半截、并发 lock、schema 演进、脱敏。

**升级到 mindkeep 的信号**：

- 出现过「被 Ctrl-C 丢数据」或「两个进程同时写坏了文件」—— 得用 WAL。
- 记录 > 1k 条，JSON 文件 > 几 MB，读写开始明显慢 —— 得用 SQLite。
- 多个项目要隔离 —— 你要自己实现 Q2 的 project id 算法。
- 要脱敏（Q10）、要 CLI 看（`show/export`）—— 自己做要写几百行。
- 要多个 agent / session 贡献记忆 —— 需要统一 API。

基本原则：**只要你写 JSON 存储超过 100 行代码，换 mindkeep 可能更快**。
</details>

<details>
<summary><strong>Q22. 开源协议？能商用吗？</strong></summary>

**MIT**。可商用，可闭源集成，只需保留 LICENSE 文件里的版权声明。

「贡献你的改动回来」是建议不是义务。如果改出了通用功能，欢迎提 PR；
但把它嵌进你的商业产品里卖钱 —— 完全允许，这就是 MIT 的点。

详见 [LICENSE](../LICENSE)。
</details>

---

## 还没解答？

- 出错了 → [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
- 使用细节 → [README.md](../README.md)
- 底层契约 / ADR → [ARCHITECTURE.md](../ARCHITECTURE.md)
- 提 issue / PR → https://github.com/AllenS0104/mindkeep
