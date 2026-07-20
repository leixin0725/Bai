"""[2026-07-19] 稳定错误只携带安全消息、错误码和可重试语义。"""

from __future__ import annotations


class BaiError(Exception):
    """[2026-07-19] 对外错误不得包含正文、凭据、SDK 堆栈或真实绝对路径。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


class TurnRejected(BaiError):
    """[2026-07-20] 明确拒绝是领域决定，不得复用普通 provider 失败路径。"""

    def __init__(self) -> None:
        super().__init__("TURN_REJECTED", "当前轮次已无痕撤销。")


class TurnInterrupted(TurnRejected):
    """[2026-07-20] TUI Ctrl+C 先复用拒绝回滚，再由 CLI 映射为退出码 130。"""

    def __init__(self) -> None:
        BaiError.__init__(self, "TURN_INTERRUPTED", "当前轮次已撤销，进程已中断。")


class TraceIntegrityError(BaiError):
    def __init__(self, message: str = "提示来源不完整，调用已阻止。") -> None:
        super().__init__("TRACE_INTEGRITY_FAILED", message)


class CredentialExposureError(BaiError):
    def __init__(self) -> None:
        super().__init__("CREDENTIAL_EXPOSURE", "提示载荷疑似包含可用凭据，原值未显示或发送。")


class DebugPresentationError(BaiError):
    def __init__(self, message: str = "调试批准界面不可用，调用已阻止。") -> None:
        super().__init__("DEBUG_PRESENTATION_FAILED", message)


def fail(code: str, message: str, *, retryable: bool = False) -> "NoReturn":
    """[2026-07-19] 用统一入口避免不同适配器自行拼接不安全异常文本。"""
    raise BaiError(code, message, retryable=retryable)


from typing import NoReturn  # noqa: E402  # [2026-07-19] 仅用于 fail 的返回注解。
