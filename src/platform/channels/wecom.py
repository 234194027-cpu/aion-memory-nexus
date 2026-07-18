import time
import json
import uuid
import asyncio
import base64
from typing import Optional, Callable, Dict, Any, Awaitable
import httpx
import websockets
import logging
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from src.shared.config import settings
from src.shared.utils.runtime_metrics import runtime_metrics

logger = logging.getLogger(__name__)


class WeComBotMessage:
    def __init__(self, data: Dict[str, Any]):
        self.raw = data
        self.msg_type = data.get("msgtype", "text")
        self.from_user = data.get("from_userid") or data.get("from", {}).get("userid", "")
        self.to_user = data.get("to_userid", "")
        self.chat_id = data.get("chatid", "")
        self.chat_type = data.get("chattype", "")
        self.aibot_id = data.get("aibotid", "")
        self.content = ""
        self.msg_id = data.get("msgid", "")
        
        if self.msg_type == "text" and "text" in data:
            self.content = data["text"].get("content", "")
    
    def __repr__(self):
        return f"WeComBotMessage(from={self.from_user}, type={self.msg_type}, content={self.content[:50]})"


class WeComBotClient:
    WS_URL = "wss://openws.work.weixin.qq.com"
    API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
    CMD_MESSAGE_CALLBACK = "aibot_msg_callback"
    CMD_EVENT_CALLBACK = "aibot_event_callback"
    CMD_LEGACY_MESSAGE = "aibot_msg"
    CMD_RESPOND_MESSAGE = "aibot_respond_msg"
    CMD_RESPOND_WELCOME_MESSAGE = "aibot_respond_welcome_msg"
    CMD_SEND_MESSAGE = "aibot_send_msg"
    CMD_HEARTBEAT = "ping"
    HEARTBEAT_INTERVAL_SECONDS = 30
    DEFAULT_WELCOME_TEXT = "你好，我是人生记忆助手。你可以直接和我聊天、记录想法，或问我关于你过往记忆的问题。"
    
    def __init__(self, bot_id: str, secret: str):
        self.bot_id = bot_id
        self.secret = secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._message_handler: Optional[Callable[[WeComBotMessage], Awaitable[str]]] = None
        self._connected = False
        self._last_error: Optional[str] = None
        self._reconnect_count = 0
        self._max_reconnect = 10
        self._send_lock = asyncio.Lock()
        # Sending after a dropped socket and the receive loop can observe the
        # same disconnect at almost the same moment.  Only one path may create
        # the replacement socket; otherwise WeCom can invalidate the previous
        # subscription and leave an apparently healthy bot unable to reply.
        self._reconnect_lock = asyncio.Lock()
    
    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.API_BASE}/gettoken",
                    params={
                        "corpid": self.bot_id,
                        "corpsecret": self.secret,
                    },
                    timeout=30,
                )
                data = response.json()
                if data.get("errcode") != 0:
                    raise Exception(f"Failed to get access token: {data}")
                self._access_token = data["access_token"]
                self._token_expires_at = time.time() + data["expires_in"]
                runtime_metrics.record_external_call("wecom_token")
                return self._access_token
        except Exception:
            runtime_metrics.record_external_call("wecom_token", failed=True)
            raise
    
    async def send_text_message(self, user_id: str, content: str) -> dict:
        # WeCom AI Bot proactive send supports markdown/template_card; plain text
        # is valid for passive welcome replies but returns 40008 for aibot_send_msg.
        return await self.send_chat_message(user_id, {
            "msgtype": "markdown",
            "markdown": {"content": content},
        })
    
    async def send_markdown_message(self, user_id: str, content: str) -> dict:
        return await self.send_chat_message(user_id, {
            "msgtype": "markdown",
            "markdown": {"content": content},
        })

    async def reply_text_message(self, frame: Dict[str, Any], content: str) -> dict:
        body = frame.get("body", {})
        stream_id = body.get("msgid") or str(uuid.uuid4())
        return await self._send_ws_frame(
            req_id=frame.get("headers", {}).get("req_id", str(uuid.uuid4())),
            cmd=self.CMD_RESPOND_MESSAGE,
            body={
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "content": content,
                    "finish": True,
                },
            },
        )

    async def reply_welcome_message(self, frame: Dict[str, Any], content: str | None = None) -> dict:
        """Reply to an ``enter_chat`` event using WeCom's dedicated welcome command."""
        return await self._send_ws_frame(
            req_id=frame.get("headers", {}).get("req_id", str(uuid.uuid4())),
            cmd=self.CMD_RESPOND_WELCOME_MESSAGE,
            body={
                "msgtype": "text",
                "text": {"content": content or self.DEFAULT_WELCOME_TEXT},
            },
        )

    async def send_chat_message(self, chat_id: str, body: Dict[str, Any]) -> dict:
        return await self._send_ws_frame(
            req_id=f"{self.CMD_SEND_MESSAGE}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            cmd=self.CMD_SEND_MESSAGE,
            body={"chatid": chat_id, **body},
        )

    async def download_file(self, url: str, aes_key: str | None = None) -> tuple[bytes, str | None, str | None]:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.content
            filename = _filename_from_content_disposition(response.headers.get("content-disposition", ""))
            content_type = response.headers.get("content-type")
        if aes_key:
            data = decrypt_wecom_file(data, aes_key)
        return data, filename, content_type

    async def _send_ws_frame(self, req_id: str, cmd: str, body: Dict[str, Any]) -> dict:
        frame = {
            "cmd": cmd,
            "headers": {"req_id": req_id},
            "body": body,
        }

        async with self._send_lock:
            if not self._is_ws_open():
                await self._reconnect_for_send()

            if not self._is_ws_open():
                runtime_metrics.record_external_call("wecom_send", failed=True)
                return {"errcode": -1, "errmsg": "websocket_not_connected", "last_error": self._last_error}

            try:
                await self._ws.send(json.dumps(frame, ensure_ascii=False))
                runtime_metrics.record_external_call("wecom_send")
                return {"errcode": 0, "errmsg": "sent", "cmd": cmd, "req_id": req_id}
            except websockets.exceptions.ConnectionClosed as exc:
                self._connected = False
                self._last_error = str(exc)
                await self._reconnect_for_send()
                if not self._is_ws_open():
                    runtime_metrics.record_external_call("wecom_send", failed=True)
                    return {"errcode": -1, "errmsg": "websocket_closed", "last_error": self._last_error}
                try:
                    await self._ws.send(json.dumps(frame, ensure_ascii=False))
                except Exception as retry_exc:
                    self._connected = False
                    self._last_error = _safe_wecom_error(retry_exc)
                    logger.warning("WeCom retry send failed: %s", self._last_error)
                    runtime_metrics.record_external_call("wecom_send", failed=True)
                    return {"errcode": -1, "errmsg": "websocket_retry_failed", "last_error": self._last_error}
                runtime_metrics.record_external_call("wecom_send")
                return {"errcode": 0, "errmsg": "sent_after_reconnect", "cmd": cmd, "req_id": req_id}
            except Exception as exc:
                self._connected = False
                self._last_error = _safe_wecom_error(exc)
                logger.warning("WeCom send failed: %s", self._last_error)
                runtime_metrics.record_external_call("wecom_send", failed=True)
                return {"errcode": -1, "errmsg": "websocket_send_failed", "last_error": self._last_error}

    def _is_ws_open(self) -> bool:
        return bool(self._ws and self._connected and not self._ws.closed)

    async def _reconnect_for_send(self) -> None:
        async with self._reconnect_lock:
            if not self._running or self._is_ws_open():
                return
            self._connected = False
            previous_ws = self._ws
            self._ws = None
            try:
                if previous_ws:
                    await previous_ws.close()
            except Exception:
                pass
            await self._connect_ws()
    
    def set_message_handler(self, handler: Callable[[WeComBotMessage], Awaitable[str]]):
        self._message_handler = handler
    
    async def connect(self):
        if self._running:
            return
        
        self._running = True
        self._reconnect_count = 0
        await self._connect_ws()
    
    async def _connect_ws(self):
        try:
            self._ws = await websockets.connect(
                self.WS_URL,
                # WeCom AI bots use an application-level `{"cmd":"ping"}`
                # heartbeat, not RFC WebSocket Ping frames.  Sending generic
                # WebSocket pings causes the gateway to close the connection
                # with protocol error 1002 after the first heartbeat cycle.
                ping_interval=None,
                ping_timeout=None,
                open_timeout=15,
                close_timeout=10,
            )
            
            subscribe_msg = {
                "cmd": "aibot_subscribe",
                "headers": {
                    "req_id": str(uuid.uuid4()),
                },
                "body": {
                    "bot_id": self.bot_id,
                    "secret": self.secret,
                }
            }
            
            await self._ws.send(json.dumps(subscribe_msg))
            
            first_response = await asyncio.wait_for(self._ws.recv(), timeout=15)
            first_data = json.loads(first_response)
            
            if self._is_subscribe_success(first_data):
                self._connected = True
                self._last_error = None
                self._reconnect_count = 0
                self._receive_task = asyncio.create_task(self._receive_loop())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(self._ws))
                runtime_metrics.record_external_call("wecom_connection")
                logger.info("WeCom long connection established")
            elif first_data.get("cmd") == "aibot_subscribe_reply":
                body = first_data.get("body", {})
                self._last_error = _subscription_error(body)
                self._connected = False
                raise Exception(self._last_error)
            else:
                self._last_error = f"Unexpected subscribe response: cmd={first_data.get('cmd', '')}"
                self._connected = False
                raise Exception(self._last_error)
                
        except Exception as e:
            self._last_error = _safe_wecom_error(e)
            self._connected = False
            runtime_metrics.record_external_call("wecom_connection", failed=True)
            logger.warning(
                "WeCom long connection failed (attempt %s/%s): %s",
                self._reconnect_count + 1,
                self._max_reconnect + 1,
                self._last_error,
            )
            if self._running and self._reconnect_count < self._max_reconnect:
                self._reconnect_count += 1
                await asyncio.sleep(min(5 * self._reconnect_count, 60))
                await self._connect_ws()
            elif self._running:
                logger.error("WeCom long connection stopped after retry budget was exhausted")

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Maintain WeCom's own long-connection heartbeat protocol."""
        try:
            while self._running and self._ws is ws and self._is_ws_open():
                await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
                if not (self._running and self._ws is ws and self._is_ws_open()):
                    return
                await ws.send(json.dumps({
                    "cmd": self.CMD_HEARTBEAT,
                    "headers": {"req_id": str(uuid.uuid4())},
                    "body": {},
                }))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The receive loop owns reconnection.  This task only reports the
            # failed heartbeat and leaves the active socket state unchanged.
            logger.warning("WeCom heartbeat failed: %s", _safe_wecom_error(exc))
    
    async def _receive_loop(self):
        ws = self._ws
        if not ws:
            return
        try:
            async for message in ws:
                try:
                    data = json.loads(message)
                    cmd = data.get("cmd", "")
                    
                    if cmd in {self.CMD_MESSAGE_CALLBACK, self.CMD_LEGACY_MESSAGE}:
                        body = data.get("body", {})
                        msg = WeComBotMessage(body)
                        logger.warning(
                            "WeCom message received: cmd=%s from=%s type=%s msg_id=%s",
                            cmd, msg.from_user, msg.msg_type, msg.msg_id,
                        )
                        
                        if self._message_handler:
                            try:
                                reply = await self._message_handler(msg)
                                runtime_metrics.record_task("wecom_message")
                                if reply:
                                    await self.reply_text_message(data, reply)
                            except Exception as e:
                                runtime_metrics.record_task("wecom_message", failed=True)
                                error_msg = f"处理消息时出错: {str(e)}"
                                try:
                                    await self.reply_text_message(data, error_msg)
                                except Exception as send_err:
                                    logger.warning(f"Failed to send error reply: {send_err}")
                    
                    elif cmd == self.CMD_EVENT_CALLBACK:
                        await self._handle_event_callback(data)

                    elif cmd == "aibot_pong":
                        pass

                    elif not cmd and data.get("errcode") == 0 and data.get("errmsg") == "ok":
                        # WeCom sends command-less success acknowledgements for
                        # application heartbeats. They are protocol control
                        # frames, not unhandled business messages.
                        logger.debug("WeCom protocol acknowledgement received")

                    else:
                        body = data.get("body", {})
                        body_keys = list(body.keys()) if isinstance(body, dict) else []
                        logger.warning(
                            "WeCom unhandled frame: cmd=%s errcode=%s errmsg=%s keys=%s body_keys=%s",
                            cmd,
                            data.get("errcode"),
                            data.get("errmsg"),
                            list(data.keys()),
                            body_keys,
                        )
                    
                except json.JSONDecodeError:
                    continue
                except Exception:
                    logger.warning("WS message parse error", exc_info=True)
                    continue
                    
        except websockets.exceptions.ConnectionClosed as exc:
            # A sender may already have installed a replacement socket.  A
            # stale receive loop must never reconnect over that subscription.
            if self._ws is not ws:
                return
            self._connected = False
            self._last_error = _safe_wecom_error(exc)
            logger.warning("WeCom socket closed; scheduling reconnect: %s", self._last_error)
            if self._running and self._reconnect_count < self._max_reconnect:
                self._reconnect_count += 1
                await asyncio.sleep(min(5 * self._reconnect_count, 60))
                await self._connect_ws()
        except Exception as e:
            if self._ws is not ws:
                return
            self._last_error = _safe_wecom_error(e)
            self._connected = False
            logger.warning("WeCom receive loop failed: %s", self._last_error)

    async def _handle_event_callback(self, frame: Dict[str, Any]) -> None:
        """Handle non-message Bot callbacks without routing them as conversation text."""
        body = frame.get("body", {})
        event = body.get("event", {}) if isinstance(body, dict) else {}
        event_type = event.get("eventtype", "") if isinstance(event, dict) else ""
        logger.info(
            "WeCom bot event received: type=%s msg_id=%s",
            event_type or "unknown",
            body.get("msgid", "") if isinstance(body, dict) else "",
        )

        if event_type != "enter_chat":
            return

        try:
            await self.reply_welcome_message(frame)
            runtime_metrics.record_task("wecom_enter_chat")
        except Exception as exc:
            runtime_metrics.record_task("wecom_enter_chat", failed=True)
            logger.warning("Failed to send WeCom welcome reply: %s", _safe_wecom_error(exc))

    def _is_subscribe_success(self, data: Dict[str, Any]) -> bool:
        """WeCom long-connection subscribe success has appeared in two shapes."""
        if data.get("cmd") == "aibot_subscribe_reply":
            return data.get("body", {}).get("errcode") == 0
        return data.get("errcode") == 0 and data.get("errmsg") == "ok"
    
    async def disconnect(self):
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        self._connected = False
    
    def is_connected(self) -> bool:
        return self._connected
    
    def get_status(self) -> dict:
        return {
            "bot_id_configured": bool(self.bot_id),
            "secret_configured": bool(self.secret),
            "connected": self._connected,
            "running": self._running,
            "last_error": self._last_error,
            "reconnect_count": self._reconnect_count,
        }


