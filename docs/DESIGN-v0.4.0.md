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

| Tool                          | Mode  | API call                               | Notes                                                   |
| ----------------------------- | ----- | -------------------------------------- | ------------------------------------------------------- |
| `mindkeep_recall`             | read  | `MemoryStore.recall`                   | The headline tool — agents will call this most.         |
| `mindkeep_list_facts`         | read  | `MemoryStore.list_facts`               | For pinned-only / tag-filtered browsing.                |
| `mindkeep_list_adrs`          | read  | `MemoryStore.list_adrs`                | Same shape as `list_facts`.                             |
| `mindkeep_stats`              | read  | reuses `_cmd_stats` JSON path          | Counts, tokens, hit rates.                              |
| `mindkeep_doctor`             | read  | reuses `_cmd_doctor --json`            | Health check; structured `{checks:[...], ok:bool}`.     |
| `mindkeep_add_fact`           | write | `MemoryStore.add_fact`                 | WriteGuard applies; `force` per call.                   |
| `mindkeep_add_adr`            | write | `MemoryStore.add_adr`                  | WriteGuard applies.                                     |
| `mindkeep_set_preference`     | write | `MemoryStore.set_preference`           | Global prefs DB (see §8).                               |
| `mindkeep_pin` / `_unpin`     | write | `pin_fact` / `unpin_fact` (and ADR)    | Two tools, not one — `kind` arg disambiguates.          |

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
    "pin":   { "type": "boolean", "default": false },
    "force": { "type": "boolean", "default": false }
  },
  "required": ["value"]
}
// returns: { "id": int, "token_estimate": int, "pinned": bool }
```

The remaining tools follow the same one-to-one pattern with their API
counterparts; the full schemas live alongside the implementation in
`src/mindkeep/mcp/tools.py`.

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
mcp = [ "mcp>=1.0" ]
```

`pip install mindkeep` stays zero-dep. `pip install mindkeep[mcp]` adds
the SDK. `mindkeep mcp serve` and `mindkeep-mcp` both check for the SDK
at startup:

```python
try:
    import mcp.server  # noqa: F401
except ImportError:
    sys.stderr.write(
        "mindkeep MCP server requires the 'mcp' SDK.\n"
        "Install with: pip install 'mindkeep[mcp]'\n"
    )
    sys.exit(2)
```

Exit code 2 (vs 1) so clients that auto-restart on failure can
distinguish "missing dep" from "runtime error" if they care.

---

## 8. Project context resolution

How does a stdio MCP server know which project store to read from?
mindkeep's existing `MemoryStore.open()` resolves a `ProjectId` from
`cwd`. We honour the same convention by default and add one override:

| Source                          | Precedence | Use case                                   |
| ------------------------------- | ---------- | ------------------------------------------ |
| `MINDKEEP_PROJECT_DIR` env var  | highest    | Pin the server to a fixed project from `claude_desktop_config.json`. |
| `cwd` at server start time      | default    | Match v0.3 CLI behavior; project-local agents. |
| Tool-call argument              | rejected   | See below.                                 |

**Why not per-call `project_id` arguments?** Two reasons:

