from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.models import AppSettings, OwnerProfile
from app.profiles.schemas import ProfileUpdate, SettingsUpdate


class ProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_profile(self) -> OwnerProfile:
        profile = await self.session.scalar(select(OwnerProfile).limit(1))
        if profile is None:
            raise AppError(
                "PROFILE_NOT_CONFIGURED",
                "Complete local onboarding before using JobScout.",
                status_code=404,
            )
        return profile

    async def upsert_profile(self, payload: ProfileUpdate) -> OwnerProfile:
        profile = await self.session.scalar(select(OwnerProfile).limit(1))
        now = datetime.now(UTC)
        if profile is None:
            profile = OwnerProfile(
                display_name=payload.display_name,
                email=payload.email,
                country_code=payload.country_code,
            )
            self.session.add(profile)
            settings = await self.session.scalar(select(AppSettings).limit(1))
            if settings is None:
                self.session.add(AppSettings())
        else:
            profile.display_name = payload.display_name
            profile.email = payload.email
            profile.country_code = payload.country_code
            profile.updated_at = now

        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def get_settings(self) -> AppSettings:
        settings = await self.session.scalar(select(AppSettings).limit(1))
        if settings is None:
            raise AppError(
                "SETTINGS_NOT_CONFIGURED",
                "Complete local onboarding before editing preferences.",
                status_code=404,
            )
        return settings

    async def update_settings(self, payload: SettingsUpdate) -> AppSettings:
        settings = await self.session.scalar(select(AppSettings).limit(1))
        if settings is None:
            raise AppError(
                "PROFILE_NOT_CONFIGURED",
                "Create the local profile before editing preferences.",
                status_code=409,
            )
        for field, value in payload.model_dump().items():
            setattr(settings, field, value)
        settings.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(settings)
        return settings