_default_bot: Optional[WeComBotClient] = None

def get_wecom_bot() -> Optional[WeComBotClient]:
    global _default_bot
    if _default_bot:
        return _default_bot
    
    if not settings.WECOM_BOT_ID or not settings.WECOM_BOT_SECRET:
        return None
    
    _default_bot = WeComBotClient(
        bot_id=settings.WECOM_BOT_ID,
        secret=settings.WECOM_BOT_SECRET,
    )
    return _default_bot


def _safe_wecom_error(exc: Exception) -> str:
    """Keep operator diagnostics useful without writing credentials to logs."""
    message = str(exc).replace("\n", " ").strip()
    return f"{type(exc).__name__}: {message[:300]}" if message else type(exc).__name__


def _subscription_error(body: Any) -> str:
    if not isinstance(body, dict):
        return "Subscription failed: invalid response body"
    errcode = body.get("errcode", "unknown")
    errmsg = str(body.get("errmsg", "unknown")).replace("\n", " ")[:200]
    return f"Subscription failed: errcode={errcode}, errmsg={errmsg}"


def decrypt_wecom_file(encrypted_data: bytes, aes_key: str) -> bytes:
    if not encrypted_data:
        raise ValueError("encrypted_data_empty")
    if not aes_key:
        raise ValueError("aes_key_required")
    padded_key = aes_key + "=" * ((4 - len(aes_key) % 4) % 4)
    key = base64.b64decode(padded_key)
    iv = key[:16]
    block_size = 16
    remainder = len(encrypted_data) % block_size
    if remainder:
        encrypted_data = encrypted_data + b"\x00" * (block_size - remainder)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted_data) + decryptor.finalize()
    if not decrypted:
        raise ValueError("decrypted_data_empty")
    pad_len = decrypted[-1]
    if pad_len < 1 or pad_len > 32 or pad_len > len(decrypted):
        raise ValueError("invalid_pkcs7_padding")
    if any(item != pad_len for item in decrypted[-pad_len:]):
        raise ValueError("invalid_pkcs7_padding_bytes")
    return decrypted[:-pad_len]


def _filename_from_content_disposition(value: str) -> str | None:
    if not value:
        return None
    import re
    from urllib.parse import unquote

    match = re.search(r"filename\*=UTF-8''([^;\s]+)", value, re.IGNORECASE)
    if match:
        return unquote(match.group(1))
    match = re.search(r'filename="?([^";\s]+)"?', value, re.IGNORECASE)
    if match:
        return unquote(match.group(1))
    return None
