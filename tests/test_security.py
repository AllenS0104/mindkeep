"""Tests for mindkeep.security: SecretsRedactor and SizeLimiter."""

from __future__ import annotations

from pathlib import Path

import pytest

from mindkeep import MemoryStore, SecretsRedactor, SizeLimiter


# ─────────────── helper ───────────────


def redact(text: str, **kwargs) -> str:
    return SecretsRedactor(**kwargs).apply("fact", "content", text)


# ─────────────── per-rule: positive + negative ───────────────


def test_aws_access_key_positive() -> None:
    out = redact("key: AKIAIOSFODNN7EXAMPLE here")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws_access_key]" in out


def test_aws_access_key_negative_wrong_shape() -> None:
    # Lowercase / wrong length must not trigger.
    out = redact("key: AKIAshort and akiaiosfodnn7example")
    assert "[REDACTED" not in out


def test_aws_secret_key_positive_with_context() -> None:
    out = redact('aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"')
    assert "wJalrXUtnFEMI" not in out
    assert "[REDACTED:aws_secret_key]" in out
    # Prefix preserved so the *which* is still observable.
    assert "aws_secret_access_key" in out


def test_aws_secret_key_negative_without_context() -> None:
    # A bare 40-char base64 blob must NOT be redacted by default
    # (too many false positives like hashes, commit SHAs, etc.).
    out = redact("sha: wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEYY")
    assert "[REDACTED:aws_secret_key]" not in out


def test_github_classic_token_positive() -> None:
    tok = "ghp_" + "a" * 36
    out = redact(f"auth: {tok}")
    assert tok not in out
    assert "[REDACTED:github_token]" in out


def test_github_classic_token_negative_short() -> None:
    out = redact("ghp_tooShort")
    assert "[REDACTED:github_token]" not in out


def test_github_fine_grained_positive() -> None:
    tok = "github_pat_" + "A1b2_" * 20  # 100 chars after prefix, > 82
    out = redact(tok)
    assert "[REDACTED:github_fine_grained_pat]" in out


def test_github_fine_grained_negative_too_short() -> None:
    out = redact("github_pat_" + "a" * 10)
    assert "[REDACTED:github_fine_grained_pat]" not in out


def test_azure_storage_key_positive() -> None:
    key = "A" * 86 + "=="
    out = redact(f"AccountKey={key};")
    # kv_secret will also fire (key=...). Either redaction is fine; the
    # raw secret must be gone.
    assert key not in out
    assert "[REDACTED:" in out


def test_azure_storage_key_negative_wrong_length() -> None:
    out = redact("blob: " + ("A" * 50 + "=="))
    assert "[REDACTED:azure_storage_key]" not in out


def test_slack_token_positive() -> None:
    out = redact("slack: xoxb-123456789012-ABCDEFGHIJKL")
    assert "xoxb-123456789012-ABCDEFGHIJKL" not in out
    assert "[REDACTED:slack_token]" in out


def test_slack_token_negative() -> None:
    out = redact("xox-nope-short")
    assert "[REDACTED:slack_token]" not in out


def test_google_api_key_positive() -> None:
    key = "AIza" + "x" * 35
    out = redact(f"k={key}")
    assert key not in out
    assert "[REDACTED:" in out  # either google_api_key or kv_secret


def test_google_api_key_negative() -> None:
    out = redact("AIza_but_too_short")
    assert "[REDACTED:google_api_key]" not in out


def test_openai_classic_key_positive() -> None:
    key = "sk-" + "A" * 48
    out = redact(f"OPENAI={key}")
    assert key not in out
    assert "[REDACTED:" in out


def test_openai_project_key_positive() -> None:
    key = "sk-proj-" + "X" * 40
    out = redact(f"key: {key}")
    assert key not in out
    assert "[REDACTED:openai_key]" in out


def test_openai_key_negative() -> None:
    out = redact("sk-short")
    assert "[REDACTED:openai_key]" not in out


def test_pem_private_key_block_positive() -> None:
    pem = (
        "prefix\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA...\n"
        "deadbeefdeadbeef\n"
        "-----END RSA PRIVATE KEY-----\n"
        "suffix"
    )
    out = redact(pem)
    assert "MIIEpAIBAAKCAQEA" not in out
    assert "[REDACTED:pem_private_key]" in out
    assert "prefix" in out and "suffix" in out


def test_pem_private_key_negative_no_block() -> None:
    out = redact("just talking about PRIVATE KEY in prose")
    assert "[REDACTED:pem_private_key]" not in out


