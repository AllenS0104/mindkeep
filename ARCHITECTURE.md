# MindKeep — Architecture Contract

**Status**: Accepted (frozen for implementation)
**Owner**: Architect
**Audience**: coder-a (core logic), coder-b (tests), db (schema), opsmaster (packaging), agent-integration author
**Language**: Python 3.11+ · stdlib only (no runtime deps)

> This document is the **single source of truth**. All downstream agents MUST conform. Any deviation requires a new ADR and architect approval.

---

## 1. Scope & Goals

A cross-project, on-disk **memory store** for AI coding agents (the `.github/agents/*` team). It persists:

1. **Project facts** — stable truths about a codebase ("tests live in `/tests`", "uses pnpm").
2. **ADRs** — architecture decisions captured during sessions.
3. **User preferences** — user-level style / workflow preferences that cross projects.
4. **Session summaries** — compressed per-session recaps for future context replay.

**Non-goals** (v1):
- No semantic / vector search. Keyword + tag filtering only.
- No sync / cloud. Local-first, per-machine.
- No multi-writer concurrency across processes (single-process-per-project expected; SQLite WAL makes concurrent read+write safe within reason).

---

## 2. Directory Structure

```
mindkeep/
├── ARCHITECTURE.md                    # this file (frozen contract)
├── README.md                          # user-facing quickstart
├── pyproject.toml                     # packaging, entrypoint = mindkeep
├── src/
│   └── mindkeep/
│       ├── __init__.py                # re-exports MemoryStore, ProjectId, Filter
│       ├── __main__.py                # python -m mindkeep → CLI
│       ├── store.py                   # MemoryStore (public API)
│       ├── schema.py                  # SQL DDL constants + migrate()
│       ├── project.py                 # resolve_project_id()
│       ├── paths.py                   # data_dir(), db_path_for()
│       ├── models.py                  # dataclasses: Fact, ADR, Preference, SessionSummary, ProjectId
│       ├── filters.py                 # Filter Protocol + default filters (secrets redactor)
│       ├── safety.py                  # flush scheduler, atexit + signal hooks, atomic rename
│       ├── cli.py                     # argparse-based CLI dispatcher
│       └── errors.py                  # exception hierarchy
├── tests/
│   ├── unit/
│   │   ├── test_project.py
│   │   ├── test_schema.py
│   │   ├── test_filters.py
│   │   └── test_store_crud.py
│   ├── integration/
│   │   ├── test_cli.py
│   │   └── test_cross_project.py
│   └── crash/
│       └── test_crash_safety.py       # subprocess + SIGKILL simulations
└── .github/
    └── agents/
        └── memory-protocol.md          # written by agent-integration-hook todo
```

**Rules**:
- `src/` layout (not flat) to prevent import-from-cwd bugs.
- Public imports: `from mindkeep import MemoryStore, ProjectId, Filter`.
- Anything not re-exported in `__init__.py` is internal (may change without ADR).

---

## 3. On-Disk Layout

### 3.1 Data directory resolution (`paths.data_dir()`)

Priority order:
1. `$MINDKEEP_HOME` env var (if set, use as-is).
2. Windows: `%APPDATA%\mindkeep\` (typically `C:\Users\<u>\AppData\Roaming\mindkeep`).
3. macOS: `~/Library/Application Support/mindkeep/`.
4. Linux: `$XDG_DATA_HOME/mindkeep/` or `~/.local/share/mindkeep/`.

Created with `mkdir(parents=True, exist_ok=True)` on first access. All paths via `pathlib.Path`.

### 3.2 Per-project DB file

`<data_dir>/projects/<project-hash>.db` where `<project-hash>` is the 12-char identifier from `resolve_project_id` (see §5).

Co-located sidecar: `<project-hash>.meta.json` (human-readable mirror of the `meta` table — optional, written on `close()`, used by `mindkeep list` for fast enumeration without opening every DB).

Global file: `<data_dir>/preferences.db` — holds cross-project `preferences` only (single DB, shared across all projects).

---

## 4. SQLite Schema (frozen DDL)

All per-project DBs share this schema. Schema version lives in `meta`. Migrations are forward-only and executed in `schema.migrate(conn)` on every `MemoryStore.__init__`.

```sql
-- ─────────── meta (singleton row, id=1) ───────────
CREATE TABLE IF NOT EXISTS meta (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version    INTEGER NOT NULL,
    project_id        TEXT    NOT NULL,     -- 12-char hash
    display_name      TEXT    NOT NULL,     -- human-readable (repo name or cwd basename)
    id_source         TEXT    NOT NULL,     -- 'git_remote' | 'cwd_hash'
    origin_value      TEXT    NOT NULL,     -- the git URL or absolute cwd that produced the id
    created_at        TEXT    NOT NULL,     -- ISO-8601 UTC
    updated_at        TEXT    NOT NULL
);

