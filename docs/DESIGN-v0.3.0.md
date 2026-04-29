# mindkeep v0.3.0 Design Document

> Status: **Approved with conditions** · Source: PMO meeting 2026-04-29
> Release gate: Eval suite (Issue #10) must pass before tagging v0.3.0.

This document is the source-of-truth design record for mindkeep v0.3.0. It
captures the consensus of a four-perspective review (Architect, Product,
Cost-Economist, Skeptic) and the rationale for what ships, what is deferred,
and the constraints each contributor placed on the design.

---

## 1. Goals

v0.3.0 is the release where mindkeep stops being "a small SQLite-backed note
store with a CLI" and becomes "a token-aware memory layer that an AI agent can
plug into." The four headline goals — in priority order — are:

1. **Reduce per-session token cost by ~3× on the Typical workload**
   (50 facts + 10 ADRs). v0.2.0 dumps everything; v0.3.0 must show only what
   the session asks for, plus pinned items.
2. **Provide intent-triggered retrieval.** Move from `autoload everything` to
   `recall on demand`. The agent decides when to ask; mindkeep decides what to
   return.
3. **Multi-agent integration.** Ship first-class on-ramps for Claude (skills),
   GitHub Copilot CLI (`AGENTS.md`), Cursor (`.cursor/rules/*.mdc`), and a
   generic adapter for other agents.
4. **Make every "saves tokens" claim quantifiable.** Every cost claim in
   marketing or docs must be reproducible via `mindkeep stats` and the eval
   suite (Issue #10). No hand-wavy numbers.

### Non-goals (deferred to v0.4 or later)

- **Automatic salience scoring formula.** Only manual `--pin` lands in v0.3.0.
  We do not yet have enough usage data to design a scoring function that won't
  immediately be wrong.
- **`gc --older-than --unread`.** No real users means no rotting data to
  validate the heuristics against. Shipping a destructive command without a
  test population is how you delete people's notes.
- **LLM-side summarization, vector embeddings, MCP server.** All three are
  plausible v0.4+ work but are out of scope here. Each one breaks the
  zero-runtime-dependency posture in a different way and needs its own design
  doc.

---

## 2. Workload assumptions (Cost-Economist)

All cost claims are anchored to a single reference workload, defined here so
the eval suite can reproduce it.

### Typical workload

- **Store:** 50 facts (avg ~80 tokens each) + 10 ADRs (avg ~220 tokens each).
- **Session shape:** 1 implicit context load at session start, followed by
  ~3 explicit `recall` calls during the session.
- **Estimator:** zero-dep heuristic, `tokens ≈ ceil(chars / 4)` for ASCII,
  `ceil(chars / 2)` for CJK-heavy text. Calibrated against tiktoken on a
  fixture corpus to within ±15% — close enough for budgeting decisions.

### Token math

| Component                          |   Tokens | Notes                                              |
| ---------------------------------- | -------: | -------------------------------------------------- |
| v0.2.0 baseline (autoload all)     |   ~3,400 | 50×80 (facts) + 10×220 (ADRs) + framing overhead   |
| v0.3.0 target (Typical session)    |   ~1,130 | ~50 (registry) + 3×175 (recalls) + ~555 (pinned)   |
| Skill registry tax (always paid)   |     ~50 | Floor cost: agent must learn `recall` exists       |
| Per-`recall` overhead              |    ~175 | top-5 hits w/ snippets, BM25-ranked                |
| Pinned items budget                |    ~555 | Cap at 6 pinned items × ~92 tokens (median fact)   |

**3× claim derivation:** `3,400 / 1,130 ≈ 3.01`. The claim is "approximately
3×" specifically so it survives normal variance. The eval suite must report
the actual ratio per run; if it drops below 2.5× on the Typical workload, the
release is blocked.

### Heavy workload (sanity check)

500 facts + 50 ADRs. v0.2.0 baseline is too large for many context windows
(>30k tokens); v0.3.0 stays bounded by `--budget` regardless of store size.
This is the primary qualitative win — cost stops scaling with store size.

---

## 3. Schema changes (v3) — Architect

### 3.1 New columns on `facts` and `adrs`

```sql
-- Applied to BOTH facts and adrs unless noted.
ALTER TABLE facts ADD COLUMN last_accessed_at TIMESTAMP NULL;
ALTER TABLE facts ADD COLUMN access_count    INTEGER  NOT NULL DEFAULT 0;
ALTER TABLE facts ADD COLUMN pin             INTEGER  NOT NULL DEFAULT 0; -- 0 or 1
ALTER TABLE facts ADD COLUMN archived_at     TIMESTAMP NULL;
ALTER TABLE facts ADD COLUMN token_estimate  INTEGER  NULL;

ALTER TABLE adrs  ADD COLUMN last_accessed_at TIMESTAMP NULL;
ALTER TABLE adrs  ADD COLUMN access_count    INTEGER  NOT NULL DEFAULT 0;
ALTER TABLE adrs  ADD COLUMN pin             INTEGER  NOT NULL DEFAULT 0;
ALTER TABLE adrs  ADD COLUMN archived_at     TIMESTAMP NULL;
ALTER TABLE adrs  ADD COLUMN token_estimate  INTEGER  NULL;
```

Field semantics:

- `last_accessed_at` — set on every `recall` hit and every `show <id>`. NULL
  means "never accessed under v3 tracking" (legacy or freshly inserted).
- `access_count` — monotonically increasing. Never decremented. Used for
  read-heavy detection in stats; not used for ordering in v0.3.0.
- `pin` — 0/1 boolean. Pinned items are always candidates for the
  pinned-items budget. There is no priority among pinned items in v0.3.0;
  ordering is by recency of pinning.
- `archived_at` — soft-delete sentinel. Archived rows are excluded from
  `recall` and `show` by default but remain queryable with `--include-archived`.
- `token_estimate` — computed at write time using the heuristic from §2. Cached
  to avoid recomputation in stats. `mindkeep doctor --recompute-tokens` exists
  for callers who change the estimator.

### 3.2 FTS5 virtual tables

```sql
CREATE VIRTUAL TABLE facts_fts USING fts5(
    content, tags,
    content='facts',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE adrs_fts USING fts5(
    title, decision, rationale,
    content='adrs',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
```

`unicode61` is required for CJK because the default `simple` tokenizer
splits on whitespace only and would treat a Chinese paragraph as one token.

### 3.3 FTS sync triggers

```sql
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, content, tags)
  VALUES (new.rowid, new.content, new.tags);
END;

CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, content, tags)
  VALUES ('delete', old.rowid, old.content, old.tags);
END;

CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, content, tags)
  VALUES ('delete', old.rowid, old.content, old.tags);
  INSERT INTO facts_fts(rowid, content, tags)
  VALUES (new.rowid, new.content, new.tags);
END;
```

Equivalent triplet for `adrs_fts` over `(title, decision, rationale)`. The
`content=` external-content pattern was chosen over storing-in-FTS to keep the
authoritative copy of each row in the base table — backups stay simple.

### 3.4 Migration policy (Skeptic-mandated)

The Skeptic's central concern: "you do not know what timestamps mean for rows
that existed before you started tracking timestamps. Do not lie about them."

Rules:

1. All new columns must be NULLable or have a default of 0. No backfilling
   `last_accessed_at = NOW()` — that would be a lie. Legacy rows stay NULL.
2. A new bookkeeping row in `meta`:
   ```sql
   INSERT INTO meta(key, value) VALUES
     ('access_tracking_started_at', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
   ```
   Any future analysis that depends on access timestamps must read this row
   and carve out a "tracking blind spot" before this date.
3. Future destructive commands that touch staleness (e.g. `gc --unread`) MUST
   require an explicit `--include-unknown-access` flag to act on rows where
   `last_accessed_at IS NULL`. Default behavior: skip with a warning.
4. Schema version bumped to `3` in `meta.schema_version`. `mindkeep doctor`
   refuses to operate on schemas it does not understand and prints the upgrade
   command.
5. Migration is one-way. There is no v3→v2 downgrade. Users who need the old
   shape are told to restore from backup. (mindkeep is a notes store; it is
   the user's responsibility to back up the SQLite file, and we document it.)

---

## 4. CLI surface diff

### 4.1 Existing commands — extended

| Command           | Status   | Change                                                                 |
| ----------------- | -------- | ---------------------------------------------------------------------- |
| `mindkeep show`   | extended | New flags: `--top N`, `--budget TOKENS`, `--pinned`                    |
| `mindkeep doctor` | extended | Now reports token usage, schema version, FTS5 health, orphaned indexes |

`show --top N` returns the N most relevant items for the implicit session
context. `--budget TOKENS` is a hard cap; selection greedily picks
highest-ranked items that fit. `--pinned` restricts output to pinned items
only and is intended for `integrate`-generated bootstrap snippets.

### 4.2 New commands

| Command                                      | Purpose                                  |
| -------------------------------------------- | ---------------------------------------- |
| `mindkeep recall <query> [--top N] [--budget T]` | FTS5 + BM25 retrieval                |
| `mindkeep stats [--json]`                    | Introspection: counts, tokens, hit rates |
| `mindkeep pin <id>` / `mindkeep unpin <id>`  | Minimal manual salience                  |
| `mindkeep integrate <target>`                | Replaces former "skill emit"             |

`recall` ranks via BM25 on the FTS table, then re-orders to push pinned items
into the result set if they match the query at all (pinned-and-matched > raw
BM25 > unpinned BM25 within budget). It updates `last_accessed_at` and
increments `access_count` for every returned row, in a single transaction.

`stats --json` emits a stable schema (documented in `docs/USAGE.md` once
implemented) so the eval suite can consume it directly.

`integrate <target>` accepts: `claude`, `copilot`, `cursor`, `generic`. It
writes the appropriate file(s) for that target into the current directory and
prints the path(s). Re-running is idempotent: existing files are diffed and
the user is prompted before overwrite.

---

## 5. Session-level token budget

mindkeep cannot enforce token usage inside the agent — it can only enforce
what *it* returns. The mechanism:

- **Environment variable:** `MINDKEEP_SESSION_BUDGET=<int>`. If unset, no
  session-level cap is enforced (per-call `--budget` still applies).
- **State file:** a small JSON file with `{ "session_id": "...", "spent": N,
  "started_at": "..." }`. Location:
  - Linux/macOS: `$XDG_RUNTIME_DIR/mindkeep/session.json`, falling back to
    `$TMPDIR/mindkeep-$UID/session.json`.
  - Windows: `%LOCALAPPDATA%\Temp\mindkeep\session.json`.
- **Reset semantics:** a session is identified by `MINDKEEP_SESSION_ID`. If
  unset or changed, the state file is reset on next call. `mindkeep stats
  --reset-session` forces a reset.
- **Enforcement:** when `recall` or `show` would exceed the remaining
  budget, results are truncated to fit and a one-line warning is printed to
  stderr (never stdout — stdout is for the agent). Exit code stays 0; running
  out of budget is not an error, it's a constraint.

Rationale for stderr-only warnings: agents tend to echo whatever they see on
stdout back to the user. Budget exhaustion is operational metadata, not part
of the recall payload.

---

## 6. Trigger design (multi-agent integration)

The four supported targets have genuinely different loading models, and
pretending they're the same produces a worse experience for all four. The
guiding principle: **describe intent, not phrases.** Listing trigger phrases
("when the user says 'remember that'…") is brittle and dates badly. Describing
intent ("when the user references a prior decision the agent does not have in
context") survives model changes.

### 6.1 Per-target loading models

- **Claude (skills):** loaded into a session via the skills directory.
  Registry tax is paid once per session; mindkeep ships a `SKILL.md` that
  describes when to invoke `recall`.
- **GitHub Copilot CLI:** reads `AGENTS.md` from the project root. The
  integration writes a section that points the agent at `mindkeep recall` and
  `mindkeep show --top`.
- **Cursor:** loads `.cursor/rules/*.mdc` files. mindkeep writes a
  `mindkeep.mdc` rule that describes when to call `recall`.
- **Generic:** a plain `MINDKEEP.md` describing the same intent in
  agent-agnostic language, suitable for tools we do not yet support.

### 6.2 Sample artifacts

`SKILL.md` (Claude) — stub:

```markdown
---
name: mindkeep
description: Recall prior project facts and decisions before answering.
---

Use `mindkeep recall <query>` when the user references a prior decision,
naming convention, or fact you do not already have in context. Prefer
`recall` over re-asking the user. Pinned items will appear first.
```

`AGENTS.md` (Copilot CLI) — snippet:

```markdown
## Project memory

This project uses [mindkeep](https://github.com/AllenS0104/mindkeep) as a
local memory store.

- Before answering questions about prior decisions, run
  `mindkeep recall "<topic>" --top 5`.
- To see currently pinned context, run `mindkeep show --pinned`.
- Do not modify the store on the user's behalf without explicit confirmation.
```

`.cursor/rules/mindkeep.mdc` — snippet:

```markdown
---
description: Use mindkeep to recall prior project facts and decisions.
globs: ["**/*"]
alwaysApply: true
---

When the user refers to a prior decision, convention, or fact that is not in
the current context window, call `mindkeep recall "<query>" --top 5` and
incorporate the results before answering. Pinned items take priority.
```

All three intentionally avoid hard-coded phrase lists. They tell the agent
*when* (intent) and *what* (the command), not *which words trigger it*.

---

## 7. Dependency graph

Issue numbers TBD when issues are filed; structure is fixed.

```
P0-1 schema (FTS5 + new columns + migration)
     ├─→ P0-2 session budget (env + state file)
     ├─→ P0-3 show --top / --budget
     ├─→ P0-4 recall command
     │        └─→ P1-5 integrate <target>
     ├─→ P1-6 stats --json
     │        └─→ P1-9 doctor (token usage, FTS health)
     ├─→ P1-7 write guard (token_estimate at insert)
     └─→ P1-8 pin / unpin

   All of the above ─→ P2-10 eval suite gate
```

P0 must land before any P1. P2-10 (eval gate) is the last thing standing
between `main` and a `v0.3.0` tag.

---

## 8. Cut from scope (with rationale)

Each cut has a named owner-perspective, so future contributors know who to
ask before resurrecting it.

- **`mindkeep summary` standalone command** — *Product.* Already covered by
  `recall "" --top 5`. A second command surface for the same operation
  doubles the docs burden without adding capability.
- **Token-count footer always-on** — *Cost-Economist.* Always-on numbers
  invite the agent to comment on them, which costs more tokens than the
  numbers were worth. Ships as opt-in `--show-cost` only.
- **`gc --older-than --unread`** — *Skeptic.* No real users → no rotting
  data → no way to validate the policy → high risk of deleting things people
  cared about. Re-evaluate in v0.4 once we have telemetry from real installs.
- **Salience scoring formula** — *Architect + Skeptic.* Designing a scoring
  function from no data is fiction. Ship `--pin` only; collect access data
  via the v3 schema; revisit in v0.3.1 with real frequencies.
- **"Skill emit" as a universal feature** — *Architect.* The four targets
  have different file formats and different loading models; pretending
  otherwise produces a worst-of-all-worlds output. Replaced by `integrate
  <target>` with target-specific generators.

---

## 9. Open questions

These are decisions made provisionally; they may be revisited in v0.3.1 with
real-world data, but they should not block v0.3.0.

- **Token estimator: tiktoken vs heuristic?**
  Decision: heuristic (`chars/4` for ASCII, `chars/2` for CJK). Adding
  tiktoken would add a non-trivial native dependency and break the
  zero-runtime-dep posture, which is a core value. ±15% accuracy is more
  than enough for budgeting; we are not billing anyone.
- **Session-budget storage path on Windows.**
  Decision: `%LOCALAPPDATA%\Temp\mindkeep\session.json`. There is no exact
  XDG analog on Windows; `%LOCALAPPDATA%\Temp` is the closest match for
  "ephemeral, per-user." Documented alongside the Linux/macOS path.
- **Release timing.**
  Gated on two conditions: (a) eval suite green on the Typical workload
  showing ≥2.5× reduction; (b) at least one non-maintainer user has
  successfully run `mindkeep integrate` against their agent of choice and
  reported back. Time is not a gate.

---

## 10. References

- PR #5 — PyPI publish documentation. Completed prerequisite; v0.3.0
  releases will use that pipeline unchanged.
- Issues #1–#10 — implementation tracking, to be filed against this design.
  Numbering in the dependency graph (§7) is provisional and will be updated
  once GitHub assigns real numbers.
- `docs/USAGE.md` — will be updated alongside the feature PRs, not in this
  doc PR. This file is the design; USAGE is the user-facing reference.