def test_jwt_positive() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    out = redact(f"Bearer {jwt}")
    assert jwt not in out
    assert "[REDACTED:jwt]" in out


def test_jwt_negative() -> None:
    out = redact("eyJonly_one_segment")
    assert "[REDACTED:jwt]" not in out


def test_kv_password_positive() -> None:
    out = redact("password=hunter2 and password: s3cr3t")
    assert "hunter2" not in out
    assert "s3cr3t" not in out
    assert out.count("[REDACTED:kv_password]") == 2


def test_kv_api_key_variants() -> None:
    out = redact("api_key=abc, api-key: def, apikey=ghi")
    assert "abc" not in out and "def" not in out and "ghi" not in out


def test_kv_negative_longer_identifier() -> None:
    # ``authorization_code`` must not be eaten by the ``auth`` rule.
    out = redact("authorization_code=visible123")
    assert "visible123" in out
    assert "[REDACTED" not in out


def test_kv_negative_non_sensitive_key() -> None:
    out = redact("username=alice")
    assert "[REDACTED" not in out


# ─────────────── mixed-content + idempotency ───────────────


def test_multiple_secrets_all_redacted() -> None:
    text = (
        "aws=AKIAIOSFODNN7EXAMPLE\n"
        "gh=ghp_" + "Z" * 36 + "\n"
        "password=hunter2\n"
    )
    out = redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "ghp_" + "Z" * 36 not in out
    assert "hunter2" not in out


def test_idempotent_second_pass_is_noop() -> None:
    text = "AKIAIOSFODNN7EXAMPLE and password=hunter2"
    r = SecretsRedactor()
    once = r.apply("fact", "content", text)
    twice = r.apply("fact", "content", once)
    assert once == twice


# ─────────────── configuration knobs ───────────────


def test_enabled_rules_whitelist() -> None:
    r = SecretsRedactor(enabled_rules={"aws_access_key"})
    out = r.apply("fact", "c", "AKIAIOSFODNN7EXAMPLE and password=hunter2")
    assert "[REDACTED:aws_access_key]" in out
    # kv_secret was NOT enabled.
    assert "hunter2" in out


def test_enabled_rules_unknown_raises() -> None:
    with pytest.raises(ValueError):
        SecretsRedactor(enabled_rules={"no_such_rule"})


def test_custom_pattern_applied() -> None:
    r = SecretsRedactor(custom_patterns={"internal_id": r"EMP-\d{6}"})
    out = r.apply("fact", "c", "employee EMP-123456 joined")
    assert "EMP-123456" not in out
    assert "[REDACTED:internal_id]" in out


def test_custom_pattern_name_collision_rejected() -> None:
    with pytest.raises(ValueError):
        SecretsRedactor(custom_patterns={"aws_access_key": r"."})


def test_empty_input_passthrough() -> None:
    assert redact("") == ""


# ─────────────── MemoryStore integration ───────────────


def test_memory_store_redacts_fact_on_write(tmp_path: Path) -> None:
    store = MemoryStore.open(
        cwd=tmp_path,
        data_dir=tmp_path,
        filters=[SecretsRedactor()],
    )
    store.add_fact("my AKIAIOSFODNN7EXAMPLE is leaked, password=hunter2")
    facts = store.list_facts()
    assert len(facts) == 1
    content = facts[0].value if hasattr(facts[0], "value") else facts[0]["value"]
    assert "AKIAIOSFODNN7EXAMPLE" not in content
    assert "hunter2" not in content
    assert "[REDACTED:aws_access_key]" in content
    assert "[REDACTED:kv_password]" in content


# ─────────────── SizeLimiter ───────────────


def test_size_limiter_passthrough_under_limit() -> None:
    lim = SizeLimiter(max_chars=100)
    assert lim.apply("fact", "c", "short") == "short"


def test_size_limiter_exact_boundary() -> None:
    lim = SizeLimiter(max_chars=5)
    assert lim.apply("fact", "c", "abcde") == "abcde"


def test_size_limiter_truncates_over_limit() -> None:
    lim = SizeLimiter(max_chars=10)
    out = lim.apply("fact", "c", "a" * 100)
    assert out.startswith("a" * 10)
    assert out.endswith("...[truncated 90 chars]")


def test_size_limiter_far_over_limit() -> None:
    lim = SizeLimiter(max_chars=3)
    out = lim.apply("fact", "c", "x" * 1_000_000)
    assert out.startswith("xxx")
    assert "truncated 999997 chars" in out


def test_size_limiter_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        SizeLimiter(max_chars=0)
