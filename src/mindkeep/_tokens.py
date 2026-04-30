"""Cheap token estimator (stdlib only).

Heuristic from DESIGN-v0.3.0 §9: ASCII chars/4, CJK chars/2.
This is intentionally approximate — we never call a real tokenizer.
"""
from __future__ import annotations


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    # Common CJK ranges: Hiragana, Katakana, CJK Unified Ideographs,
    # Hangul Syllables, CJK Symbols.
    return (
        0x3000 <= o <= 0x9FFF
        or 0xAC00 <= o <= 0xD7AF
        or 0xF900 <= o <= 0xFAFF
        or 0xFF00 <= o <= 0xFFEF
    )


def estimate(text: str) -> int:
    """Return an approximate token count for *text*.

    ASCII-ish characters cost 1/4 token each; CJK characters cost 1/2.
    Always returns ``>= 1`` for non-empty input so a tiny call still
    debits the session budget.
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        else:
            other += 1
    tokens = (other + 3) // 4 + (cjk + 1) // 2
    return max(1, tokens)
