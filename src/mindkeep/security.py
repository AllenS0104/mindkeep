"""Pluggable security filters for :class:`MemoryStore`.

Implements the :class:`~mindkeep.memory_api.Filter` protocol (simplified
form defined by ADR-0004 / ARCHITECTURE §8):

    def apply(self, kind: str, field: str, value: str) -> str: ...

Two filters are shipped:

* :class:`SecretsRedactor` — pattern-based redaction of well-known secret
  shapes (AWS keys, GitHub tokens, Azure storage keys, Slack, Google,
  OpenAI, PEM blocks, JWTs) plus a generic ``key=value`` sweep for
  common sensitive key names (``password``, ``api_key``, ``token`` …).
* :class:`SizeLimiter` — truncates runaway blobs (e.g. pasted logs) to
  keep the memory store honest.

Design notes
------------
* Redactions replace the matched span with ``[REDACTED:<kind>]``.
* All built-in patterns are **idempotent** — re-applying the filter to
  already-redacted text is a no-op. The placeholder syntax was chosen
  precisely so that no pattern can match it again.
* Rules are pure regex; no network, no I/O, no 3rd-party deps.
* The AWS secret-access-key pattern intentionally requires an
  ``aws_secret*`` context word: a bare 40-char base64 blob is too
  ambiguous to redact by default.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Pattern

__all__ = ["SecretsRedactor", "SizeLimiter"]


# ──────────────────────────── built-in rule table ────────────────────────────
#
# Order matters. Multi-line / greedy rules (PEM, JWT) run first so that
# later, narrower rules cannot chew off pieces of them. The generic
# key=value sweep runs last so specific patterns have first crack.

_DEFAULT_RULES: "tuple[tuple[str, Pattern[str]], ...]" = (
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----"
            r".*?"
            r"-----END (?:RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    ),
    (
        "github_fine_grained_pat",
        re.compile(r"github_pat_[A-Za-z0-9_]{82,}"),
    ),
    (
        "github_token",
        # Classic tokens: ghp_, gho_, ghs_, ghu_, ghr_ followed by 36 chars.
        re.compile(r"gh[pousr]_[A-Za-z0-9]{36}\b"),
    ),
    (
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "openai_key",
        # Covers both sk- and sk-proj- forms.
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),
    ),
    (
        "azure_storage_key",
        # 88-char base64 ending in "==".
        re.compile(r"\b[A-Za-z0-9+/]{86}==(?:[^A-Za-z0-9+/=]|$)"),
    ),
)


# The AWS secret-access-key rule is *contextual*: we only redact a
# 40-char base64 string if it sits next to an ``aws_secret*`` identifier.
# Replacement must preserve the context prefix, so it gets its own path
# (sub-callable, not a flat pattern).
_AWS_SECRET_CTX = re.compile(
    r"(?i)(aws_secret[a-z_]*\s*[=:]\s*[\"']?)([A-Za-z0-9/+=]{40})(?=[\"'\s,;]|$)"
)


# Generic ``sensitive_key = value`` sweep. Key is preserved; only the
# value is redacted so downstream log readers can still see *which*
# credential leaked. The negative look-arounds prevent matching longer
# identifiers like ``authorization_code`` or ``my_token_name``.
_KV_PATTERN = re.compile(
    r"(?i)"
    r"(?<![A-Za-z0-9_])"
    r"(password|passwd|api[_-]?key|apikey|secret|token|auth)"
    r"(?![A-Za-z0-9_])"
    r"(\s*[=:]\s*)"
    r"(\"[^\"]+\"|'[^']+'|[^\s,;&]+)"
)

_DEFAULT_RULE_NAMES = frozenset(name for name, _ in _DEFAULT_RULES) | {
    "aws_secret_key",
    "kv_secret",
}


def _redacted(kind: str) -> str:
    return f"[REDACTED:{kind}]"


# ──────────────────────────── SecretsRedactor ────────────────────────────


class SecretsRedactor:
    """Filter that substitutes well-known secret shapes with placeholders.

    Parameters
    ----------
    enabled_rules:
        Optional whitelist of rule names. ``None`` (default) enables all
        built-in rules plus any ``custom_patterns``. Unknown names in
        ``enabled_rules`` raise :class:`ValueError` — fail loud rather
        than silently letting secrets through because of a typo.
    custom_patterns:
        Mapping of ``{rule_name: regex_source}`` to layer on top of the
        built-ins. Compiled with ``re.DOTALL`` so multi-line patterns
        work out of the box.
    """

    name = "secrets_redactor"

    def __init__(
        self,
        enabled_rules: Iterable[str] | None = None,
        custom_patterns: Mapping[str, str] | None = None,
    ) -> None:
        custom_compiled: list[tuple[str, Pattern[str]]] = []
        if custom_patterns:
            for rule_name, src in custom_patterns.items():
                if rule_name in _DEFAULT_RULE_NAMES:
                    raise ValueError(
                        f"custom rule name {rule_name!r} collides with a built-in"
                    )
                custom_compiled.append((rule_name, re.compile(src, re.DOTALL)))

        self._all_rules: tuple[tuple[str, Pattern[str]], ...] = (
            _DEFAULT_RULES + tuple(custom_compiled)
        )
        self._all_names: frozenset[str] = frozenset(
            [n for n, _ in self._all_rules] + ["aws_secret_key", "kv_secret"]
        )

        if enabled_rules is None:
            self._enabled = self._all_names
        else:
            requested = frozenset(enabled_rules)
            unknown = requested - self._all_names
            if unknown:
                raise ValueError(f"unknown rule name(s): {sorted(unknown)}")
            self._enabled = requested

    # ---- Filter protocol ------------------------------------------------

    def apply(self, kind: str, field: str, value: str) -> str:  # noqa: ARG002
        if not value:
            return value
        out = value

        # Flat regex rules.
        for rule_name, pattern in self._all_rules:
            if rule_name not in self._enabled:
                continue
            out = pattern.sub(_redacted(rule_name), out)

        # Contextual AWS secret-access-key (preserves the prefix).
        if "aws_secret_key" in self._enabled:
            out = _AWS_SECRET_CTX.sub(
                lambda m: m.group(1) + _redacted("aws_secret_key"), out
            )

        # Generic key=value sweep (preserves key + separator).
        if "kv_secret" in self._enabled:
            out = _KV_PATTERN.sub(self._kv_replace, out)

        return out

    @staticmethod
    def _kv_replace(m: "re.Match[str]") -> str:
        key, sep, val = m.group(1), m.group(2), m.group(3)
        # Already redacted? Leave it alone to keep idempotency cheap.
        if val.startswith("[REDACTED:") and val.endswith("]"):
            return m.group(0)
        tag = f"kv_{key.lower().replace('-', '_')}"
        return f"{key}{sep}{_redacted(tag)}"


# ──────────────────────────── SizeLimiter ────────────────────────────


class SizeLimiter:
    """Filter that hard-caps the length of any field.

    Truncates to ``max_chars`` and appends ``...[truncated N chars]``
    where ``N`` is the number of characters dropped. Values at or below
    the limit pass through untouched.
    """

    name = "size_limiter"

    def __init__(self, max_chars: int = 10_000) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self._max = max_chars

    def apply(self, kind: str, field: str, value: str) -> str:  # noqa: ARG002
        if value is None:
            return value  # type: ignore[return-value]
        n = len(value)
        if n <= self._max:
            return value
        dropped = n - self._max
        return f"{value[: self._max]}...[truncated {dropped} chars]"
