"""WP-0A-T06 单元测试: 错误分类 + 重试扩展 + idempotency_key 列.

测试目标:
  - ErrorClass 9 类分类值稳定（D19）
  - ClassifiedError 默认 retryable 策略正确
  - ClassifiedError 显式 retryable 覆盖默认值
  - classify_exception 启发式映射正确
  - is_retryable 辅助函数识别 ClassifiedError
  - retry.py 扩展覆盖 ClassifiedError(retryable=True)
  - retry.py 不重试 ClassifiedError(retryable=False)
  - retry.py 识别 provider 429 状态码
  - retry.py 不重试 4xx 客户端错误（除 429 外）
"""
from __future__ import annotations

import asyncio

import pytest

from src.shared.errors.error_classification import (
    ErrorClass,
    ClassifiedError,
    DEFAULT_RETRYABLE_BY_CLASS,
    is_retryable,
    classify_exception,
)
from src.shared.utils.retry import (
    async_retry,
    with_retry,
    _is_provider_retryable_status,
    _should_retry_exception,
    DEFAULT_RETRYABLE_EXCEPTIONS,
    PROVIDER_RETRYABLE_STATUS_CODES,
)


# ============================================================================
# Step 1: ErrorClass 9 类分类值稳定
# ============================================================================

EXPECTED_ERROR_CLASSES = {
    "validation",
    "permission",
    "policy",
    "provider",
    "timeout",
    "budget",
    "conflict",
    "retryable",
    "internal",
}


def test_error_class_has_exactly_9_categories():
    """ErrorClass 必须有恰好 9 个分类（D19）."""
    members = {member.value for member in ErrorClass}
    assert members == EXPECTED_ERROR_CLASSES
    assert len(ErrorClass) == 9


def test_error_class_values_are_strings():
    """ErrorClass 继承 str + Enum，每个成员的值必须是 str."""
    for member in ErrorClass:
        assert isinstance(member.value, str)


def test_error_class_is_json_serializable():
    """ErrorClass 必须可被 JSON 序列化（继承 str）."""
    import json
    payload = {"error_class": ErrorClass.PROVIDER.value}
    serialized = json.dumps(payload)
    assert "provider" in serialized


# ============================================================================
# Step 2: ClassifiedError 默认 retryable 策略正确
# ============================================================================


def test_classified_error_default_retryable_uses_lookup_table():
    """ClassifiedError 默认 retryable 必须取自 DEFAULT_RETRYABLE_BY_CLASS."""
    for error_class, expected_retryable in DEFAULT_RETRYABLE_BY_CLASS.items():
        err = ClassifiedError(error_class, "test message")
        assert err.retryable == expected_retryable, (
            f"retryable mismatch for {error_class}: "
            f"expected={expected_retryable}, got={err.retryable}"
        )


def test_classified_error_timeout_defaults_to_retryable():
    """timeout 类默认 retryable=True."""
    err = ClassifiedError(ErrorClass.TIMEOUT, "operation timed out")
    assert err.retryable is True


def test_classified_error_retryable_class_defaults_to_true():
    """retryable 类默认 retryable=True."""
    err = ClassifiedError(ErrorClass.RETRYABLE, "transient failure")
    assert err.retryable is True


def test_classified_error_provider_defaults_to_not_retryable():
    """provider 类默认 retryable=False（避免无脑重试 5xx）."""
    err = ClassifiedError(ErrorClass.PROVIDER, "provider 500")
    assert err.retryable is False


def test_classified_error_budget_defaults_to_not_retryable():
    """budget 类默认 retryable=False（配额超限不应重试）."""
    err = ClassifiedError(ErrorClass.BUDGET, "quota exceeded")
    assert err.retryable is False


def test_classified_error_validation_defaults_to_not_retryable():
    """validation 类默认 retryable=False（输入错误不应重试）."""
    err = ClassifiedError(ErrorClass.VALIDATION, "invalid input")
    assert err.retryable is False


def test_classified_error_internal_defaults_to_not_retryable():
    """internal 类默认 retryable=False（默认不重试内部错误）."""
    err = ClassifiedError(ErrorClass.INTERNAL, "unexpected error")
    assert err.retryable is False


# ============================================================================
# Step 3: ClassifiedError 显式 retryable 覆盖默认值
# ============================================================================


def test_classified_error_explicit_retryable_true_overrides_default():
    """显式 retryable=True 必须覆盖默认值（即使分类默认 False）."""
    err = ClassifiedError(
        ErrorClass.PROVIDER,
        "retryable provider error",
        retryable=True,
    )
    assert err.retryable is True


def test_classified_error_explicit_retryable_false_overrides_default():
    """显式 retryable=False 必须覆盖默认值（即使分类默认 True）."""
    err = ClassifiedError(
        ErrorClass.TIMEOUT,
        "non-retryable timeout (e.g. deadline passed)",
        retryable=False,
    )
    assert err.retryable is False


