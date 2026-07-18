"""统一错误分类（D19）.

WP-0A-T06: 为系统中的所有异常建立 9 类分类标准，使上层可以根据
error_class 做出统一的处理策略（重试、降级、上报、拒绝）。

9 类分类（D19）:
  - validation: 输入校验失败（不重试，4xx）
  - permission: 权限不足（不重试，403）
  - policy: 治理策略拒绝（不重试，403/422）
  - provider: LLM provider 调用失败（按情况重试）
  - timeout: 超时（可重试）
  - budget: 预算/配额超限（不重试，429）
  - conflict: 状态冲突（不重试，409）
  - retryable: 显式标记为可重试的瞬时错误（可重试）
  - internal: 内部错误（默认不重试，500）

设计要点:
  - ClassifiedError 携带 error_class 和 retryable 两个独立字段:
      error_class: 用于路由到不同的处理逻辑
      retryable: 显式标记是否应当重试（覆盖默认分类规则）
  - retry.py 的扩展会识别 ClassifiedError 并读取 retryable 字段
  - 业务代码可通过 raise ClassifiedError(ErrorClass.BUDGET, "...") 抛出
    带分类信息的异常，让上层无需 try/except 多种异常类型

使用示例:
    from src.shared.errors.error_classification import (
        ErrorClass, ClassifiedError,
    )

    # 不可重试的预算超限
    raise ClassifiedError(ErrorClass.BUDGET, "monthly token quota exceeded")

    # 可重试的 provider 故障
    raise ClassifiedError(
        ErrorClass.PROVIDER,
        "upstream provider 5xx",
        retryable=True,
    )

    # 显式标记可重试的瞬时错误
    raise ClassifiedError(
        ErrorClass.RETRYABLE,
        "transient cache miss",
        retryable=True,
    )
"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional

import httpx


class ErrorClass(str, Enum):
    """9 类错误分类（D19）。

    继承 str + Enum 使其可直接 JSON 序列化为字符串，便于日志和 API 响应传递。
    """

    VALIDATION = "validation"
    PERMISSION = "permission"
    POLICY = "policy"
    PROVIDER = "provider"
    TIMEOUT = "timeout"
    BUDGET = "budget"
    CONFLICT = "conflict"
    RETRYABLE = "retryable"
    INTERNAL = "internal"


# 默认重试策略表：每个 ErrorClass 的默认 retryable 值。
# 业务代码在抛出 ClassifiedError 时可通过 retryable=True / False 显式覆盖。
DEFAULT_RETRYABLE_BY_CLASS: dict[ErrorClass, bool] = {
    ErrorClass.VALIDATION: False,
    ErrorClass.PERMISSION: False,
    ErrorClass.POLICY: False,
    ErrorClass.PROVIDER: False,  # provider 故障默认不重试；显式 retryable=True 才重试
    ErrorClass.TIMEOUT: True,
    ErrorClass.BUDGET: False,
    ErrorClass.CONFLICT: False,
    ErrorClass.RETRYABLE: True,
    ErrorClass.INTERNAL: False,
}


class ClassifiedError(Exception):
    """携带错误分类信息的异常基类。

    Attributes:
        error_class: ErrorClass 枚举值，用于路由处理策略。
        retryable: 是否应当重试。默认值取自 DEFAULT_RETRYABLE_BY_CLASS；
            业务代码可通过 retryable 参数显式覆盖。
    """

    def __init__(
        self,
        error_class: ErrorClass,
        message: str,
        *,
        retryable: Optional[bool] = None,
    ) -> None:
        self.error_class = error_class
        # 显式参数优先；None 时回退到分类默认值
        self.retryable = (
            retryable if retryable is not None
            else DEFAULT_RETRYABLE_BY_CLASS.get(error_class, False)
        )
        super().__init__(message)

    def __repr__(self) -> str:
        return (
            f"ClassifiedError(error_class={self.error_class.value!r}, "
            f"retryable={self.retryable!r}, message={str(self)!r})"
        )


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否应当重试。

    识别 ClassifiedError 并读取其 retryable 字段；
    其他异常类型不识别（由 retry.py 的 retryable_exceptions 元组处理）。

    Args:
        exc: 任意异常实例。

    Returns:
        True 如果异常是 ClassifiedError 且标记为 retryable=True。
    """
    if isinstance(exc, ClassifiedError):
        return bool(exc.retryable)
    return False


def classify_exception(exc: BaseException) -> ErrorClass:
    """把任意异常映射到 ErrorClass（用于日志聚合和告警）。

    优先识别 ClassifiedError 自带的 error_class；
    对常见异常类型做启发式映射；兜底返回 INTERNAL。

    Args:
        exc: 任意异常实例。

    Returns:
        最匹配的 ErrorClass 枚举值。
    """
    if isinstance(exc, ClassifiedError):
        return exc.error_class

    # 常见异常类型启发式映射
    if isinstance(exc, asyncio.TimeoutError):
        return ErrorClass.TIMEOUT
    if isinstance(exc, TimeoutError):
        return ErrorClass.TIMEOUT
    if isinstance(exc, PermissionError):
        return ErrorClass.PERMISSION
    if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
        return ErrorClass.VALIDATION
    if isinstance(exc, ConnectionError):
        return ErrorClass.RETRYABLE
    if isinstance(exc, httpx.HTTPError):
        return ErrorClass.PROVIDER
    # 兜底
    return ErrorClass.INTERNAL


__all__ = [
    "ErrorClass",
    "ClassifiedError",
    "DEFAULT_RETRYABLE_BY_CLASS",
    "is_retryable",
    "classify_exception",
]
