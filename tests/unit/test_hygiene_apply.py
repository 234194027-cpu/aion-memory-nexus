import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.cognition.api.memory_governance import apply_hygiene_suggestions
from src.cognition.schemas.governance import HygieneApplyRequest, HygieneSuggestion
from src.execution.models.user import User
from src.memory.models.memory_type import MemoryType
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.raw_event import SensitivityLevel, VisibilityScope
from src.shared.db.database import async_session, init_db
from src.shared.ids.id_generator import generate_memory_id
from src.shared.security.auth import get_password_hash


def test_hygiene_apply_requires_approval_and_can_merge():
    async def run():
        await init_db()
        suffix = uuid4().hex
        user = User(
            id=f"hygiene-user-{suffix}",
            email=f"hygiene-{suffix}@example.com",
            hashed_password=get_password_hash("test123456"),
        )
        primary_id = generate_memory_id()
        secondary_id = generate_memory_id()

        async with async_session() as db:
            db.add(user)
            for memory_id, title in [
                (primary_id, "primary duplicate"),
                (secondary_id, "secondary duplicate"),
            ]:
                db.add(
                    CommittedMemory(
                        id=memory_id,
                        user_id=user.id,
                        memory_type=MemoryType.FACT,
                        title=title,
                        body="same duplicated body",
                        confidence=0.9,
                        importance=0.8,
                        sensitivity=SensitivityLevel.NORMAL,
                        visibility_scope=VisibilityScope.PROJECT,
                        status=CommittedStatus.ACTIVE,
                        valid_from=datetime.now(timezone.utc),
                    )
                )
            await db.commit()

            suggestion = HygieneSuggestion(
                type="merge_duplicate_memories",
                priority="high",
                memory_ids=[primary_id, secondary_id],
                reason="duplicate_pair_above_threshold",
                auto_apply=True,
            )

            with pytest.raises(HTTPException) as denied:
                await apply_hygiene_suggestions(
                    request=HygieneApplyRequest(suggestions=[suggestion]),
                    db=db,
                    user=user,
                )
            assert denied.value.status_code == 400

            result = await apply_hygiene_suggestions(
                request=HygieneApplyRequest(suggestions=[suggestion], approved=True),
                db=db,
                user=user,
            )
            assert result.applied_count == 1
            assert result.failed == []

            secondary = (
                await db.execute(
                    select(CommittedMemory).where(CommittedMemory.id == secondary_id)
                )
            ).scalar_one()
            assert secondary.status == CommittedStatus.SUPERSEDED

    asyncio.run(run())


def test_hygiene_apply_reports_unsupported_without_writing():
    async def run():
        await init_db()
        suffix = uuid4().hex
        user = User(
            id=f"hygiene-user-{suffix}",
            email=f"hygiene-{suffix}@example.com",
            hashed_password=get_password_hash("test123456"),
        )
        async with async_session() as db:
            db.add(user)
            await db.commit()

            result = await apply_hygiene_suggestions(
                request=HygieneApplyRequest(
                    approved=True,
                    suggestions=[
                        HygieneSuggestion(
                            type="review_low_confidence_memory",
                            priority="low",
                            memory_ids=["missing"],
                            reason="confidence_below_threshold",
                        )
                    ],
                ),
                db=db,
                user=user,
            )

        assert result.applied_count == 0
        assert result.failed == []
        assert result.unsupported[0]["type"] == "review_low_confidence_memory"

    asyncio.run(run())