def test_classified_error_carries_error_class_attribute():
    """ClassifiedError 必须暴露 error_class 属性."""
    err = ClassifiedError(ErrorClass.CONFLICT, "state conflict")
    assert err.error_class is ErrorClass.CONFLICT


def test_classified_error_carries_message():
    """ClassifiedError 必须把 message 传给父类 Exception."""
    err = ClassifiedError(ErrorClass.POLICY, "policy rejected")
    assert str(err) == "policy rejected"


def test_classified_error_repr_contains_class_and_retryable():
    """__repr__ 必须包含 error_class 和 retryable 字段（便于日志）."""
    err = ClassifiedError(ErrorClass.PROVIDER, "msg", retryable=True)
    repr_str = repr(err)
    assert "provider" in repr_str
    assert "retryable=True" in repr_str


# ============================================================================
# Step 4: is_retryable 辅助函数
# ============================================================================


def test_is_retryable_returns_true_for_classified_error_retryable():
    """is_retryable 对 retryable=True 的 ClassifiedError 返回 True."""
    err = ClassifiedError(ErrorClass.PROVIDER, "msg", retryable=True)
    assert is_retryable(err) is True


def test_is_retryable_returns_false_for_classified_error_not_retryable():
    """is_retryable 对 retryable=False 的 ClassifiedError 返回 False."""
    err = ClassifiedError(ErrorClass.PROVIDER, "msg", retryable=False)
    assert is_retryable(err) is False


def test_is_retryable_returns_false_for_non_classified_error():
    """is_retryable 对非 ClassifiedError 异常返回 False."""
    assert is_retryable(ValueError("not classified")) is False
    assert is_retryable(asyncio.TimeoutError()) is False
    assert is_retryable(RuntimeError("plain")) is False


# ============================================================================
# Step 5: classify_exception 启发式映射
# ============================================================================


def test_classify_exception_returns_self_error_class_for_classified_error():
    """classify_exception 对 ClassifiedError 返回其自带的 error_class."""
    err = ClassifiedError(ErrorClass.BUDGET, "quota")
    assert classify_exception(err) is ErrorClass.BUDGET


def test_classify_exception_maps_timeout_error_to_timeout():
    """TimeoutError 必须映射到 TIMEOUT."""
    assert classify_exception(TimeoutError("timed out")) is ErrorClass.TIMEOUT


def test_classify_exception_maps_asyncio_timeout_to_timeout():
    """asyncio.TimeoutError 必须映射到 TIMEOUT."""
    assert classify_exception(asyncio.TimeoutError()) is ErrorClass.TIMEOUT


def test_classify_exception_maps_permission_error_to_permission():
    """PermissionError 必须映射到 PERMISSION."""
    assert classify_exception(PermissionError("denied")) is ErrorClass.PERMISSION


def test_classify_exception_maps_value_error_to_validation():
    """ValueError 必须映射到 VALIDATION."""
    assert classify_exception(ValueError("bad input")) is ErrorClass.VALIDATION


def test_classify_exception_maps_type_error_to_validation():
    """TypeError 必须映射到 VALIDATION."""
    assert classify_exception(TypeError("bad type")) is ErrorClass.VALIDATION


def test_classify_exception_maps_key_error_to_validation():
    """KeyError 必须映射到 VALIDATION."""
    assert classify_exception(KeyError("missing")) is ErrorClass.VALIDATION


def test_classify_exception_maps_connection_error_to_retryable():
    """ConnectionError 必须映射到 RETRYABLE."""
    assert classify_exception(ConnectionError("refused")) is ErrorClass.RETRYABLE


def test_classify_exception_falls_back_to_internal():
    """未知异常必须兜底到 INTERNAL."""
    assert classify_exception(RuntimeError("unknown")) is ErrorClass.INTERNAL


# ============================================================================
# Step 6: retry.py 扩展覆盖 ClassifiedError
# ============================================================================


def test_default_retryable_exceptions_includes_classified_error():
    """DEFAULT_RETRYABLE_EXCEPTIONS 必须包含 ClassifiedError."""
    assert ClassifiedError in DEFAULT_RETRYABLE_EXCEPTIONS


def test_should_retry_classified_error_when_retryable_true():
    """_should_retry_exception 对 retryable=True 的 ClassifiedError 返回 True."""
    err = ClassifiedError(ErrorClass.PROVIDER, "msg", retryable=True)
    assert _should_retry_exception(err, DEFAULT_RETRYABLE_EXCEPTIONS) is True


def test_should_retry_does_not_retry_classified_error_when_retryable_false():
    """_should_retry_exception 对 retryable=False 的 ClassifiedError 返回 False."""
    err = ClassifiedError(ErrorClass.PROVIDER, "msg", retryable=False)
    assert _should_retry_exception(err, DEFAULT_RETRYABLE_EXCEPTIONS) is False


