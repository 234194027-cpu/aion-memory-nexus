import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from src.shared.db.database import Base
# 导入所有模型确保 Base.metadata 有内容
from src.memory.models import *  # noqa: F403
from src.cognition.models import *  # noqa: F403
from src.execution.models import *  # noqa: F403
from src.platform.models import *  # noqa: F403

config = context.config

# 优先使用环境变量中的数据库 URL（生产环境），否则回退到 alembic.ini
db_url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
if db_url:
    # Alembic 需要同步驱动，将异步 URL 转换为同步
    db_url = db_url.replace("+aiosqlite", "")
    # 确保使用 psycopg (v3) 驱动而非 psycopg2
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
