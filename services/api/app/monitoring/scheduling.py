from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models import CareerSource, SavedCompany

Clock = Callable[[], datetime]


class SourceScheduler:
    def __init__(self, session: AsyncSession, *, clock: Clock | None = None) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))

    async def claim_due(self, limit: int = 50, lease_minutes: int = 10) -> list[tuple[UUID, str]]:
        now = self.clock()
        statement = (
            select(CareerSource)
            .join(SavedCompany, SavedCompany.company_id == CareerSource.company_id)
            .where(
                SavedCompany.status == "active",
                CareerSource.status.in_(("pending_resolution", "supported", "degraded")),
                CareerSource.next_poll_at.is_not(None),
                CareerSource.next_poll_at <= now,
                or_(
                    CareerSource.lease_expires_at.is_(None),
                    CareerSource.lease_expires_at <= now,
                ),
            )
            .order_by(CareerSource.next_poll_at, CareerSource.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        sources = list((await self.session.scalars(statement)).all())
        claims: list[tuple[UUID, str]] = []
        for source in sources:
            owner = f"poll:{uuid4()}"
            source.lease_owner = owner
            source.lease_expires_at = now + timedelta(minutes=lease_minutes)
            claims.append((source.id, owner))
        await self.session.commit()
        return claims

    async def claim_one(self, source_id: UUID, lease_minutes: int = 10) -> tuple[UUID, str]:
        now = self.clock()
        source = await self.session.scalar(
            select(CareerSource).where(CareerSource.id == source_id).with_for_update()
        )
        if source is None:
            raise AppError("CAREER_SOURCE_NOT_FOUND", "Career source not found.", status_code=404)
        if source.status not in {"pending_resolution", "supported", "degraded"}:
            raise AppError(
                "CAREER_SOURCE_NOT_POLLABLE",
                "This career source is paused or unsupported.",
                status_code=409,
            )
        if source.lease_expires_at is not None and source.lease_expires_at > now:
            raise AppError(
                "CAREER_SOURCE_ALREADY_LEASED",
                "This career source already has a poll in progress.",
                status_code=409,
            )
        owner = f"manual:{uuid4()}"
        source.lease_owner = owner
        source.lease_expires_at = now + timedelta(minutes=lease_minutes)
        await self.session.commit()
        return source.id, owner

    async def pause(self, source_id: UUID) -> CareerSource:
        source = await self.session.get(CareerSource, source_id)
        if source is None:
            raise AppError("CAREER_SOURCE_NOT_FOUND", "Career source not found.", status_code=404)
        source.status = "paused"
        source.next_poll_at = None
        source.lease_owner = None
        source.lease_expires_at = None
        await self.session.commit()
        return source

    async def release_claim(self, source_id: UUID, lease_owner: str) -> None:
        source = await self.session.get(CareerSource, source_id)
        if source is not None and source.lease_owner == lease_owner:
            source.lease_owner = None
            source.lease_expires_at = None
            await self.session.commit()
