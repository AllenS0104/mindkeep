# mindkeep v0.4.0 Design Document

> Status: **Approved with conditions** · Source: PMO meeting 2026-05-07
> Closes #32 (design ticket). Implementation tickets enumerated in §16.
> Release gate: integration test suite (issue P2 in §16) round-trips every
> tool against the official `mcp` Python SDK client before tagging v0.4.0.

This document is the source-of-truth design record for mindkeep v0.4.0. It
captures the consensus of a four-perspective review (Architect, Product,
Cost-Economist, Skeptic) on what it means to expose mindkeep as a Model
Context Protocol (MCP) server — what ships, what is deferred, and the
constraints each contributor placed on the design.

---

## 1. Goals & non-goals

v0.4.0 is the release where mindkeep stops being "a CLI you teach the agent
to shell out to" and becomes "a first-class tool surface any MCP-aware
client can discover and call without copy-paste". The three headline goals
— in priority order — are:

1. **Zero-config in MCP-aware clients.** A user installs `mindkeep[mcp]`,
   adds one line to `claude_desktop_config.json` (or `cursor.json`, or
   `continue.json`), and the agent immediately has structured access to
   every operation that v0.3 ships through the CLI. No `AGENTS.md`. No
   shell parsing. No string templating in the prompt.
2. **Structured tool calls instead of stdout scraping.** Today an agent
   calls `mindkeep recall "X"` and parses plain-text output. v0.4 returns
   JSON-shaped results through the MCP tool-call protocol — the agent
   gets typed inputs and outputs and never has to know the CLI exists.
3. **Hold the zero-runtime-dependency posture.** The MCP SDK is a real
   dependency. It does **not** belong in the core install. v0.4 keeps
   `pip install mindkeep` zero-deps; the SDK lives behind the
   `mindkeep[mcp]` extra.

### Non-goals (deferred to v0.5 or later)

- **SSE / Streamable-HTTP transports.** stdio covers Claude Desktop,
  Cursor, Continue, and every local agent that matters in 2026. Network
  transports introduce auth, CORS, and multi-tenant questions we are not
  ready to answer.
- **MCP "prompts" surface.** Prompts are reusable templates the user
  composes from in the client UI. mindkeep has no first-class concept of a
  prompt today; inventing one alongside the tool surface would dilute
  both. Re-evaluate in v0.5 once we have telemetry on which tool calls
  agents actually issue.
- **Multi-tenant / multi-project switching mid-session.** v0.4 binds one
  server process to one project (`cwd` at start). Switching projects
  means restarting the server. A "project switcher" tool is on the v0.5
  shortlist, gated on real demand.
- **Telemetry, auth, rate-limiting, audit log.** stdio MCP servers run as
  the user; the trust boundary is the user's shell. Adding audit infra
  before there is a network transport is YAGNI.
- **Incremental / streaming tool responses.** Useful for large recalls
  but requires the streamable-HTTP transport; revisit alongside SSE.

---

## 2. Background: MCP in 90 seconds

The Model Context Protocol is a JSON-RPC 2.0–based protocol that lets a
host application (Claude Desktop, Cursor, Continue) talk to local
"servers" that expose three kinds of capabilities:

- **Tools** — model-invoked functions with JSON-Schema input. The host
  shows the tool list to the model; the model picks one and emits a
  structured call. The server returns a structured result. This is the
  primary surface for actions and queries.
- **Resources** — addressable read-only content the host can attach to
  the conversation context. Identified by URI (e.g. `file://...`,
  `mindkeep://facts/42`). Resources are *user-selected* in the host UI;
  the model does not pick them autonomously.
- **Prompts** — parameterized prompt templates the user can invoke from
  a slash menu. Out of scope for v0.4 (see §1 non-goals).

### Why an mindkeep MCP server matters more than `mindkeep integrate`

The v0.3 `integrate` story works, but it has three problems:

1. **Shell parsing.** The agent sees `mindkeep recall ...` output as
   plain text, has to recognize column boundaries, and re-encodes it
   into its own context. Every model rev is a re-validation.
2. **Per-target instructions drift.** Each target (`AGENTS.md`,
   `SKILL.md`, `.cursor/rules/*.mdc`) has its own template and its own
   style of "describe when to call". They drift apart over time.
3. **No discoverability.** The agent only knows the commands the
   integration file mentions. New flags or subcommands are invisible
   until we update every template.

MCP fixes all three: tool discovery is automatic (the host calls
`tools/list`), inputs and outputs are structured, and there is exactly
one schema to maintain — the server's. v0.4 supersedes `_integrations.py`
for MCP-aware clients; the copy-paste path remains for clients that have
not yet adopted MCP (see §12 — `mindkeep integrate` will gain MCP-aware
targets, not be removed).

---

## 3. Tool surface

The proposed tool surface is a deliberately thin shim over
`mindkeep.MemoryStore`. Each tool has a stable JSON-Schema input and a
JSON output. Output payloads are kept compact by default; callers cap
them with `top` / `limit` (see §13 token-budget impact).

v0.4 ships **ten tools** (five read, five write — counting `pin` and
`unpin` as separate). They are kept as separate tools rather than collapsed
into one `set_pin {pinned: bool}` so that the model-facing description for
each verb stays unambiguous and so a `--allow-writes`-disabled server can
hide both with a single registration filter.

| Tool                          | Mode  | API call                               | Notes                                                   |
| ----------------------------- | ----- | -------------------------------------- | ------------------------------------------------------- |
| `mindkeep_recall`             | read  | `MemoryStore.recall`                   | The headline tool — agents will call this most.         |
| `mindkeep_list_facts`         | read  | `MemoryStore.list_facts`               | For pinned-only / tag-filtered browsing.                |
| `mindkeep_list_adrs`          | read  | `MemoryStore.list_adrs`                | Same shape as `list_facts`. Server enforces `limit` (API has none today). |
| `mindkeep_stats`              | read  | `mindkeep.core.collect_stats(store)`   | Pure data helper (see §3.3). Counts, tokens, hit rates. |
| `mindkeep_doctor`             | read  | `mindkeep.core.collect_doctor(...)`    | Pure data helper (see §3.3). `{checks:[...], ok:bool}`. |
| `mindkeep_add_fact`           | write | `MemoryStore.add_fact`                 | WriteGuard applies. `force` is **not** exposed (§9).    |
| `mindkeep_add_adr`            | write | `MemoryStore.add_adr`                  | WriteGuard applies. `force` is **not** exposed (§9).    |
| `mindkeep_pin`                | write | `pin_fact` / `pin_adr`                 | `kind` arg disambiguates fact vs ADR.                   |
| `mindkeep_unpin`              | write | `unpin_fact` / `unpin_adr`             | `kind` arg disambiguates fact vs ADR.                   |

