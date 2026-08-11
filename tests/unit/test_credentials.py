"""[2026-07-19] 凭据门禁必须拒绝原值并只留下不可逆指纹。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.security.credentials import CredentialGuard, secret_fingerprint


FAKE_SECRET = "sk-test-only-1234567890abcdef1234567890"


def test_credential_guard_rejects_secret_without_echo() -> None:
    guard = CredentialGuard()
    with pytest.raises(BaiError) as raised:
        guard.ensure_safe(f"Authorization: Bearer {FAKE_SECRET}")
    rendered = str(raised.value)
    assert FAKE_SECRET not in rendered
    assert raised.value.code == "CREDENTIAL_REJECTED"


def test_fingerprint_is_irreversible() -> None:
    fingerprint = secret_fingerprint(FAKE_SECRET)
    assert FAKE_SECRET not in fingerprint
    assert fingerprint.startswith("sha256:")
    assert fingerprint == secret_fingerprint(FAKE_SECRET)


def test_normal_text_is_preserved() -> None:
    assert CredentialGuard().ensure_safe("普通的非敏感聊天内容") == "普通的非敏感聊天内容"
