"""One persistent asyncio loop for synchronous worker entry points.

Celery invokes synchronous task functions.  Creating a fresh event loop for
every task breaks async database pools and reusable HTTP clients, so workers
submit all coroutine work to this process-local loop instead.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Coroutine, TypeVar


T = TypeVar("T")


class PersistentAsyncRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="life-memory-async-worker",
                daemon=True,
            )
            self._thread.start()
        self._ready.wait(timeout=10)
        if self._loop is None:
            raise RuntimeError("persistent_async_runner_start_failed")
        return self._loop

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def submit(self, coroutine: Coroutine[object, object, T]) -> Future[T]:
        return asyncio.run_coroutine_threadsafe(coroutine, self._ensure_started())

    def run(self, coroutine: Coroutine[object, object, T]) -> T:
        return self.submit(coroutine).result()


persistent_async_runner = PersistentAsyncRunner()


def schedule_coroutine(coroutine: Coroutine[object, object, T]) -> asyncio.Task[T] | Future[T]:
    """Use the current application loop, or the persistent worker loop."""
    try:
        return asyncio.get_running_loop().create_task(coroutine)
    except RuntimeError:
        return persistent_async_runner.submit(coroutine)