> **Removed from v0.4 MCP surface:** `mindkeep_set_preference`. Preferences
> persist to a *global* (cross-project) DB; an autonomous agent writing
> there can poison every future session in every other project. We declined
> to ship a project-only variant in v0.4 (it would require a new API
> column or a new store) and instead keep `set_preference` CLI-only. The
> agent can still *read* preferences indirectly via the application layer
> if needed; v0.5 may revisit a project-scoped preference write tool.

### 3.1 Schemas (JSON-Schema, abbreviated)

```jsonc
// mindkeep_recall
{
  "type": "object",
  "properties": {
    "query": { "type": "string", "minLength": 1 },
    "top":   { "type": "integer", "minimum": 1, "maximum": 50, "default": 10 },
    "kind":  { "type": "string", "enum": ["all", "facts", "adrs"], "default": "all" }
  },
  "required": ["query"]
}
// returns: { "hits": [ { kind, id, score, snippet, tags, value, extra } ] }

// mindkeep_list_facts
{
  "type": "object",
  "properties": {
    "tag":         { "type": ["string", "null"] },
    "pinned_only": { "type": "boolean", "default": false },
    "limit":       { "type": "integer", "minimum": 1, "maximum": 200, "default": 20 }
  }
}
// returns: { "facts": [ { id, value, tags, pin, created_at, updated_at, token_estimate } ] }

// mindkeep_add_fact
{
  "type": "object",
  "properties": {
    "value": { "type": "string", "minLength": 1 },
    "tags":  { "type": "array", "items": { "type": "string" }, "default": [] },
    "pin":   { "type": "boolean", "default": false }
  },
  "required": ["value"]
}
// returns: { "id": int, "token_estimate": int, "pinned": bool }

// mindkeep_pin / mindkeep_unpin
{
  "type": "object",
  "properties": {
    "kind": { "type": "string", "enum": ["fact", "adr"] },
    "id":   { "type": "integer", "minimum": 1 }
  },
  "required": ["kind", "id"]
}
```

The remaining tools follow the same one-to-one pattern with their API
counterparts; the full schemas live alongside the implementation in
`src/mindkeep/mcp/tools.py`.

### 3.3 Adapter responsibilities (server ↔ MemoryStore mismatches)

The MCP tool schemas and the v0.3 `MemoryStore` API surface are not
identical. The server layer is responsible for bridging the gap; tool
handlers MUST NOT silently rely on schema validation alone. Specifically:

- **Field renames.** Tool input `value` (the model-facing word for fact /
  ADR text) maps to `MemoryStore.add_fact(content=...)` /
  `MemoryStore.add_adr(content=...)`. Documenting this in the schema is
  not enough — the handler does the rename.
- **Return-shape backfill.** `add_fact` / `add_adr` return only an `int`
  rowid. The handler queries the freshly-written row to produce the
  documented tool result `{id, token_estimate, pinned}`. This is a single
  read-after-write inside the same transaction; no separate caller flow.
- **`list_adrs` `limit` clamp.** The current `MemoryStore.list_adrs` API
  has no `limit` parameter (verified in `src/mindkeep/memory_api.py`).
  The server slices `rows[:limit]` after the API call; the schema's
  `maximum: 200` is the hard cap.
- **All numeric caps enforced in the handler.** JSON-Schema `maximum` is
  necessary but not sufficient — a non-conforming client can submit
  out-of-range values. The handler clamps `top` (recall), `limit`
  (list_*), and any future numeric input *after* schema validation.
- **`stats` / `doctor` data helpers.** `_cmd_stats` and `_cmd_doctor` are
  CLI commands that `print()` to stdout. Reusing them from MCP tool
  handlers would corrupt the JSON-RPC channel (see §3.4). The server
  calls **pure data helpers** `mindkeep.core.collect_stats(store) -> dict`
  and `mindkeep.core.collect_doctor(data_dir, project_id) -> dict`. The
  CLI continues to print; the MCP handlers return the dict directly. The
  helper extraction is part of #34's acceptance.

### 3.4 stdio invariant: MCP tool handlers MUST NOT write to stdout

In stdio MCP transport, **stdout is the JSON-RPC frame channel**. Any
stray `print()`, banner, log line, or `traceback.print_exc()` written
to stdout will corrupt the protocol stream and brick the session. This
is a hard correctness invariant for v0.4:

1. Tool handlers, resource handlers, and any code they call (including
   `MemoryStore`, filters, and `collect_stats` / `collect_doctor`) MUST
   NOT touch `sys.stdout`. Diagnostics go to `sys.stderr`.
2. The CLI commands (`_cmd_stats`, `_cmd_doctor`) continue to print to
   stdout — they are the *consumers* of the data helpers, not the
   producers. The data helpers are stdout-clean.
3. The startup-time SDK-missing message (§7) and the project-binding
   diagnostic (§8) both write to **stderr**.
4. The integration test plan (§14) includes a "no-stdout-contamination"
   regression: a subprocess test that launches `mindkeep-mcp` over
   stdio, drives every tool through the official `mcp` SDK client, and
   asserts that every byte written to the server's stdout parses as an
   MCP frame.

### 3.5 Tool descriptions (model-facing, intent-driven)

