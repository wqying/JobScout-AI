from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import database_session
from app.profiles.schemas import (
    ProfileResponse,
    ProfileUpdate,
    SettingsResponse,
    SettingsUpdate,
)
from app.profiles.service import ProfileService

router = APIRouter(tags=["profile"])
Session = Annotated[AsyncSession, Depends(database_session)]


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(session: Session) -> ProfileResponse:
    profile = await ProfileService(session).get_profile()
    return ProfileResponse.model_validate(profile)


@router.put("/profile", response_model=ProfileResponse)
async def put_profile(payload: ProfileUpdate, session: Session) -> ProfileResponse:
    profile = await ProfileService(session).upsert_profile(payload)
    return ProfileResponse.model_validate(profile)


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(session: Session) -> SettingsResponse:
    settings = await ProfileService(session).get_settings()
    return SettingsResponse.model_validate(settings)


@router.put("/settings", response_model=SettingsResponse)
async def put_settings(payload: SettingsUpdate, session: Session) -> SettingsResponse:
    settings = await ProfileService(session).update_settings(payload)
    return SettingsResponse.model_validate(settings)
