"""结构化日志配置.

提供两种日志格式:
    - development: 人类可读的单行文本 (含 request_id 缩写), 便于本地调试
    - production: 每行一个 JSON 对象, 便于日志聚合服务 (ELK / Loki) 检索分析

仅使用标准库 logging, 不引入 python-json-logger 等额外依赖.

request_id 通过 contextvars 在协程间隔离, 由 ASGI 中间件
(src.shared.utils.request_id.RequestIDMiddleware) 注入.
"""
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# 请求级上下文: 每个请求唯一标识 (由中间件设置, 日志 formatter 读取).
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# 标准 LogRecord 内置属性, 不进入 JSON 的 extra 字段.
# 列表来自 logging.LogRecord 的全部属性 + 我们自己加的字段.
# taskName 是 Python 3.12+ 新增的内置属性, 一并排除避免污染输出.
_LOGRECORD_BUILTIN_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
    # 我们显式提升为顶级字段, 不再放进 extra.
    "request_id", "timestamp", "level", "logger",
})


class JSONFormatter(logging.Formatter):
    """生产环境日志 formatter: 每行一个 JSON 对象.

    输出字段:
        timestamp  ISO8601 UTC 时间
        level      日志级别名
        logger     logger 名
        message    日志消息 (已格式化)
        request_id 请求 id (可能为空字符串)
        exception  异常栈 (仅当 exc_info 存在)
        <extra>    其他通过 extra= 传入的结构化字段
    """

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(""),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        # 收集所有非内置属性作为 extra 字段, 避免遗漏新增埋点字段.
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_BUILTIN_ATTRS:
                continue
            # 跳过不可 JSON 序列化的对象 (如 httpx.AsyncClient), 防止整体序列化失败.
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            log_obj[key] = value
        return json.dumps(log_obj, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """开发环境日志 formatter: 单行人类可读, 含 request_id 缩写."""

    def format(self, record: logging.LogRecord) -> str:
        rid = request_id_var.get("")
        rid_str = f" [req:{rid[:8]}]" if rid else ""
        base = (
            f"{self.formatTime(record)} [{record.levelname}] "
            f"{record.name}{rid_str}: {record.getMessage()}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(environment: str = "development") -> None:
    """初始化根 logger, 根据 environment 选择日志格式.

    - development: DEBUG 级别 + 人类可读格式
    - production / 其他: INFO 级别 + JSON 格式

    替换根 logger 的全部 handler, 避免重复输出 basicConfig 留下的 handler.
    """
    handler = logging.StreamHandler(sys.stdout)
    if environment == "production":
        handler.setFormatter(JSONFormatter())
        level = logging.INFO
    else:
        handler.setFormatter(HumanFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
        level = logging.DEBUG

    root = logging.getLogger()
    # 清掉 basicConfig / 重复 import 残留的 handler, 仅保留我们的 stdout handler.
    root.handlers = [handler]
    root.setLevel(level)

    # 降低常见噪声库的级别, 避免淹没业务日志.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "aiosqlite", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def set_request_id(request_id: str) -> None:
    """设置当前上下文的 request_id (供中间件 / 任务调用)."""
    request_id_var.set(request_id or "")


def get_request_id() -> str:
    """读取当前上下文的 request_id."""
    return request_id_var.get("")
