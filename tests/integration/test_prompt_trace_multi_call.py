"""[2026-07-20] 多调用按 curation、chat、tool、retry 顺序逐项批准且互不合并。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bai_agent.application import build_application
from bai_agent.domain.errors import BaiError
from bai_agent.model_calls.gateway import ModelCallGateway
from bai_agent.providers.deepseek import DeepSeekProvider
from tests.prompt_debug_fakes import FakeAdapter, FakePresenter, UnavailableEstimator, make_draft


@pytest.mark.asyncio
async def test_multi_call_sequence_and_retry_attempt_are_distinct() -> None:
    adapter = FakeAdapter(failures=[BaiError("PROVIDER_FAILED", "失败。", retryable=True)])
    presenter = FakePresenter()
    gateway = ModelCallGateway(adapter, debug_enabled=True, presenter=presenter, estimator=UnavailableEstimator(), max_attempts=2, backoff_seconds=0)
    await gateway.complete(make_draft(sequence=7, purpose="memory_curation"))
    await gateway.complete(make_draft(sequence=8, purpose="chat"))
    await gateway.complete(make_draft(sequence=9, purpose="tool_continuation"))
    assert presenter.decisions == [
        ("call-7", 1), ("call-7", 2), ("call-8", 1), ("call-9", 1)
    ]
    assert [state.status for state in gateway.call_states if state.status in {"provider_failed", "completed"}] == [
        "provider_failed", "completed", "completed", "completed"
    ]


@pytest.mark.asyncio
async def test_deepseek_tool_continuation_is_approved_once_per_successful_send(tmp_path: Path) -> None:
    class ToolThenAnswerCompletions:
        def __init__(self) -> None:
            self.requests = []

        async def create(self, **kwargs):
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                call = SimpleNamespace(
                    id="call-source-1",
                    type="function",
                    function=SimpleNamespace(
                        name="memory_source_query",
                        arguments='{"memory_id":"missing"}',
                    ),
                )
                message = SimpleNamespace(content="", tool_calls=[call])
                finish_reason = "tool_calls"
            else:
                message = SimpleNamespace(content="最终回答", tool_calls=None)
                finish_reason = "stop"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
                usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
            )

    completions = ToolThenAnswerCompletions()
    adapter = DeepSeekProvider(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        {
            "model": "deepseek-v4-flash",
            "stream": False,
            "thinking_enabled": False,
            "max_output_tokens": 8192,
            "retryable_statuses": [429, 500, 503],
        },
    )
    presenter = FakePresenter()
    app = build_application(
        Path("config"),
        tmp_path / "data",
        provider=adapter,
        debug_prompts=True,
        presenter=presenter,
    )
    try:
        assert await app.run_turn("查一下缺失来源") == "最终回答"
    finally:
        app.close()

    assert len(completions.requests) == 2
    assert [attempt for _call_id, attempt in presenter.decisions] == [1, 1]
    assert all(
        request["extra_body"] == {"thinking": {"type": "disabled"}}
        for request in completions.requests
    )
    continuation = completions.requests[1]["messages"]
    assistant_index = next(index for index, message in enumerate(continuation) if message["role"] == "assistant")
    assert continuation[assistant_index]["tool_calls"][0]["id"] == "call-source-1"
    assert continuation[assistant_index + 1]["role"] == "tool"
    assert continuation[assistant_index + 1]["tool_call_id"] == "call-source-1"
