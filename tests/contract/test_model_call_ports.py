"""[2026-07-20] 模型调用端口测试固定唯一物化与事务职责。"""

import inspect

from bai_agent.domain.ports import (
    ApprovalPresenter,
    ModelCallGateway,
    ProviderAdapter,
    TokenEstimator,
    TurnUnitOfWorkPort,
)


def test_model_call_ports_expose_minimal_contracts() -> None:
    assert tuple(inspect.signature(ProviderAdapter.prepare).parameters) == ("self", "draft", "attempt")
    assert tuple(inspect.signature(ProviderAdapter.materialize_sdk_kwargs).parameters) == ("self", "request")
    assert tuple(inspect.signature(ProviderAdapter.send_once).parameters) == ("self", "payload")
    assert hasattr(ApprovalPresenter, "decide")
    assert hasattr(TokenEstimator, "estimate")
    assert hasattr(ModelCallGateway, "complete")
    assert all(hasattr(TurnUnitOfWorkPort, name) for name in ("begin", "discard", "pending", "ready", "commit"))
