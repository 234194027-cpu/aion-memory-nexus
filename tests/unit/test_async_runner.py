from __future__ import annotations


def test_persistent_runner_reuses_one_event_loop() -> None:
    import asyncio

    from src.shared.async_runner import PersistentAsyncRunner

    runner = PersistentAsyncRunner()

    async def loop_id() -> int:
        return id(asyncio.get_running_loop())

    assert runner.run(loop_id()) == runner.run(loop_id())
