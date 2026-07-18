"""异步重试装饰器 (参考 Letta 的 LLM 调用重试机制).

设计目标:
    - 仅对特定异常重试 (httpx.HTTPError, asyncio.TimeoutError, ConnectionError)
    - 不重试 ValueError / TypeError / KeyError 等业务异常
    - 指数退避: delay = initial_delay * (backoff_factor ** attempt)
    - 默认配置: max_retries=3, backoff_factor=2 -> 1s, 2s, 4s

WP-0A-T06 扩展:
    - 识别 ClassifiedError 并读取其 retryable 字段（D19）
    - 识别 provider 429 状态码（httpx.HTTPStatusError 且 status_code == 429）
    - asyncio.TimeoutError 已在默认覆盖范围内

用法 1 (装饰器, 无参):
    @with_retry
    async def call_llm(): ...

用法 2 (带参装饰器):
    @with_retry(max_retries=5, backoff_factor=2.0)
    async def call_llm(): ...

用法 3 (函数包装, 用于在不修改原函数定义的情况下添加重试):
    retried_generate = with_retry(provider.generate, max_retries=3)
    result = await retried_generate(prompt)

注意: 不修改 src/shared/llm/providers.py (避免和其他 agent 冲突),
     在调用方用 with_retry 包装 provider.generate 即可.
"""
import asyncio
import functools
import logging
from typing import Tuple, Type, Callable, Optional, Any

import httpx

from src.shared.errors.error_classification import ClassifiedError

logger = logging.getLogger(__name__)

# 默认重试的异常类型: 网络错误 + 超时.
# 不包含 ValueError / TypeError / KeyError / json.JSONDecodeError 等业务异常.
DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    httpx.HTTPError,
    asyncio.TimeoutError,
    ConnectionError,
    ClassifiedError,  # WP-0A-T06: 由 retryable 字段决定是否重试
)

# provider 429 状态码: 显式可重试的限流响应
PROVIDER_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _is_provider_retryable_status(exc: BaseException) -> bool:
    """检查异常是否为 provider 429/5xx 状态码（httpx.HTTPStatusError）.

    通过鸭子类型检查 response.status_code 字段，避免在 import 时硬依赖
    httpx.HTTPStatusError 的具体类路径（兼容 mock 测试）。
    """
    response = getattr(exc, "response", None)
    if response is None:
        return False
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        return False
    return status_code in PROVIDER_RETRYABLE_STATUS_CODES


def _should_retry_exception(
    exc: BaseException,
    retryable_exceptions: Tuple[Type[BaseException], ...],
) -> bool:
    """决定是否对单个异常重试。

    优先级:
      1. ClassifiedError: 仅当 retryable=True 时重试（忽略 retryable_exceptions）
      2. provider 429/5xx 状态码: 显式可重试（即使不在 retryable_exceptions 中）
      3. retryable_exceptions 匹配: isinstance 检查

    Args:
        exc: 捕获的异常实例。
        retryable_exceptions: 配置的可重试异常类型 tuple。

    Returns:
        True 如果应当重试。
    """
    # 1. ClassifiedError 由 retryable 字段决定（D19）
    if isinstance(exc, ClassifiedError):
        return bool(exc.retryable)
    # 2. provider 429/5xx 状态码（httpx.HTTPStatusError 子类）
    if _is_provider_retryable_status(exc):
        return True
    # 3. 默认 isinstance 检查
    return isinstance(exc, retryable_exceptions)


def async_retry(
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    initial_delay: float = 1.0,
    retryable_exceptions: Optional[Tuple[Type[BaseException], ...]] = None,
):
    """异步函数重试装饰器工厂.

    Args:
        max_retries: 最大重试次数 (不含首次调用). 默认 3.
        backoff_factor: 退避因子, delay = initial_delay * (backoff_factor ** attempt).
                        默认 2.0 -> 1s, 2s, 4s.
        initial_delay: 首次重试前等待秒数. 默认 1.0.
        retryable_exceptions: 触发重试的异常类型 tuple. 默认网络/超时异常.

    Returns:
        装饰器函数.
    """
    if retryable_exceptions is None:
        retryable_exceptions = DEFAULT_RETRYABLE_EXCEPTIONS

    def decorator(func: Callable[..., Any]):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc: Optional[BaseException] = None
            # attempt 0 是首次调用, 1..max_retries 是重试
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    # WP-0A-T06: 用统一判定函数识别 ClassifiedError + 429 + 默认集合
                    if not _should_retry_exception(e, retryable_exceptions):
                        # 非 retryable 异常直接抛出, 不重试
                        raise
                    last_exc = e
                    if attempt >= max_retries:
                        logger.warning(
                            "async_retry: %s failed after %d retries: error_type=%s",
                            getattr(func, "__name__", repr(func)),
                            max_retries,
                            type(e).__name__,
                        )
                        raise
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.info(
                            "async_retry: %s attempt %d/%d failed (error_type=%s); retry in %.1fs",
                            getattr(func, "__name__", repr(func)),
                            attempt + 1,
                            max_retries,
                            type(e).__name__,
                            delay,
                    )
                    await asyncio.sleep(delay)
                # 非 retryable 异常直接抛出, 不重试 (不进入 except 分支)
            # 理论上不会执行到这里 (循环内要么 return 要么 raise)
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("async_retry: unreachable")

        return wrapper

    return decorator


def with_retry(
    func: Optional[Callable[..., Any]] = None,
    *,
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    initial_delay: float = 1.0,
    retryable_exceptions: Optional[Tuple[Type[BaseException], ...]] = None,
):
    """便捷包装: 既可作为装饰器使用, 也可作为函数包装器.

    支持三种用法 (见模块 docstring).
    """
    if func is not None and callable(func):
        # 用法 1: @with_retry (无参) 或 with_retry(some_func)
        return async_retry(
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            initial_delay=initial_delay,
            retryable_exceptions=retryable_exceptions,
        )(func)

    # 用法 2: @with_retry(max_retries=5) (带参装饰器)
    return async_retry(
        max_retries=max_retries,
        backoff_factor=backoff_factor,
        initial_delay=initial_delay,
        retryable_exceptions=retryable_exceptions,
    )
