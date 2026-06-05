"""U4：transcript 专属扩展脱敏（覆盖面远超 bench/scrub 的 5 类）。"""

from __future__ import annotations

import session_extract as se


def test_scrub_aws_access_key():
    out, hits = se.scrub_secrets("key=AKIAIOSFODNN7EXAMPLE done")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "AWS_ACCESS_KEY" in hits


def test_scrub_aws_secret_kv():
    out, hits = se.scrub_secrets("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    assert "wJalrXUtnFEMI" not in out
    assert "KV_SECRET" in hits


def test_scrub_github_token():
    out, hits = se.scrub_secrets("token ghp_0123456789abcdef0123456789abcdef0123")
    assert "ghp_0123456789abcdef" not in out
    assert "GITHUB_TOKEN" in hits


def test_scrub_db_url():
    out, hits = se.scrub_secrets("DATABASE_URL=postgresql://admin:hunter2@db.internal:5432/app")
    assert "hunter2" not in out
    assert "URL_CRED" in hits


def test_scrub_dotenv_password():
    out, hits = se.scrub_secrets("DB_PASSWORD=s3cr3tValue\nOTHER=ok")
    assert "s3cr3tValue" not in out
    assert "KV_SECRET" in hits
    assert "OTHER=ok" in out  # 非密钥行保留


def test_scrub_pem_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc123\nxyz==\n-----END RSA PRIVATE KEY-----"
    out, hits = se.scrub_secrets(f"key:\n{pem}\ndone")
    assert "MIIabc123" not in out
    assert "PRIVATE_KEY" in hits
    assert "done" in out


def test_scrub_jwt():
    jwt = "eyJhbGciOiJIUzI1Ni;".replace(";", "") + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36"
    out, hits = se.scrub_secrets(f"auth {jwt}")
    assert "JWT" in hits


def test_scrub_clean_text_no_hits():
    out, hits = se.scrub_secrets("just a normal sentence about fizzbuzz")
    assert hits == []
    assert out == "just a normal sentence about fizzbuzz"


def test_residual_secret_risk():
    # 脱敏后仍有字母+数字长 token → 建议人工确认
    assert se.residual_secret_risk("blob a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6") is True
    # 纯散文不触发
    assert se.residual_secret_risk("this is a normal plan with words") is False
