# WP-0A-T01: 统一质量门命令（Linux/macOS/CI 使用）
# Windows 用户请使用 quality.ps1

.PHONY: quality lint typecheck test migration-check frontend-quality install-dev import-check check-import-boundaries

quality: lint typecheck check-import-boundaries test migration-check frontend-quality

install-dev:
	python -X utf8 -m pip install -r requirements-dev.txt
	cd admin-web && npm ci

lint:
	python -X utf8 -m ruff check src/ tests/

typecheck:
	python -X utf8 -m mypy

test:
	python -X utf8 -m pytest tests/ -q

migration-check:
	python -X utf8 scripts/check_migrations.py

frontend-quality:
	cd admin-web && npm run lint && npm run typecheck && npm run build

import-check:
	lint-imports

# WP-0A-T04: 检查 API 层不直接导入 src.shared.llm.providers
# import-linter forbidden 契约因传递依赖无法使用，改用 grep 检查直接 import
check-import-boundaries:
	@# 检查 src/memory/api/ 下所有 .py 文件不直接 from src.shared.llm.providers import
	@if grep -rnE "from[[:space:]]+src\.shared\.llm\.providers[[:space:]]+import" src/memory/api/ ; then \
		echo "ERROR: src/memory/api/ 不允许直接导入 src.shared.llm.providers，请通过 MemoryAnswerService 调用"; \
		exit 1; \
	else \
		echo "OK: src/memory/api/ 无直接 src.shared.llm.providers 导入"; \
	fi
