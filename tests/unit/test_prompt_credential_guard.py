"""[2026-07-20] 提示凭据门禁在显示与发送前复用并只返回脱敏信息。"""

import pytest

from bai_agent.domain.errors import BaiError
from bai_agent.security.credentials import PromptCredentialGuard


def test_transport_credentials_are_separate_and_payload_hits_are_redacted() -> None:
    guard = PromptCredentialGuard()
    safe = {"messages": [{"role": "user", "content": "正常文本"}]}
    assert guard.before_display(safe) == safe
    assert guard.before_send(safe) == safe
    with pytest.raises(BaiError) as raised:
        guard.before_display({"messages": [{"content": "Bearer " + "x" * 24}]})
    assert raised.value.code == "CREDENTIAL_EXPOSURE"
    assert "x" * 24 not in raised.value.safe_message
