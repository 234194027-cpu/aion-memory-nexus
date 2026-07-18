import os
from pathlib import Path
import tempfile
from uuid import uuid4
import asyncio

import pytest

# 在导入 app 之前设置测试环境变量
os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "testing"
_test_db_name = f"test_life_memory_{uuid4().hex}.db"
_test_db_path = Path(tempfile.gettempdir()) / _test_db_name
os.environ["POSTGRES_URL"] = f"sqlite+aiosqlite:///{_test_db_path.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["BOOTSTRAP_DEFAULTS"] = "false"
os.environ["AUTO_PATCH_SCHEMA"] = "false"
os.environ["CREATE_SCHEMA_ON_STARTUP"] = "true"
os.environ["SOLO_MODE"] = "false"
os.environ["ALLOW_DEV_AUTH_FALLBACK"] = "false"


@pytest.fixture(autouse=True)
def ensure_test_schema():
    from src.shared.db.database import init_db

    asyncio.run(init_db())


def pytest_sessionfinish(session, exitstatus):
    for suffix in ("", "-shm", "-wal"):
        try:
            _test_db_path.with_name(_test_db_path.name + suffix).unlink(missing_ok=True)
        except OSError:
            pass