1. The MCP host does not generally know which projects exist on the
   user's machine. Letting the model pick a project is a cross-project
   data-leak vector ("recall facts from my employer's repo while
   working in my hobby repo").
2. v0.3 has no notion of a project registry the agent could consult;
   project IDs are derived hashes of paths. Asking the agent to guess
   them is hostile UX.

If a power user wants "one server, many projects", they restart the
server with a different `MINDKEEP_PROJECT_DIR`. Multi-project switching
mid-session is an explicit non-goal (§1).

---

## 9. Write-permission story

By default the server allows reads and writes. Two switches:

- **`--read-only`** (CLI flag on `mindkeep mcp serve` and
  `mindkeep-mcp`). Disables every write tool at registration time —
  they do not appear in `tools/list` at all (so the model cannot try
  and fail; it simply does not know they exist). Intended for the
  paranoid: "give the agent read access to my notes, never write."
- **`force` per write call.** The existing `force=True` argument
  surfaces as a tool input on `mindkeep_add_fact` and `mindkeep_add_adr`.
  It is **never** a server-wide setting — the choice to bypass the
  WriteGuard is a per-write decision, the same as in v0.3.

Writes raise `WriteGuardError` if content exceeds the per-kind cap
(facts=100, ADRs=1500 by default). The error surfaces structured
fields — `kind`, `cap`, `pre_tokens`, `post_tokens` — into the MCP
error response (see §10) so the agent can decide whether to retry
with `force=True` or split the content.

---

## 10. Error mapping

MCP follows JSON-RPC 2.0 error semantics. We map mindkeep exceptions
into a small set of error codes plus a structured `data` payload:

| Python exception                          | MCP code  | `error.data` shape                                        | Tool result      |
| ----------------------------------------- | --------- | --------------------------------------------------------- | ---------------- |
| `WriteGuardError`                         | -32001    | `{ kind, cap, pre_tokens, post_tokens, hint }`            | `isError: true`  |
| `ValueError` (unknown id, bad enum)       | -32602    | `{ field, value }`                                        | `isError: true`  |
| `StorageError` (FTS5 missing, schema vN)  | -32002    | `{ reason, schema_version, recoverable: bool }`           | `isError: true`  |
| `RuntimeError` ("MemoryStore is closed")  | -32603    | `{}`                                                      | `isError: true`  |
| Any other exception                       | -32603    | `{ exc_type, traceback_id }` (traceback_id stderr-logged) | `isError: true`  |

Code numbers in the `-32000…-32099` server-error band are MCP-reserved
for application errors (per JSON-RPC). `-32602` (Invalid params) and
`-32603` (Internal error) are stock JSON-RPC codes.

We **always** set `isError: true` on the tool result rather than only
returning a protocol-level error. The MCP spec encourages using tool
results for errors the model can recover from — and "your fact is too
long, retry with `force=true` or shorter text" is exactly that.

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

## 12. Client install snippets

These snippets live in `docs/MCP-INSTALL.md` (new file in the
implementation PR). v0.4 also extends `mindkeep integrate` with three
new targets — `claude-desktop`, `cursor-mcp`, `continue-mcp` — that
emit machine-merged JSON instead of markdown.

**`claude_desktop_config.json`:**

```jsonc
{
  "mcpServers": {
    "mindkeep": {
      "command": "mindkeep-mcp",
      "args": []
    }
  }
}
```

To pin the server to a specific project regardless of where Claude is
launched from:

```jsonc
{
  "mcpServers": {
    "mindkeep": {
      "command": "mindkeep-mcp",
      "args": ["--read-only"],
      "env": { "MINDKEEP_PROJECT_DIR": "/Users/me/code/myrepo" }
    }
  }
}
```

**Cursor (`~/.cursor/mcp.json` or workspace `.cursor/mcp.json`):**

```jsonc
{
  "mcpServers": {
    "mindkeep": { "command": "mindkeep-mcp", "args": [] }
  }
}
```

**Continue (`~/.continue/config.json`):**

```jsonc
{
  "experimental": {
    "modelContextProtocolServers": [
      { "transport": { "type": "stdio", "command": "mindkeep-mcp" } }
    ]
  }
}
```

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

Three layers:

1. **Unit tests** on the tool registration / schema / dispatch logic,
   not requiring a live transport. We use the `mcp` SDK's in-process
   server harness (or, if absent, a minimal fake) to invoke handlers
   directly. Targets: input validation, error mapping (every row of
   §10's table), `--read-only` filtering of the tool list.
2. **Integration tests** using the official `mcp` Python SDK *client*
   against the real `mindkeep-mcp` entry point over stdio. One test
   per tool that round-trips: list tools, call tool, assert result
   shape. One test per resource URI. Marked `@pytest.mark.integration`
   (matches the existing pytest marker convention).
3. **Compat tests** that boot `mindkeep-mcp`, send a `tools/list`
   request, and snapshot the schema. Snapshots live in
   `tests/mcp/snapshots/`. Schema drift is breaking and must be a
   conscious, reviewed change.

Coverage gate: 90% line coverage on `src/mindkeep/mcp/`, matching the
existing project-wide bar.

---

## 15. Scope cuts

Each cut has a one-line "why deferred":

- **SSE / Streamable-HTTP transport** — every shipping host is stdio in 2026; network transport opens auth/CORS/discovery questions that need their own design doc.
- **OAuth / bearer auth on the server** — implied by the cut above; stdio runs as the user, no auth needed.
- **Multi-tenant / per-call project_id** — cross-project data-leak risk and there is no project registry the agent can consult (§8).
- **MCP prompts** — mindkeep has no first-class prompt concept; inventing one alongside the tool surface dilutes both.
- **Incremental / streaming tool responses** — needs streamable-HTTP transport; revisit alongside SSE.
- **Telemetry / audit log** — premature without a network transport; the trust boundary is the user's shell.
- **`mindkeep_clear` / `_export` / `_import` / `_session` tools** — destructive or filesystem-side-effecting, agent has no business doing these autonomously (§3.2).
- **Per-tool rate limiting** — stdio is single-process and single-user; if the agent loops, the user sees CPU spike and kills it. YAGNI.
- **Schema versioning of tool inputs** — the v0.4 schemas are v1; we'll add a versioning policy when we have a v2 to compare against.

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
| #33 | P0  | mcp: project skeleton + entry points + optional dep extra      | —            |
| #34 | P0  | mcp: read tools (recall / list_facts / list_adrs / stats / doctor) | #33      |
| #35 | P0  | mcp: write tools (add_fact / add_adr / set_preference) + WriteGuard error mapping | #33 |
| #36 | P1  | mcp: pin/unpin tools + resources (`mindkeep://facts|adrs|project`) | #34, #35 |
| #37 | P1  | mcp: `mindkeep integrate` adds `claude-desktop` / `cursor-mcp` / `continue-mcp` targets | #33 |
| #38 | P1  | mcp: `--read-only` flag + per-tool max `top`/`limit` enforcement | #34, #35  |
| #39 | P2  | mcp: integration test suite using official `mcp` Python SDK client | #34, #35, #36 |
| #40 | P2  | mcp: README "MCP server" section + `docs/MCP-INSTALL.md`         | #33–#38     |

P0 must land before any P1. P2 is the release gate: #39 (integration
tests green against the real SDK) is the last thing standing between
`main` and a `v0.4.0` tag.

---

## 17. Open questions

These are decisions made provisionally; they may be revisited in v0.4.1
with real-world data, but they should not block v0.4.0.

- **`mcp` SDK version pin.** Decision: `mcp>=1.0`. The SDK had a
  stabilization push for the 1.0 line; pinning to ≥1.0 protects us
  from pre-1.0 churn while staying loose enough that bugfix releases
  flow through. Revisit if 1.x proves unstable.
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
