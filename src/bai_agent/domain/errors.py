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


def fail(code: str, message: str, *, retryable: bool = False) -> "NoReturn":
    """[2026-07-19] 用统一入口避免不同适配器自行拼接不安全异常文本。"""
    raise BaiError(code, message, retryable=retryable)


from typing import NoReturn  # noqa: E402  # [2026-07-19] 仅用于 fail 的返回注解。

