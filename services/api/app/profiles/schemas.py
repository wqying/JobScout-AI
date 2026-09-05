from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RoleType = Literal["internship", "new_grad", "entry_level"]
RemotePreference = Literal["any", "remote", "hybrid", "onsite"]


def _default_roles() -> list[RoleType]:
    return ["internship", "new_grad", "entry_level"]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class ProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    country_code: Literal["US"] = "US"

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        cleaned = _clean_optional(value)
        if cleaned is not None:
            local, separator, domain = cleaned.rpartition("@")
            if not separator or not local or "." not in domain or " " in cleaned:
                raise ValueError("Enter a valid email address")
        return cleaned


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    email: str | None
    country_code: str
    created_at: datetime
    updated_at: datetime


class SettingsUpdate(BaseModel):
    role_types: list[RoleType] = Field(
        default_factory=_default_roles,
        min_length=1,
        max_length=3,
    )
    keywords: list[str] = Field(default_factory=list, max_length=30)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=30)
    preferred_locations: list[str] = Field(default_factory=list, max_length=30)
    remote_preference: RemotePreference = "any"
    notify_current_jobs_on_save: bool = False
    email_notifications_enabled: bool = False
    notification_email: str | None = Field(default=None, max_length=254)

    @field_validator("role_types")
    @classmethod
    def unique_roles(cls, value: list[RoleType]) -> list[RoleType]:
        return list(dict.fromkeys(value))

    @field_validator("keywords", "excluded_keywords", "preferred_locations")
    @classmethod
    def clean_list(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(item.split()) for item in value if item.strip()]
        if any(len(item) > 80 for item in cleaned):
            raise ValueError("List entries must be 80 characters or fewer")
        return list(dict.fromkeys(cleaned))

    @field_validator("notification_email")
    @classmethod
    def validate_notification_email(cls, value: str | None) -> str | None:
        return ProfileUpdate.validate_email(value)

    @model_validator(mode="after")
    def require_notification_email(self) -> "SettingsUpdate":
        if self.email_notifications_enabled and not self.notification_email:
            raise ValueError("A notification email is required when email alerts are enabled")
        return self


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role_types: list[str]
    keywords: list[str]
    excluded_keywords: list[str]
    preferred_locations: list[str]
    remote_preference: str
    notify_current_jobs_on_save: bool
    email_notifications_enabled: bool
    notification_email: str | None
    created_at: datetime
    updated_at: datetime