def test_retry_decorator_retries_on_classified_error_retryable_true():
    """with_retry 必须重试 retryable=True 的 ClassifiedError."""
    call_count = 0

    @with_retry(max_retries=2, backoff_factor=1.0, initial_delay=0.0)
    async def flaky_function():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ClassifiedError(ErrorClass.PROVIDER, "retry me", retryable=True)
        return "success"

    result = asyncio.run(flaky_function())
    assert result == "success"
    assert call_count == 3


def test_retry_decorator_does_not_retry_classified_error_retryable_false():
    """with_retry 不重试 retryable=False 的 ClassifiedError."""
    call_count = 0

    @with_retry(max_retries=3, backoff_factor=1.0, initial_delay=0.0)
    async def non_retryable_function():
        nonlocal call_count
        call_count += 1
        raise ClassifiedError(ErrorClass.BUDGET, "no retry")

    with pytest.raises(ClassifiedError) as exc_info:
        asyncio.run(non_retryable_function())
    assert exc_info.value.error_class is ErrorClass.BUDGET
    assert call_count == 1


def test_retry_decorator_does_not_retry_value_error():
    """with_retry 不重试 ValueError 等业务异常."""
    call_count = 0

    @with_retry(max_retries=3, backoff_factor=1.0, initial_delay=0.0)
    async def invalid_function():
        nonlocal call_count
        call_count += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        asyncio.run(invalid_function())
    assert call_count == 1


def test_retry_policy_does_not_retry_local_file_errors():
    """FileNotFoundError is an OSError but is not a transient provider failure."""
    error = FileNotFoundError("missing local file")
    assert _should_retry_exception(error, DEFAULT_RETRYABLE_EXCEPTIONS) is False


# ============================================================================
# Step 7: provider 429 状态码识别
# ============================================================================


class _StubResponse:
    """模拟 httpx.Response 的最小 stub."""
    def __init__(self, status_code: int):
        self.status_code = status_code


class _StubHTTPStatusError(Exception):
    """模拟 httpx.HTTPStatusError 的最小 stub（鸭子类型）."""
    def __init__(self, message: str, response: _StubResponse):
        super().__init__(message)
        self.response = response


def test_provider_retryable_status_codes_includes_429():
    """429 必须在 PROVIDER_RETRYABLE_STATUS_CODES 中."""
    assert 429 in PROVIDER_RETRYABLE_STATUS_CODES


def test_provider_retryable_status_codes_includes_5xx():
    """5xx 必须在 PROVIDER_RETRYABLE_STATUS_CODES 中."""
    assert 500 in PROVIDER_RETRYABLE_STATUS_CODES
    assert 502 in PROVIDER_RETRYABLE_STATUS_CODES
    assert 503 in PROVIDER_RETRYABLE_STATUS_CODES
    assert 504 in PROVIDER_RETRYABLE_STATUS_CODES


def test_is_provider_retryable_status_returns_true_for_429():
    """429 响应必须识别为可重试."""
    err = _StubHTTPStatusError("rate limited", _StubResponse(429))
    assert _is_provider_retryable_status(err) is True


def test_is_provider_retryable_status_returns_true_for_503():
    """503 响应必须识别为可重试."""
    err = _StubHTTPStatusError("service unavailable", _StubResponse(503))
    assert _is_provider_retryable_status(err) is True


def test_is_provider_retryable_status_returns_false_for_400():
    """400 响应不重试（4xx 客户端错误）."""
    err = _StubHTTPStatusError("bad request", _StubResponse(400))
    assert _is_provider_retryable_status(err) is False


def test_is_provider_retryable_status_returns_false_for_404():
    """404 响应不重试."""
    err = _StubHTTPStatusError("not found", _StubResponse(404))
    assert _is_provider_retryable_status(err) is False


def test_is_provider_retryable_status_returns_false_for_no_response():
    """没有 response 属性的异常不重试."""
    err = ValueError("no response here")
    assert _is_provider_retryable_status(err) is False


def test_should_retry_exception_recognizes_429_status_code():
    """_should_retry_exception 必须识别 429 状态码为可重试."""
    err = _StubHTTPStatusError("rate limited", _StubResponse(429))
    # 即使 retryable_exceptions 不包含 _StubHTTPStatusError，也应当重试
    assert _should_retry_exception(err, ()) is True


def test_retry_decorator_retries_on_429_status():
    """with_retry 必须重试 429 响应."""
    call_count = 0

    @with_retry(max_retries=2, backoff_factor=1.0, initial_delay=0.0)
    async def rate_limited_function():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise _StubHTTPStatusError("rate limited", _StubResponse(429))
        return "success-after-retry"

    result = asyncio.run(rate_limited_function())
    assert result == "success-after-retry"
    assert call_count == 2
