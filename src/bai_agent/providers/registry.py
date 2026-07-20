"""[2026-07-19] Provider 注册表只允许显式适配器，并在网络调用前核对能力。"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from bai_agent.domain.errors import BaiError
from bai_agent.providers.deepseek import DeepSeekProvider
from bai_agent.security.credentials import read_secret


def create_provider(provider: dict[str, Any], profile: dict[str, Any]) -> DeepSeekProvider:
    if provider.get("adapter") != "deepseek_openai_compatible":
        raise BaiError("PROVIDER_ADAPTER_UNKNOWN", "Provider 适配器未注册。")
    if profile.get("stream", False):
        raise BaiError("PROVIDER_CAPABILITY_INVALID", "首版不允许展示未确认流式输出。")
    # [2026-07-20] SDK 内部重试会绕过逐物理请求审批；重试只允许由 ModelCallGateway 统一执行。
    client = AsyncOpenAI(
        api_key=read_secret(str(provider["api_key_env"])),
        base_url=str(provider["base_url"]),
        max_retries=0,
    )
    merged = dict(profile)
    merged["provider_id"] = str(provider["id"])
    merged["retryable_statuses"] = tuple(
        provider.get("retry", {}).get("retryable_statuses", (429, 500, 503))
    )
    return DeepSeekProvider(client, merged)
