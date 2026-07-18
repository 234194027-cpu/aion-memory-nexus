from __future__ import annotations


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _Connection:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.closed = False
        self.calls: list[str] = []

    def execute(self, statement, _params=None):
        self.calls.append(str(statement))
        return _ScalarResult(self.acquired)

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.connect_calls = 0

    def connect(self) -> _Connection:
        self.connect_calls += 1
        return self.connection


def test_postgres_scheduler_lock_blocks_follower_without_starting_jobs(monkeypatch) -> None:
    from src.shared.config import settings
    from src.shared.db import scheduler

    connection = _Connection(acquired=False)
    monkeypatch.setattr(settings, "POSTGRES_URL", "postgresql://leader-lock-test")
    monkeypatch.setattr(scheduler, "sync_engine", _Engine(connection))
    monkeypatch.setattr(scheduler, "_leader_lock_connection", None)

    assert scheduler.acquire_scheduler_leader_lock() is False
    assert connection.closed is True
    assert scheduler._leader_lock_connection is None


def test_postgres_scheduler_lock_is_released_on_shutdown(monkeypatch) -> None:
    from src.shared.config import settings
    from src.shared.db import scheduler

    connection = _Connection(acquired=True)
    monkeypatch.setattr(settings, "POSTGRES_URL", "postgresql://leader-lock-test")
    monkeypatch.setattr(scheduler, "sync_engine", _Engine(connection))
    monkeypatch.setattr(scheduler, "_leader_lock_connection", None)

    assert scheduler.acquire_scheduler_leader_lock() is True
    assert scheduler._leader_lock_connection is connection

    scheduler.release_scheduler_leader_lock()

    assert connection.closed is True
    assert scheduler._leader_lock_connection is None
    assert any("pg_advisory_unlock" in statement for statement in connection.calls)


def test_follower_does_not_register_or_start_scheduler_jobs(monkeypatch) -> None:
    from src.shared.db import scheduler

    class _Scheduler:
        def __init__(self) -> None:
            self.add_job_calls = 0
            self.running = False

        def add_job(self, *_args, **_kwargs) -> None:
            self.add_job_calls += 1

    fake_scheduler = _Scheduler()
    monkeypatch.setattr(scheduler, "scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler, "_scheduler_started", False)
    monkeypatch.setattr(scheduler, "acquire_scheduler_leader_lock", lambda: False)

    assert scheduler.start_scheduler() is False
    assert fake_scheduler.add_job_calls == 0