MCP tool `description` fields are read by the model when deciding which
tool to call. v0.3's integration playbook (DESIGN-v0.3.0 §6) settled on
**describe intent, not phrases** — listing trigger words ("when the user
says 'remember that'…") is brittle; describing intent ("when the user
references a prior decision the agent doesn't have in context") survives
model revs. v0.4 carries this forward. Each tool ships with a 1–3
sentence description that names both *when to use* and *when NOT to
use*. The implementation lives in `src/mindkeep/mcp/tools.py` next to
the schema; the strings below are normative.

| Tool | Description (model-facing) |
| ---- | -------------------------- |
| `mindkeep_recall` | Search this project's mindkeep store for facts and ADRs related to a topic. Use this **before** broader browsing tools when the user references prior context, a past decision, or "what did we decide about X". Do **not** call to answer questions about mindkeep itself (use `stats`/`doctor`). |
| `mindkeep_list_facts` | Browse facts by tag or pinned state when you already know roughly what bucket of memory to read. Use **after** `recall` came back empty, or when the user asks "what's pinned" / "what's tagged X". Avoid for free-text questions — `recall` is cheaper and more relevant. |
| `mindkeep_list_adrs` | Browse architectural decision records by status or pinned state. Same shape as `list_facts`; same when-to-use rules. |
| `mindkeep_stats` | Return counts, token totals, and hit-rate metrics about this project's mindkeep store. Use only when the user asks *about the store itself* ("how many facts do I have?"). Do NOT use to answer project questions. |
| `mindkeep_doctor` | Run a structured health check on this project's mindkeep store. Use only when the user asks "is mindkeep working?" or after a suspected schema/storage error. Do NOT use as a general status query. |
| `mindkeep_add_fact` | Persist a durable, user-relevant fact to this project's memory. Use **only** for content the user would want surfaced in a future session: design preferences, naming conventions, decisions, gotchas. Do NOT use for transient state, secrets, credentials, or anything the user has not explicitly endorsed. |
| `mindkeep_add_adr` | Record an architectural decision (context + decision + consequences). Use only when the user has explicitly framed something as a decision. Same secrets/transient-state rule as `add_fact`. |
| `mindkeep_pin` | Mark a fact or ADR as pinned so it surfaces first in future listings. Use when the user says something is important enough to keep visible; do NOT auto-pin everything you write. |
| `mindkeep_unpin` | Remove the pinned flag from a fact or ADR. Use when the user explicitly says an item is no longer high-priority. |

### 3.6 When agents should call which tool (intent guide)

This subsection is a v0.3 carry-forward (cf. DESIGN-v0.3.0 §6) updated
for the MCP surface. It describes the *flow* the agent should follow,
independent of the host UI:

1. **First read, then write.** On any user reference to prior context,
   call `mindkeep_recall` before doing anything else. Only fall through
   to `list_facts` / `list_adrs` if recall returns nothing relevant.
2. **Write only durable, non-secret, user-relevant memory.** Calls to
   `add_fact` / `add_adr` are persistent and shared across this
   project's future sessions. The agent should require an explicit user
   signal ("remember this", "log this decision", a clear durable
   preference) before writing. Transient turn-state, debugging notes,
   and anything secret-shaped (tokens, passwords, PII) MUST NOT be
   written.
3. **Pin sparingly.** Pinning makes an item compete for the top of every
   future listing. Use only when the user has explicitly signaled
   importance.
4. **Never use `stats` / `doctor` to answer project questions.** They
   describe the store; they do not contain user content.
5. **Confirm before destructive intent.** v0.4 does not ship destructive
   tools (no `clear`, no `delete`), but if the user asks for one,
   surface that v0.3 CLI is the path and surface the command — do not
   substitute "I'll just unpin / overwrite" autonomously.

### 3.2 Inclusion / exclusion rationale

- **Included:** every read operation an agent currently shells out for,
  every write operation that has a stable v0.3 API surface, plus pin /
  unpin (which agents will want once they realize they can curate the
  store themselves).
- **`mindkeep_clear`** — *excluded.* Destructive, irreversible, and an
  agent has no business deleting all facts in a project without an
  explicit human-driven flow. Stays CLI-only.
- **`mindkeep_export` / `_import`** — *excluded.* File-system side
  effects in arbitrary working directories from a tool call is bad
  hygiene. The user runs these from their shell.
- **`mindkeep_session`** — *excluded.* The session-budget state file is
  a CLI rendering concern (see §11); MCP tool calls don't have sessions
  in the v0.3 sense.
- **`mindkeep_recent_sessions`** — *excluded for v0.4.* Useful but
  low-volume; defer until we see an agent ask for it.

---

## 4. Resources

mindkeep exposes three URI schemes as MCP resources:

| URI pattern               | Maps to                                                | Why a resource, not a tool                          |
| ------------------------- | ------------------------------------------------------ | --------------------------------------------------- |
| `mindkeep://facts/{id}`   | one fact's full record (JSON)                          | User picks "attach this fact" from the host UI.     |
| `mindkeep://adrs/{id}`    | one ADR's full record (JSON)                           | Same — user-driven attachment.                      |
| `mindkeep://project`      | `{ project_id, db_path, schema_version, counts }`      | Stable, cheap; useful as session-start context.     |

Resources are listed via `resources/list` and read via `resources/read`.
The list is paginated for stores with thousands of items.

**Why these as resources?** The MCP design split is: tools are *agent*
actions, resources are *user* attachments. A user pulling up a specific
fact in their host UI to drop into the conversation is a resource flow.
The agent searching for it is a tool flow (`mindkeep_recall`). Both are
valid, and they're not redundant — they serve different actors.

**Why not resources for every list?** `resources/list` returning every
fact is fine for 50 rows, painful for 5,000. The agent-driven path
(`mindkeep_list_facts` with `limit`) is the right shape for bulk
browsing. Resources are for "I, the user, want to attach *this one
thing*."

### 4.1 URI stability

`mindkeep://facts/{id}` and `mindkeep://adrs/{id}` are valid **only for
the currently bound project and only for the current server lifetime**.
Concretely:

- The `{id}` is the SQLite rowid in this project's DB. It is not
  globally unique; the same id refers to a different fact in a
  different project.
- Restarting the server with a different `--project-dir` /
  `MINDKEEP_PROJECT_DIR` invalidates every previously-issued URI.
- A `resources/read` for a nonexistent id, or for an id that exists in
  a different project than the one currently bound, returns an MCP
  resource-not-found error. The server does not silently substitute.

A `mindkeep://{project_id}/facts/{id}` form was considered for hosts
that persist URIs across sessions (so a saved chat can re-open a
specific fact). It is **deferred to v0.5** — v0.4 hosts (Claude Desktop,
Cursor, Continue) do not persist URIs across server restarts, so the
extra namespace isn't paying for itself yet.

---

## 5. Transport

**stdio is the only supported transport in v0.4.** Decision rationale:

- Every host that matters in 2026 (Claude Desktop, Cursor, Continue,
  VS Code's MCP support) launches local servers over stdio.
- SSE / Streamable-HTTP transports require a listening port, which
  raises auth, CORS, and discovery questions. None of those have
  obviously correct answers for a personal-machine notes store.
- The `mcp` Python SDK ships a stdio server in two lines; there is no
  technical reason to gate v0.4 on additional transports.

**SSE: out for v0.4** (deferred to v0.5+). When we add it, it gets its
own design doc covering bind address, auth token, and the multi-tenant
project resolution story (§8) that stdio elides.

---

## 6. Entry point

We ship **both** entry points and document each one's use:

1. **`mindkeep mcp serve`** — adds an `mcp` subcommand to the existing
   `mindkeep` CLI with one verb (`serve`). This is the right shape for
   shell users who already have `mindkeep` on PATH and want to spike
   the server interactively.
2. **`mindkeep-mcp`** — a separate `console_script` defined in
   `pyproject.toml` that invokes the same `serve` entry. This is the
   right shape for `claude_desktop_config.json` and friends, which
   take a single command + arg list (not a subcommand) and dislike
   shell quoting.

```toml
# pyproject.toml
[project.scripts]
mindkeep     = "mindkeep.cli:main"
mindkeep-mcp = "mindkeep.mcp.server:main"   # NEW
```

**Tradeoffs.** Two entry points is a small docs cost (we have to say
"either of these works"). One entry point is a config-file footgun
(`["mindkeep", "mcp", "serve"]` works, but the failure mode if the
user types it wrong is silent). Shipping both is cheap and lets each
audience use the shape they expect.

---

## 7. Optional dependency boundary

The `mcp` SDK is **not** added to core dependencies. It lives behind an
extra:

```toml
# pyproject.toml
[project.optional-dependencies]
dev = [ "pytest>=7.4", "pytest-cov>=4.1" ]
mcp = [ "mcp>=1.0,<2.0" ]
```

> **SDK version pin.** Tested against `mcp` Python SDK **1.26.0** (current
> latest at design time: 1.27.0). The `<2.0` upper bound protects v0.4
> from a future breaking-major; CI pins the tested version explicitly via
> the `dev` extra. Bumping to ≥2.0 is a v0.5 conversation.

### 7.1 Lazy-import rules (hard requirements)

The optional-dep boundary is only meaningful if `pip install mindkeep`
without the extra still works for everything that isn't the MCP server.
The following are **hard correctness requirements**, not aspirations:

1. `python -c "import mindkeep"` MUST succeed without `mcp` installed.
   The package's `__init__.py` may not import any name from `mcp`, even
   transitively.
2. `mindkeep --help` MUST succeed without `mcp` installed. The argparse
   parser construction in `mindkeep.cli` may not import `mcp` — not even
   inside subcommand factories that argparse evaluates eagerly.
3. The SDK is imported lazily *inside* `mindkeep.mcp.server:main` (or a
   helper called by `main()`), not at module load time. Importing
   `mindkeep.mcp.server` for entry-point resolution does not pull in the
   SDK.
4. Both entry points (`mindkeep mcp serve` and `mindkeep-mcp`), when
   the extra is missing, print a single friendly message naming the
   install command and exit code 2:

   ```python
   try:
       import mcp.server  # local import inside main()
   except ImportError:
       sys.stderr.write(
           "mindkeep MCP server requires the 'mcp' SDK.\n"
           "Install with: pip install 'mindkeep[mcp]'\n"
       )
       sys.exit(2)
   ```

   Exit code 2 (vs 1) lets clients that auto-restart on failure
   distinguish "missing dep" from "runtime error" if they care.
5. The test plan (§14) includes regressions for items 1, 2, and 4.

---

## 8. Project context resolution

How does a stdio MCP server know which project store to read from? This
question is **load-bearing** for v0.4. Claude Desktop, Cursor, and
Continue all launch local MCP servers from their *own* working directory
(typically the user's home directory or the host app's install
directory) — **not** the project repo. v0.3's "cwd at process start"
convention silently binds the server to `~/.mindkeep/<hash-of-home>/`
and writes go to a bogus project. This must not happen.

### 8.1 Resolution precedence

| Source                          | Precedence | Use case                                   |
| ------------------------------- | ---------- | ------------------------------------------ |
| `--project-dir PATH` flag       | highest    | Power-user one-off; explicit in process args. |
| `MINDKEEP_PROJECT_DIR` env var  | next       | Pin the server to a fixed project from `claude_desktop_config.json` (the canonical mechanism for global hosts). |
| `cwd` discovery (v0.3 default)  | fallback   | CLI-launched server; project-local agents whose cwd is the repo. |
| Tool-call argument              | rejected   | See §8.4.                                  |

The flag and env var are equivalent in semantics; the flag wins when
both are set. Both accept an absolute or relative path; relative paths
resolve against the server process's startup cwd.

### 8.2 Implementation note

`MemoryStore.open(cwd=...)` in `src/mindkeep/memory_api.py` today only
honors an explicit `cwd` argument; neither `--project-dir` nor
`MINDKEEP_PROJECT_DIR` is read by core. v0.4 implements the override
**at the MCP server entry point**, not inside `MemoryStore`:

1. `mindkeep.mcp.server:main` parses `--project-dir`, falls back to
   `MINDKEEP_PROJECT_DIR`, falls back to `Path.cwd()`.
2. The resolved path is passed as `cwd=` to `MemoryStore.open(cwd=...)`,
   which delegates to `resolve_project_id(cwd=...)` exactly as today.
3. Core (`MemoryStore.open`, `resolve_project_id`) gets **no new code
   path** in v0.4. The override is a server-layer concern. (A
   `MemoryStore.open_for_mcp(...)` factory was considered and rejected:
   it would push MCP knowledge into core for no abstraction win.)

### 8.3 The `mindkeep://project` resource exposes the resolution

The `mindkeep://project` resource (§4) returns:

```jsonc
{
  "resolved_project_dir": "/Users/me/code/myrepo",
  "project_id": "abc123def456",
  "id_source": "env",          // one of: "flag", "env", "cwd-discovery", "auto-init"
  "db_path": "/Users/me/Library/Application Support/mindkeep/abc123def456.db",
  "schema_version": 3,
  "counts": { "facts": 42, "adrs": 7 }
}
```

`id_source` is the resolution-precedence label, used by hosts and humans
to diagnose "wait, why is the agent reading the wrong project". It is
**also** logged once to stderr at server startup.

### 8.4 Startup-time diagnosability guard

If, after resolution, the project directory is one of the following
*and* would auto-initialize a fresh `.mindkeep/` there, the server emits
a stderr warning at startup and sets `id_source` accordingly so clients
can surface it:

- Exactly the user's `$HOME` (or `%USERPROFILE%` on Windows).
- An OS temp directory (`/tmp`, `/var/tmp`, `$TMPDIR`, `%TEMP%`).
- A directory containing no `.git`, no existing `.mindkeep/`, and no
  ancestor that does — i.e. "this is almost certainly the host app's
  default cwd, not a real project".

The warning is informational — the server still starts — but the
combination of stderr warning + `id_source` field makes the misbinding
diagnosable instead of silent.

### 8.5 Why not per-call `project_id` arguments?

Two reasons:

1. The MCP host does not generally know which projects exist on the
   user's machine. Letting the model pick a project is a cross-project
   data-leak vector ("recall facts from my employer's repo while
   working in my hobby repo").
2. v0.3 has no notion of a project registry the agent could consult;
   project IDs are derived hashes of paths. Asking the agent to guess
   them is hostile UX.

If a power user wants "one server, many projects", they restart the
server with a different `--project-dir`. Multi-project switching
mid-session is an explicit non-goal (§1).

### 8.6 `mindkeep integrate` bakes the project dir

Generated client configs from `mindkeep integrate claude-desktop |
cursor-mcp | continue-mcp` MUST include the project binding — either
`MINDKEEP_PROJECT_DIR` (preferred, lives in the `env` block) or
`--project-dir` in `args`, populated with the cwd at integrate-time.
This is normative; see §12 for the snippets.

---

## 9. Write-permission story

By default the server is **read-only**. Writes are off unless the user
explicitly enables them. Rationale: an MCP-enabled agent runs
autonomously between user prompts, and `add_fact` / `add_adr` /
`pin` / `unpin` all persist across future sessions — a bad write
poisons the project's memory permanently. Default-on writes is wrong
for the threat model.

### 9.1 Switches

- **`--allow-writes`** (CLI flag on `mindkeep mcp serve` and
  `mindkeep-mcp`). Without it, write tools (`add_fact`, `add_adr`,
  `pin`, `unpin`) are not registered with the SDK at all — they do not
  appear in `tools/list`, so the model cannot try and fail; it simply
  does not know they exist. With the flag, write tools register
  normally.
- **`--read-only`** (CLI flag, kept as an alias for explicitness). It is
  a no-op when `--allow-writes` is absent. Specifying both is an error
  (`exit 2`, stderr message). Documented in `--help` as
  "default mode; provided for clarity in scripts".
- **Generated `integrate` configs do NOT include `--allow-writes`.**
  Users opt into writes by editing their client config. Documented in
  §12.

### 9.2 `force` is not exposed to the model

v0.3's `force=True` argument bypasses `WriteGuard` size caps. Exposing
it as a tool input lets the model route around the guard with a
single-flag flip — the agent has every incentive to retry-with-force
on the first failure, which defeats the cap entirely.

In v0.4 MCP:

- `mindkeep_add_fact` / `mindkeep_add_adr` schemas **do not include
  `force`**. The handler always calls the underlying API with
  `force=False`.
- A `WriteGuardError` from core surfaces as a tool-result error
  (`isError: true`) with a structured payload (§10). The model can
  retry with shorter content, or surface the cap to the user.
- Users who want to bypass the cap use the CLI: `mindkeep add-fact
  --force ...`. This is a deliberate friction; bypassing a sanity cap
  should require human action.

### 9.3 Server-side dedup at the boundary

Even with WriteGuard, an agent in a retry loop can fill the store with
near-duplicate facts. The MCP write handlers add a boundary check:

- Before inserting, the `add_fact` handler queries for an existing fact
  with **identical `value` AND identical `tags`** (canonical-ordered)
  in the current project.
- If found, the handler returns a tool-result error
  (`isError: true`, `error_kind: "duplicate"`) with the existing fact
  id in the structured payload (§10). No row is written.
- This is a server-layer concern only; core `MemoryStore.add_fact`
  remains an append-only log. Users who actually want duplicates can
  use the CLI.
- ADRs are **not** dedup'd at the boundary (their content is much
  larger and exact-match collisions are vanishingly unlikely; a
  `WriteGuard` cap covers the runaway case).

### 9.4 Write safety story (carry-forward from v0.3)

DESIGN-v0.3.0 §6 codified "do not modify the store on the user's behalf
without explicit confirmation". v0.4 splits this into two layers:

- **Structural** (this section): default-read-only, no `force`,
  boundary dedup. The model literally cannot do most of the harm.
- **Behavioral** (tool descriptions, §3.5–§3.6): the model-facing
  description of every write tool reiterates the durable-only,
  non-secret-only, user-endorsed-only criteria. This is the v0.3
  posture, ported into MCP descriptions.

---

## 10. Error mapping

MCP follows JSON-RPC 2.0 error semantics. mindkeep splits errors into
two channels by **whether the model can recover**:

- **Recoverable tool-call errors** (the model can retry, edit, or
  surface to the user) — returned as a normal tool result with
  `isError: true` and a structured `content` payload. The host shows
  this to the model.
- **Protocol errors** (malformed JSON-RPC, unknown method, server bug)
  — returned as a JSON-RPC `error` response with a code from the
  reserved `-32000…-32099` server-error band.

### 10.1 Recoverable tool-call error payload

Every recoverable error returns a `content` block of type `json` with
this exact shape:

```jsonc
{
  "error_kind": "<one of the kinds below>",
  "message":    "human-readable summary",
  "fields":     { ... kind-specific structured data ... }
}
```

| `error_kind`         | Trigger                                           | `fields` shape                                          |
| -------------------- | ------------------------------------------------- | ------------------------------------------------------- |
| `write_guard`        | `WriteGuardError` from core                       | `{ kind, cap, pre_tokens, post_tokens, hint }`          |
| `duplicate`          | Boundary dedup rejected `add_fact` (§9.3)         | `{ existing_id, value_preview, tags }`                  |
| `not_found`          | Unknown id passed to `pin`/`unpin` or resource    | `{ kind, id }`                                          |
| `invalid_argument`   | Schema-valid input that fails domain validation   | `{ field, value, reason }`                              |
| `read_only`          | Write tool called when server has no `--allow-writes` | `{ tool }` (rare — write tools shouldn't even register) |
| `storage`            | `StorageError` from core                          | `{ reason, schema_version, recoverable: bool }`         |

### 10.2 Protocol-level errors

| Python exception (or condition)              | MCP code  | When                                         |
| -------------------------------------------- | --------- | -------------------------------------------- |
| Malformed call / unknown method              | -32600/-32601 | Client bug; SDK normally handles before us. |
| Schema validation failure pre-handler        | -32602    | Input doesn't match declared JSON-Schema.    |
| `RuntimeError` ("MemoryStore is closed") and any uncaught exception | -32603 | Server bug; payload is `{ exc_type, traceback_id }`, full traceback logged to **stderr**. |
| `StorageError` with `recoverable=false`      | -32002    | Schema version mismatch / corrupt DB.        |

We **prefer** `isError: true` tool results to JSON-RPC errors whenever
the model can usefully react. "Your fact is too long" is a tool result;
"the database file is corrupt" is a JSON-RPC error.

### 10.3 WriteGuardError mapping (concrete)

Core raises `WriteGuardError(kind, cap, pre_tokens, post_tokens)`. The
handler converts it as:

```jsonc
// MCP tool result
{
  "isError": true,
  "content": [{
    "type": "json",
    "json": {
      "error_kind": "write_guard",
      "message":    "fact exceeds 100-token cap (post-redaction: 137)",
      "fields": {
        "kind":         "fact",
        "cap":          100,
        "pre_tokens":   141,
        "post_tokens":  137,
        "hint":         "Shorten the value or split into multiple facts. The CLI accepts --force; this MCP tool does not."
      }
    }
  }]
}
```

---

## 11. Per-call session budget

**MCP tool calls do NOT count against `MINDKEEP_SESSION_BUDGET`.**

Rationale:

- The session budget (v0.3 §5) is a CLI rendering concern: it caps
  what `mindkeep recall` and `mindkeep show` print to stdout so the
  agent's stdout-buffer-as-context window doesn't get blown out. The
  budget tracks output bytes the *user's terminal* received.
- An MCP tool call is an individual API request from the host. The
  host has its own context-window budgeting; it is not mindkeep's job
  to second-guess it.
- Sharing budget state between CLI and MCP would require a session
  identifier the MCP server has no natural source for, and would
  surface confusing "you're out of budget" errors mid-conversation
  that the user did not opt into.

Per-call caps (`top`, `limit`) are still enforced — see §13.

---

## 12. Client install snippets and `mindkeep integrate <mcp-target>`

These snippets live in `docs/MCP-INSTALL.md` (new file in the
implementation PR). v0.4 also extends `mindkeep integrate` with three
new targets — `claude-desktop`, `cursor-mcp`, `continue-mcp` — that
emit machine-merged JSON instead of markdown.

### 12.1 Generated config snippets (with project binding)

Generated configs MUST include the project binding (§8.6). They MUST
NOT include `--allow-writes` (§9.1). Users who want writes opt in by
hand-editing.

**`claude_desktop_config.json`:**

```jsonc
{
  "mcpServers": {
    "mindkeep": {
      "command": "mindkeep-mcp",
      "args": [],
      "env": { "MINDKEEP_PROJECT_DIR": "/Users/me/code/myrepo" }
    }
  }
}
```

**Cursor (`~/.cursor/mcp.json` or workspace `.cursor/mcp.json`):**

```jsonc
{
  "mcpServers": {
    "mindkeep": {
      "command": "mindkeep-mcp",
      "args": [],
      "env": { "MINDKEEP_PROJECT_DIR": "/Users/me/code/myrepo" }
    }
  }
}
```

**Continue (`~/.continue/config.json`):**

```jsonc
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type":    "stdio",
          "command": "mindkeep-mcp",
          "args":    ["--project-dir", "/Users/me/code/myrepo"]
        }
      }
    ]
  }
}
```

(Continue's schema does not have a per-server `env` block in all
versions; we use `--project-dir` for portability.)

To pin to read-only on any of the above, leave the file as generated
(read-only is the default). To allow writes, edit `args` to include
`--allow-writes`.

### 12.2 `mindkeep integrate <mcp-target>`: behavior

`src/mindkeep/_integrations.py` today is string-templating; it cannot
safely mutate JSON config files that already contain unrelated MCP
servers. v0.4 adds a JSON-aware path. Per target:

| Target           | OS host config path (each OS)                                         | Format     | v0.4 mode      |
| ---------------- | --------------------------------------------------------------------- | ---------- | -------------- |
| `claude-desktop` | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Windows `%APPDATA%\Claude\claude_desktop_config.json`; Linux `~/.config/Claude/claude_desktop_config.json` | JSON       | in-place merge (with `--out`) |
| `cursor-mcp`     | `~/.cursor/mcp.json` or workspace `.cursor/mcp.json`                  | JSON       | in-place merge (with `--out`) |
| `continue-mcp`   | `~/.continue/config.json`                                             | JSONC†     | snippet-to-stdout only (deferred merge) |

† Continue's config historically permits comments in some versions. v0.4
declines to round-trip JSONC; it prints the snippet to stdout for the
user to paste.

### 12.3 Flags and merge semantics

All three subtargets share a flag set:

- **`--out PATH`** — merge into the file at `PATH`. Without `--out`, the
  command prints the snippet to stdout (the safe default).
- **`--dry-run`** — with `--out`, show the would-be merged output to
  stdout without writing.
- **`--force`** — required to overwrite an existing `mindkeep` server
  entry. Without `--force`, the command refuses with exit 2 if a key
  named `mindkeep` already exists in the relevant `mcpServers` block.

Merge rules (`claude-desktop`, `cursor-mcp` only):

1. Load the target file as JSON. If missing or empty, start with `{}`.
2. Preserve all unrelated top-level keys.
3. Inside `mcpServers` (creating if absent), preserve all unrelated
   server entries. Set the `mindkeep` entry to the snippet from §12.1
   with `MINDKEEP_PROJECT_DIR` populated from `--project-dir` or the
   current cwd at integrate-time.
4. Before writing, copy the existing file to `<file>.bak` (overwriting
   any prior backup). Write atomically (write-tmp + rename).
5. Refuse to write JSONC: if the file contains `//` or `/*` comments,
   exit 2 with a stderr message pointing the user at snippet mode.

For `continue-mcp`, the command always prints to stdout regardless of
`--out`; the in-place merge is deferred to a future release.

### 12.4 Why snippet-to-stdout is the default

Editing a user's host config is a high-blast-radius operation. The
default mode (`integrate claude-desktop` with no flags) prints the
snippet so the user can paste consciously. `--out` is a power-user
opt-in; `--force` adds another step before mindkeep overwrites their
own config.

---

## 13. Token-budget impact

MCP tool returns are rendered into the model's context window. A naive
`mindkeep_doctor` or `mindkeep_stats` could emit a few KB of text and
crowd out useful context. The mitigations:

- Every read tool that can plausibly grow has a hard `top` / `limit`
  argument with a server-side maximum (see schemas in §3.1: `recall`
  caps at 50, `list_*` caps at 200).
- `mindkeep_recall` returns `RecallHit.snippet` (already a budget-aware
  excerpt) rather than full `value` text. Callers that want full text
  call `resources/read` on `mindkeep://facts/{id}`.
- `mindkeep_doctor` returns the structured JSON shape from
  `_cmd_doctor --json` — no banners, no help text — and a `verbose`
  flag (default `false`) gates per-check explanatory strings.
- `mindkeep_stats` returns the JSON schema documented for
  `mindkeep stats --json`. We do **not** pretty-print into the tool
  result; the model can format if it wants.

These caps are **not** a substitute for §11's session budget. They are
per-call sanity limits that prevent any single tool call from being
catastrophically expensive.

---

## 14. Test plan

Three layers, plus a release-gate smoke test (§15).

1. **Unit tests** on tool registration / schema / dispatch logic, not
   requiring a live transport. Use the `mcp` SDK's in-process server
   harness (or a minimal fake) to invoke handlers directly. Targets:
   input validation, error mapping (every row of §10's tables),
   `--allow-writes`-absent filtering of the tool list.
2. **Integration tests** using the official `mcp` Python SDK *client*
   against the real `mindkeep-mcp` entry point over stdio. One test
   per tool that round-trips: list tools, call tool, assert result
   shape. One test per resource URI. Marked
   `@pytest.mark.integration`.
3. **Compat tests** that boot `mindkeep-mcp`, send a `tools/list`
   request, and snapshot the schema. Snapshots live in
   `tests/mcp/snapshots/`. Schema drift is breaking and must be a
   conscious, reviewed change.

### 14.1 Required regressions (mapped from rubber-duck findings)

These are not optional polish; each one corresponds to a class of bug
this design is structurally trying to prevent. They MUST be in the
test plan before #39 is callable done:

- **No-stdout-contamination.** Subprocess test launches `mindkeep-mcp`
  over stdio, drives every tool and every resource through the official
  SDK client, captures every byte the server writes to stdout, and
  asserts the full byte stream parses as valid MCP frames.
- **Project binding from non-project cwd.** Launch the server with
  `cwd=$HOME` and `MINDKEEP_PROJECT_DIR=/path/to/repo`; assert the
  `mindkeep://project` resource reports `id_source: "env"` and
  `resolved_project_dir` matches.
- **Project binding from `--project-dir`.** Same as above but with the
  flag instead of env; assert `id_source: "flag"`.
- **Diagnostic on home / temp / blank cwd.** Launch with
  `cwd=$HOME` and no override; assert a stderr warning is emitted and
  `id_source` is the expected `"cwd-discovery"` (or `"auto-init"` if a
  `.mindkeep/` had to be created).
- **Core install without `mcp` extra.** In a clean venv with only
  `mindkeep` installed (no extra), `python -c "import mindkeep"` and
  `mindkeep --help` both exit 0 without importing `mcp`.
- **Missing-extra friendly error.** Same clean venv: `mindkeep mcp
  serve` and `mindkeep-mcp` both exit 2 and print the
  `pip install 'mindkeep[mcp]'` hint to stderr.
- **Read-only mode hides write tools.** Launch without
  `--allow-writes`; assert `tools/list` contains exactly the five
  read tools and zero write tools.
- **`top` / `limit` clamps including `list_adrs`.** Send a request
  with `limit: 5000` to each list tool and to `recall`; assert the
  response is clamped to the documented maximum.
- **Resource read of nonexistent id.** `resources/read` on
  `mindkeep://facts/999999`: assert MCP resource-not-found, not a
  silent empty payload.
- **Boundary dedup on `add_fact`.** Submit the same `value` + `tags`
  twice; assert the second call returns
  `isError: true, error_kind: "duplicate"` with the existing id.
- **`force` is not exposed.** `tools/list` for `mindkeep_add_fact`
  does not include `force` in `inputSchema.properties`.
- **WriteGuardError → tool-result error.** Submit an over-cap fact;
  assert tool result is `isError: true, error_kind: "write_guard"`
  with the documented `fields` shape.

Coverage gate: 90% line coverage on `src/mindkeep/mcp/`, matching the
existing project-wide bar.

---

## 15. Scope cuts and release gate

Each cut has a one-line "why deferred":

- **SSE / Streamable-HTTP transport** — every shipping host is stdio in 2026; network transport opens auth/CORS/discovery questions that need their own design doc.
- **OAuth / bearer auth on the server** — implied by the cut above; stdio runs as the user, no auth needed.
- **Multi-tenant / per-call project_id** — cross-project data-leak risk and there is no project registry the agent can consult (§8).
- **MCP prompts** — mindkeep has no first-class prompt concept; inventing one alongside the tool surface dilutes both.
- **Incremental / streaming tool responses** — needs streamable-HTTP transport; revisit alongside SSE.
- **Telemetry / audit log** — premature without a network transport; the trust boundary is the user's shell.
- **`mindkeep_clear` / `_export` / `_import` / `_session` / `_set_preference` tools** — destructive, filesystem-side-effecting, or cross-project; agent has no business doing these autonomously (§3.2, §3 table note).
- **Per-tool rate limiting** — stdio is single-process and single-user; if the agent loops, the user sees CPU spike and kills it. YAGNI.
- **Schema versioning of tool inputs** — the v0.4 schemas are v1; we'll add a versioning policy when we have a v2 to compare against.
- **`continue-mcp` in-place merge** — the file historically permits JSONC; v0.4 ships snippet-to-stdout only (§12.2).
- **`mindkeep://{project_id}/...` resource form** — current hosts don't persist URIs across server restarts; revisit if telemetry shows demand (§4.1).

### 15.1 Release gate: non-maintainer smoke test

Before tagging `v0.4.0`, **at least one non-author** must exercise the
server end-to-end against a real Claude Desktop or Cursor install on
their own machine:

1. `pip install mindkeep[mcp]` from the release candidate.
2. `mindkeep integrate claude-desktop --out <real-path>` (or `cursor-mcp`).
3. Restart the host; confirm the model can call `recall`, `list_facts`,
   and one resource read.
4. With `--allow-writes` enabled, confirm `add_fact` round-trips and
   that re-issuing the same call surfaces the dedup error.
5. File the result (pass / fail + observations) on the v0.4.0 release
   issue.

This is in addition to #39's automated SDK-client integration tests.
Rationale: the SDK-level tests catch protocol regressions, but they do
not catch host-shell-environment problems (PATH, Python install
location, host-app config-file format quirks). A real-user smoke test
is the only check for those.

---

## 16. Implementation issue breakdown

Eight issues, opened against milestone v0.4.0. Numbers below are the
real GitHub issue numbers, filed alongside this design PR.

```
#33 [P0] mcp-skeleton  ─→ #34 [P0] mcp-read-tools  ─→ #36 [P1] mcp-pin-and-resources
                       ╲                            ╲
                        ─→ #35 [P0] mcp-write-tools  ─→ #38 [P1] mcp-server-flags
                                                     ╲
                                                      ─→ #37 [P1] mcp-integrate-targets
                                                       ╲
                                                        ─→ #39 [P2] mcp-integration-tests
                                                         ╲
                                                          ─→ #40 [P2] mcp-docs
```

| #   | Pri | Title                                                          | Depends on   |
| --- | --- | -------------------------------------------------------------- | ------------ |
| #33 | P0  | mcp: project skeleton + entry points + optional dep extra (lazy-import rules §7.1; `--project-dir` flag §8.1) | —            |
| #34 | P0  | mcp: read tools (recall / list_facts / list_adrs / stats / doctor) — extracts `collect_stats`/`collect_doctor` helpers (§3.3) | #33      |
| #35 | P0  | mcp: write tools (add_fact / add_adr) + WriteGuard error mapping + boundary dedup (§9.3) — `set_preference` dropped, `force` not exposed | #33 |
| #36 | P1  | mcp: pin/unpin tools + resources (`mindkeep://facts|adrs|project`) — project resource exposes `id_source`/`resolved_project_dir` (§8.3) | #34, #35 |
| #37 | P1  | mcp: `mindkeep integrate` adds `claude-desktop` / `cursor-mcp` / `continue-mcp` targets — JSON merge with `--out`/`--dry-run`/`--force`, project dir baked in, no `--allow-writes` by default (§12) | #33 |
| #38 | P1  | mcp: `--allow-writes` flag (default read-only) + per-tool max `top`/`limit` enforcement (§9.1, §3.3) | #34, #35  |
| #39 | P2  | mcp: integration test suite using official `mcp` Python SDK client — covers §14.1 regressions | #34, #35, #36 |
| #40 | P2  | mcp: README "MCP server" section + `docs/MCP-INSTALL.md`         | #33–#38     |

P0 must land before any P1. P2 is the release gate: #39 (integration
tests green against the real SDK) is the last thing standing between
`main` and a `v0.4.0` tag.

---

## 17. Open questions

These are decisions made provisionally; they may be revisited in v0.4.1
with real-world data, but they should not block v0.4.0.

- **`mindkeep://project` cacheability.** Decision: not cached;
  `resources/read` re-queries every time. The payload is small
  (sub-1KB) and cache-invalidation rules (write-through? TTL?) are
  the kind of thing that bites in week six. Re-evaluate if it shows
  up in a profile.
- **Naming: `mindkeep_recall` vs `recall`.** Decision: keep the
  `mindkeep_` prefix on every tool. MCP hosts deduplicate tools by
  name across servers; an unprefixed `recall` would collide with any
  other server that has a recall-like tool. The seven-byte cost is
  worth the conflict-free namespace.
- **Project-scoped `set_preference` in v0.5.** Decision: drop entirely
  from v0.4 (§3 table note). Revisit when there is either a project-only
  preference column in core or a clearly-bounded use case from a real
  user. The MCP surface should not be the forcing function for the
  core API change.

---

## 18. References

- Issue #32 — v0.4 design ticket. Closed by this PR.
- Issues #33–#40 — implementation tracking, filed against this design.
- `docs/DESIGN-v0.3.0.md` — predecessor design; §6 ("Trigger design")
  is the world this v0.4 design moves us out of.
- `src/mindkeep/memory_api.py` — the API surface the MCP tools wrap.
- `src/mindkeep/storage.py` — `WriteGuardError`, `StorageError`
  definitions referenced in §10.
- `src/mindkeep/_integrations.py` — superseded for MCP-aware clients
  (§2); retained for non-MCP clients.
- Model Context Protocol spec — https://spec.modelcontextprotocol.io
- MCP Python SDK — https://github.com/modelcontextprotocol/python-sdk
