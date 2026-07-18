"""app 包初始化: 在导入期完成日志系统初始化.

把 setup_logging 放在这里而不是 main.py, 是因为:
    - 多个 agent 并发修改 main.py, 在 __init__.py 调用可避免冲突.
    - 任何 `from src.app...` 导入都会触发本模块, 保证日志配置先于业务代码生效.
    - main.py 顶部的 logging.basicConfig 会被 setup_logging 覆盖 (handlers 被替换).

环境判定: 读取 src.shared.config.settings.ENVIRONMENT,
production -> JSON 结构化日志, 其他 -> 人类可读日志.
"""
import logging

from src.shared.config import settings
from src.shared.utils.logging_config import setup_logging

# 幂等初始化: 仅在第一次 import 时执行. 用模块级标记防止重复 setup.
_logging_initialized = False


def _init_logging() -> None:
    global _logging_initialized
    if _logging_initialized:
        return
    setup_logging(environment=settings.ENVIRONMENT)
    _logging_initialized = True
    logging.getLogger(__name__).debug(
        "logging initialized",
        extra={"environment": settings.ENVIRONMENT},
    )


# 导入期立即执行, 确保后续 main.py / wiring.py 等模块的 logger 都使用新配置.
_init_logging()