-- ─────────── facts ───────────
CREATE TABLE IF NOT EXISTS facts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT    NOT NULL,          -- dotted namespace, e.g. "build.tool"
    value        TEXT    NOT NULL,          -- free-form, UTF-8
    tags         TEXT    NOT NULL DEFAULT '',   -- comma-separated, lowercase
    source       TEXT    NOT NULL DEFAULT 'agent',  -- 'agent' | 'user' | 'import'
    confidence   REAL    NOT NULL DEFAULT 1.0,      -- 0.0–1.0
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(key)
);
CREATE INDEX IF NOT EXISTS idx_facts_tags    ON facts(tags);
CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_at DESC);

-- ─────────── adrs ───────────
CREATE TABLE IF NOT EXISTS adrs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    number       INTEGER NOT NULL,          -- ADR-NNN sequence, per project
    title        TEXT    NOT NULL,
    status       TEXT    NOT NULL,          -- 'proposed' | 'accepted' | 'deprecated' | 'superseded'
    context      TEXT    NOT NULL,
    decision     TEXT    NOT NULL,
    alternatives TEXT    NOT NULL DEFAULT '',
    consequences TEXT    NOT NULL DEFAULT '',
    supersedes   INTEGER,                    -- FK to adrs.id, nullable
    tags         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE(number),
    FOREIGN KEY (supersedes) REFERENCES adrs(id)
);
CREATE INDEX IF NOT EXISTS idx_adrs_status ON adrs(status);
CREATE INDEX IF NOT EXISTS idx_adrs_tags   ON adrs(tags);

