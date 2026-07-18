"""请求级 request_id ASGI 中间件.

为每个 HTTP 请求生成 / 透传唯一 request_id, 写入 contextvars,
供结构化日志 (logging_config.JSONFormatter) 关联同一请求的所有日志行.

设计要点:
    - 使用纯 ASGI 中间件 (而非 BaseHTTPMiddleware), 避免 contextvars 在
      任何子任务中丢失, 这是 Starlette 官方推荐做法.
    - 优先从入站 X-Request-ID 头读取 (便于上下游链路追踪), 否则生成 uuid4.
    - 回写到响应头 X-Request-ID, 方便客户端 / 网关关联.

注册方式 (待 src/app/main.py 集成时添加, 当前不修改 main.py):

    from src.shared.utils.request_id import RequestIDMiddleware
    app.add_middleware(RequestIDMiddleware)

注意: FastAPI 中间件注册顺序是"后添加先执行", 该中间件应尽早执行,
建议在 CORSMiddleware 之前 add_middleware.
"""
import uuid
import re
from typing import Awaitable, Callable, MutableMapping

from src.shared.utils.logging_config import request_id_var

# ASGI 三元组类型别名 (scope / receive / send).
Scope = MutableMapping[str, object]
Receive = Callable[[], Awaitable[MutableMapping[str, object]]]
Send = Callable[[MutableMapping[str, object]], Awaitable[None]]

REQUEST_ID_HEADER = "x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIDMiddleware:
    """纯 ASGI 中间件: 注入 request_id 到 contextvars 并回写响应头."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # 从入站请求头读取 request_id (大小写不敏感), 没有则生成新的.
        headers = scope.get("headers") or []
        incoming_id = ""
        for raw_name, raw_value in headers:
            try:
                name = raw_name.decode("latin-1").lower()
            except (AttributeError, UnicodeDecodeError):
                continue
            if name == REQUEST_ID_HEADER:
                try:
                    incoming_id = raw_value.decode("latin-1").strip()
                except UnicodeDecodeError:
                    incoming_id = ""
                break

        request_id = incoming_id if _SAFE_REQUEST_ID.fullmatch(incoming_id) else uuid.uuid4().hex
        # 设置 contextvar; 使用 set 返回的 token 以便请求结束后还原,
        # 避免协程复用时残留上一个请求的 id.
        token = request_id_var.set(request_id)

        # 把 request_id 也放进 scope, 方便后续中间件 / 路由直接读取.
        scope["request_id"] = request_id

        async def send_with_header(message):
            # 仅在 http 响应起始消息 (http.response.start) 上追加响应头.
            if message.get("type") == "http.response.start":
                headers_out = list(message.get("headers") or [])
                headers_out.append(
                    (
                        REQUEST_ID_HEADER.encode("latin-1"),
                        request_id.encode("latin-1"),
                    )
                )
                message["headers"] = headers_out
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)
