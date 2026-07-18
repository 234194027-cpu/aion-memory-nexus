import asyncio
import logging
from typing import Dict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理用户 WebSocket 连接，支持心跳检测。"""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self._heartbeat_intervals: Dict[str, asyncio.Task] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        # Start heartbeat
        self._start_heartbeat(user_id)

    def disconnect(self, user_id: str):
        self.active_connections.pop(user_id, None)
        # Stop heartbeat
        task = self._heartbeat_intervals.pop(user_id, None)
        if task:
            task.cancel()

    def _start_heartbeat(self, user_id: str):
        """Start periodic ping for the connection."""
        async def _ping():
            try:
                while True:
                    await asyncio.sleep(30)
                    ws = self.active_connections.get(user_id)
                    if ws:
                        await ws.send_json({"event": "ping"})
                    else:
                        break
            except (asyncio.CancelledError, Exception):
                pass
        
        self._heartbeat_intervals[user_id] = asyncio.create_task(_ping())

    async def send_json(self, user_id: str, data: dict):
        ws = self.active_connections.get(user_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(user_id)

    async def send_token(self, user_id: str, token: str, *, event: str = "token"):
        await self.send_json(user_id, {"event": event, "data": token})

    async def send_done(self, user_id: str, *, result: dict = None):
        await self.send_json(user_id, {"event": "done", "data": result or {}})

    async def send_error(self, user_id: str, message: str):
        await self.send_json(user_id, {"event": "error", "message": message})


ws_manager = ConnectionManager()