-- ─────────── session_summaries ───────────
CREATE TABLE IF NOT EXISTS session_summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT    NOT NULL,           -- opaque, provided by caller
    summary      TEXT    NOT NULL,           -- compressed recap (<= ~4KB)
    files_touched TEXT   NOT NULL DEFAULT '', -- newline-separated relative paths
    refs         TEXT    NOT NULL DEFAULT '', -- JSON array of {type,value}: PR/issue/commit
    started_at   TEXT    NOT NULL,
    ended_at     TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    UNIQUE(session_id)
);
CREATE INDEX IF NOT EXISTS idx_sess_ended ON session_summaries(ended_at DESC);
```

### 4.1 Global preferences DB (`preferences.db`)

```sql
CREATE TABLE IF NOT EXISTS meta (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT    NOT NULL UNIQUE,    -- e.g. "ui.language", "style.python.line_length"
    value        TEXT    NOT NULL,
    scope        TEXT    NOT NULL DEFAULT 'user',  -- future: 'user'|'machine'
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prefs_updated ON preferences(updated_at DESC);
```

### 4.2 Schema version

Start at `SCHEMA_VERSION = 1` (constant in `schema.py`). `migrate()` is a no-op if `meta.schema_version == SCHEMA_VERSION`. Future upgrades append `ALTER TABLE` blocks guarded by version checks.

---

## 5. Project Identification Contract

```python
# models.py
from dataclasses import dataclass
from typing import Literal

IdSource = Literal["git_remote", "cwd_hash"]

@dataclass(frozen=True, slots=True)
class ProjectId:
    id: str              # always 12 lowercase hex chars
    display_name: str    # human-friendly; never used as path/key
    source: IdSource
    origin: str          # the raw git URL or cwd that was hashed
```

```python
# project.py
def resolve_project_id(cwd: Path | None = None) -> ProjectId: ...
```

**Algorithm** (deterministic, no network):

1. `cwd = cwd or Path.cwd()`; resolve to absolute via `cwd.resolve()`.
2. Walk up looking for `.git` (file or dir). If found:
   a. Try `git -C <root> config --get remote.origin.url` via `subprocess.run` (timeout 2s, `check=False`).
   b. If exit 0 and non-empty, **normalize** the URL (see below) → `origin_value`. Set `source = "git_remote"`, `display_name = last path segment of URL without .git`.
3. **Fallback**: `source = "cwd_hash"`, `origin_value = str(cwd)` (absolute, forward-slashed), `display_name = cwd.name`.
4. `id = sha256(origin_value.encode("utf-8")).hexdigest()[:12]`.

**URL normalization** (stable across git/https/ssh forms):
- Strip trailing `.git`.
- Lowercase host.
- Convert `git@host:owner/repo` → `host/owner/repo`.
- Convert `https://host/owner/repo` → `host/owner/repo`.
- Strip auth (`user:pass@`) and query/fragment.

This guarantees `git clone git@github.com:foo/bar.git` and `https://github.com/foo/bar` produce the same `id`.

---

## 6. Public API — `MemoryStore`

```python
# store.py
from pathlib import Path
from typing import Iterable, Sequence
from .models import Fact, ADR, Preference, SessionSummary, ProjectId
from .filters import Filter

class MemoryStore:
    # ---- Construction ----
    def __init__(
        self,
        project_id: ProjectId | None = None,
        *,
        cwd: Path | None = None,
        filters: Sequence[Filter] = (),
        flush_interval: float = 30.0,
        read_only: bool = False,
    ) -> None: ...
    # If project_id is None, calls resolve_project_id(cwd).
    # Opens (or creates) the per-project DB, runs migrate(), installs atexit + signal hooks.

    @classmethod
    def open(cls, cwd: Path | None = None, **kwargs) -> "MemoryStore": ...

    # ---- Context manager ----
    def __enter__(self) -> "MemoryStore": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...

    # ---- Lifecycle ----
    def flush(self) -> None: ...      # checkpoint WAL + commit pending
    def close(self) -> None: ...      # flush, write sidecar meta.json, release locks

    # ---- Meta ----
    @property
    def project(self) -> ProjectId: ...

    # ---- Facts ----
    def put_fact(self, key: str, value: str, *, tags: Iterable[str] = (),
                 source: str = "agent", confidence: float = 1.0) -> Fact: ...
    def get_fact(self, key: str) -> Fact | None: ...
    def list_facts(self, *, tag: str | None = None, limit: int = 100,
                   offset: int = 0) -> list[Fact]: ...
    def delete_fact(self, key: str) -> bool: ...

    # ---- ADRs ----
    def add_adr(self, title: str, context: str, decision: str, *,
                status: str = "accepted", alternatives: str = "",
                consequences: str = "", supersedes: int | None = None,
                tags: Iterable[str] = ()) -> ADR: ...
    def get_adr(self, number: int) -> ADR | None: ...
    def list_adrs(self, *, status: str | None = None, tag: str | None = None,
                  limit: int = 100) -> list[ADR]: ...
    def update_adr_status(self, number: int, status: str) -> ADR: ...

    # ---- Preferences (cross-project, writes to preferences.db) ----
    def put_preference(self, key: str, value: str, *, scope: str = "user") -> Preference: ...
    def get_preference(self, key: str) -> Preference | None: ...
    def list_preferences(self, *, prefix: str | None = None) -> list[Preference]: ...
    def delete_preference(self, key: str) -> bool: ...

    # ---- Session summaries ----
    def add_session_summary(self, session_id: str, summary: str, *,
                            started_at: str, ended_at: str,
                            files_touched: Iterable[str] = (),
                            refs: Iterable[dict] = ()) -> SessionSummary: ...
    def list_session_summaries(self, *, limit: int = 20) -> list[SessionSummary]: ...

    # ---- Search (simple) ----
    def search(self, query: str, *, kinds: Sequence[str] = ("fact","adr","session"),
               limit: int = 50) -> list[dict]: ...
    # LIKE-based case-insensitive over text columns. Returns heterogeneous dicts with
    # {'kind': 'fact'|'adr'|'session', 'id': ..., 'snippet': ..., 'score': ...}.

    # ---- Bulk ----
    def export(self, dest: Path) -> Path: ...   # writes JSON file
    def import_(self, src: Path, *, merge: bool = True) -> None: ...  # reads JSON
    def clear(self, *, kinds: Sequence[str] = ("fact","adr","session")) -> None: ...
```

### 6.1 Data model dataclasses (`models.py`)

```python
@dataclass(frozen=True, slots=True)
class Fact:
    id: int; key: str; value: str; tags: tuple[str, ...]
    source: str; confidence: float
    created_at: str; updated_at: str

@dataclass(frozen=True, slots=True)
class ADR:
    id: int; number: int; title: str; status: str
    context: str; decision: str; alternatives: str; consequences: str
    supersedes: int | None; tags: tuple[str, ...]
    created_at: str; updated_at: str

@dataclass(frozen=True, slots=True)
class Preference:
    id: int; key: str; value: str; scope: str
    created_at: str; updated_at: str

@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: int; session_id: str; summary: str
    files_touched: tuple[str, ...]; refs: tuple[dict, ...]
    started_at: str; ended_at: str; created_at: str
```

### 6.2 Exceptions (`errors.py`)

```
AgentMemoryError                   # base
├── ProjectResolutionError
├── SchemaMigrationError
├── FilterRejectedError            # raised when a Filter returns REJECT
├── NotFoundError                  # get_* helpers raise only on explicit require=True variants; default returns None
├── DuplicateError                 # UNIQUE constraint violations surfaced semantically
└── StorageError                   # any other sqlite3.Error wrapped
```

All public methods may raise `StorageError`; write methods may raise `FilterRejectedError` and `DuplicateError`. Document on every method.

---

## 7. Crash-Safety Semantics

### 7.1 SQLite PRAGMAs (applied on every connection open)

```
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;        -- durable across app crashes; survives OS crash with last tx possibly lost
PRAGMA wal_autocheckpoint = 1000;    -- pages
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;          -- ms
PRAGMA temp_store = MEMORY;
```

Rationale for `synchronous=NORMAL` (not FULL): MindKeep is advisory, not financial. NORMAL is 2–10× faster on Windows and still durable for app crashes (WAL fsync on commit boundary). Documented in ADR-0002.

### 7.2 Flush scheduler (`safety.py`)

- A single `threading.Timer`-based scheduler per `MemoryStore` instance, interval = `flush_interval` (default 30s).
- `flush()` performs: `conn.commit()` then `PRAGMA wal_checkpoint(PASSIVE)`.
- The scheduler is a **daemon thread** (does not block interpreter exit).
- On any write (`put_fact`, `add_adr`, etc.), transactions are committed immediately — the flush timer only forces `wal_checkpoint` so crashes don't lose the WAL tail to an unmerged state.

### 7.3 Exit hook ordering

Registered on first `MemoryStore.__init__`, de-duplicated globally:

1. `atexit.register(close_all)` — iterates a weak set of live stores, calls `close()` on each.
2. `signal.signal(SIGTERM, handler)` (POSIX) / `SIGBREAK` (Windows) → calls `close_all()` then re-raises default.
3. `signal.signal(SIGINT, handler)` → same as above, then `raise KeyboardInterrupt`.

`close()` is idempotent and thread-safe (guarded by an `RLock`). Order within `close()`:
1. Cancel flush timer.
2. Final `commit()`.
3. `PRAGMA wal_checkpoint(TRUNCATE)`.
4. Write sidecar `<hash>.meta.json` via **atomic rename**: write `<hash>.meta.json.tmp.<pid>`, `fsync`, then `os.replace` to target.
5. Close connection.

### 7.4 Atomic write helper

All file writes outside SQLite (sidecar JSON, export files) go through `safety.atomic_write(path, data: bytes)`:
```
tmp = path.with_suffix(path.suffix + f'.tmp.{os.getpid()}')
tmp.write_bytes(data); os.fsync on the file handle; os.replace(tmp, path)
```

---

## 8. Filter Hook (`filters.py`)

```python
from typing import Protocol, Literal

FilterDecision = Literal["accept", "redact", "reject"]

class FilterResult:
    decision: FilterDecision
    value: str          # possibly rewritten when decision == 'redact'
    reason: str = ""

class Filter(Protocol):
    name: str
    def inspect(self, *, kind: str, key: str, value: str) -> FilterResult: ...
    # kind ∈ {"fact","adr.context","adr.decision","preference","session.summary"}
```

**Evaluation order** (write path):
1. All configured filters run sequentially.
2. If any returns `reject` → raise `FilterRejectedError(filter_name, reason)`.
3. If any returns `redact` → `value` is replaced before the next filter sees it.
4. `accept` → pass-through.

**Default filters** shipped in `filters.py`:
- `SecretsRedactor` — regex-redacts obvious tokens (`AKIA[0-9A-Z]{16}`, `ghp_[A-Za-z0-9]{36}`, `sk-[A-Za-z0-9]{20,}`, private key blocks). Replaces match with `<REDACTED:<kind>>`.
- `SizeLimiter(max_bytes=64_000)` — rejects values exceeding limit.

Not installed by default — callers opt-in: `MemoryStore(filters=[SecretsRedactor(), SizeLimiter()])`. Documented in README.

---

## 9. CLI (`cli.py`, entrypoint `mindkeep`)

Implemented with `argparse`. Global flags:
- `--cwd PATH` (default: current dir) — selects the project.
- `--json` — emit NDJSON instead of pretty tables.

Subcommands:

| Command | Args | Output |
|---|---|---|
| `list` | `[--kind fact\|adr\|preference\|session] [--tag T] [--limit N]` | table: `id  key/number  snippet  updated_at` |
| `show` | `<kind> <id-or-key>` | full record as pretty YAML-ish text (or JSON with `--json`) |
| `clear` | `[--kind ...]` `--yes` | writes count removed |
| `export` | `<out.json>` | writes a single JSON document `{project, facts, adrs, preferences, sessions}` |
| `import` | `<in.json> [--merge\|--replace]` | writes count imported |
| `projects` | (no args) | lists all known project DBs with display_name + id + last updated |
| `info` | (no args) | prints resolved `ProjectId`, db path, sizes, schema version |

Exit codes: `0` success, `1` user error, `2` storage error, `3` filter rejection.

JSON output schemas are **frozen** alongside this contract — downstream agents may depend on them. All timestamps are ISO-8601 UTC with trailing `Z`.

---

## 10. Agent Integration Protocol — Outline

`.github/agents/memory-protocol.md` (authored by the `agent-integration-hook` todo) MUST cover these sections in this order:

1. **When to read** — at task start, agents call `MemoryStore.open()` and consult `list_facts`, relevant `list_adrs(status='accepted')`, and `list_preferences`.
2. **When to write** — on confirmed decisions (ADR), newly-learned stable facts, session-end recap (session_summaries).
3. **Key namespaces** — reserved prefixes: `build.*`, `test.*`, `style.*`, `deploy.*`, `repo.*`.
4. **ADR authoring conventions** — map the agent ADR template fields to `add_adr()` parameters.
5. **Preferences vs facts** — decision rule: cross-project → preference; project-specific → fact.
6. **Session summary contract** — max 4KB, structure template, when to emit.
7. **Safety** — never write secrets; rely on `SecretsRedactor`; avoid PII.
8. **Failure mode** — memory store is best-effort; if `MemoryStore.open()` raises, agents log a warning and proceed without memory (do NOT abort the task).

Architect reviews & signs off on memory-protocol.md before it lands.

---

## 11. Testing Strategy

### 11.1 Unit (`tests/unit/`) — fast, in-memory or tmp_path
- `test_project.py` — URL normalization table, git/no-git dirs, determinism (same input → same hash), Windows backslash paths.
- `test_schema.py` — `migrate()` idempotency, fresh DB creation, version check.
- `test_filters.py` — Secret patterns, size limiter, redact chain, reject propagation.
- `test_store_crud.py` — each public method: happy path, not-found, duplicate, tag filtering, pagination.

### 11.2 Integration (`tests/integration/`)
- `test_cli.py` — invoke `python -m mindkeep …` via `subprocess`, assert on stdout/exit codes, JSON schema stability.
- `test_cross_project.py` — open two stores in two temp cwds, verify isolation of facts but shared preferences.

### 11.3 Crash (`tests/crash/`)
- `test_crash_safety.py` — launch a subprocess that writes N facts then `os.kill(pid, SIGKILL)` on Linux / `TerminateProcess` via `psutil` or `taskkill /F` on Windows. Re-open DB; assert committed rows survive and DB is not corrupt (`PRAGMA integrity_check`).
- atexit hook test — subprocess exits normally; assert sidecar meta.json exists and is valid JSON.

### 11.4 Non-goals for v1 tests
- No property-based (hypothesis) tests — can add later.
- No benchmarks gated in CI.

Test runner: `pytest` (listed as optional dep in `pyproject.toml [project.optional-dependencies]`). Core library stays stdlib-only.

---

## 12. Naming & Style Conventions

- Modules: `snake_case.py`
- Classes: `PascalCase`
- Functions/vars: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private helpers: `_leading_underscore`
- Type hints **mandatory** on all public functions.
- `from __future__ import annotations` at top of every module.
- Docstrings: Google style, required on every public symbol.
- Line length: 100.
- No f-strings in `logging` calls — use `%` formatting so lazy eval works.

---

## 13. Key Architectural Decisions (inline ADRs)

### ADR-0001: SQLite per project, not a single global DB
- **Decision**: Each project gets its own `.db` file.
- **Why**: Enables easy `clear`, `export`, deletion by simply removing a file; avoids lock contention; makes manual inspection trivial; natural blast radius.
- **Trade-off**: Preferences cross-cut projects → separate global `preferences.db`.

### ADR-0002: `synchronous=NORMAL` over `FULL`
- **Decision**: Accept that a host-level OS crash could lose the last committed transaction's WAL tail.
- **Why**: MindKeep is advisory. 2–10× write throughput on Windows. Still durable for application-level crashes (our actual concern).

### ADR-0003: stdlib-only core
- **Decision**: No runtime deps. `pytest` dev-only.
- **Why**: Zero-friction install in agent environments, no supply-chain risk, no version conflicts with host projects.

### ADR-0004: Filters as opt-in Protocol, not forced middleware
- **Decision**: `MemoryStore` accepts `filters: Sequence[Filter]`; default is empty.
- **Why**: Keep the library honest — the caller (agent or CLI) decides policy. Ships sensible defaults (`SecretsRedactor`) that callers plug in explicitly.

### ADR-0005: ID = first 12 hex chars of sha256
- **Decision**: Not full 64-char hash, not UUID.
- **Why**: 48 bits of entropy ≈ 2.8e14 → collision probability negligible for per-user project counts (<10^4). Short enough to type / paste into filenames comfortably. Deterministic (important for re-opening).

---

## 14. Delivery Checklist (for downstream agents)

- [ ] `src/mindkeep/` package matches §2 layout exactly.
- [ ] `schema.py` DDL byte-for-byte matches §4 (CI diff check).
- [ ] `MemoryStore` public methods match §6 signatures (including keyword-only markers).
- [ ] `resolve_project_id` determinism test passes on Windows + Linux.
- [ ] Crash test passes on Windows (primary target) and Linux.
- [ ] CLI `--json` output schemas documented in README (examples).
- [ ] `pip install -e .` followed by `mindkeep info` works from any directory.
- [ ] No runtime imports outside stdlib (`ast` check in CI).

---

*End of contract. Changes require new ADR appended to §13 and architect sign-off.*
