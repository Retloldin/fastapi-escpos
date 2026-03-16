from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_env_int(value: Union[int, str]) -> int:
    if isinstance(value, str):
        raw_value = value.strip()
        if not raw_value:
            raise ValueError("Value cannot be empty.")
        return int(raw_value, 0)
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    printer_vendor_id: int = Field(..., validation_alias="PRINTER_VENDOR_ID")
    printer_product_id: int = Field(..., validation_alias="PRINTER_PRODUCT_ID")
    printer_in_ep: int = Field(0x82, validation_alias="PRINTER_IN_EP")
    printer_out_ep: int = Field(0x01, validation_alias="PRINTER_OUT_EP")
    printer_timeout: int = Field(0, validation_alias="PRINTER_TIMEOUT")
    printer_profile: Optional[str] = Field(None, validation_alias="PRINTER_PROFILE")
    printer_cut_mode: Literal["FULL", "PART"] = Field(
        "FULL",
        validation_alias="PRINTER_CUT_MODE",
    )
    app_debug: bool = Field(False, validation_alias="APP_DEBUG")
    app_cors_allowed_origins: list[str] = Field(
        default_factory=list,
        validation_alias="APP_CORS_ALLOWED_ORIGINS",
    )
    api_bearer_token: str = Field(..., validation_alias="API_BEARER_TOKEN")

    @field_validator(
        "printer_vendor_id",
        "printer_product_id",
        "printer_in_ep",
        "printer_out_ep",
        "printer_timeout",
        mode="before",
    )
    @classmethod
    def parse_int_fields(cls, value: Union[int, str]) -> int:
        return _parse_env_int(value)

    @field_validator("printer_profile", mode="before")
    @classmethod
    def empty_profile_to_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("printer_cut_mode", mode="before")
    @classmethod
    def normalize_cut_mode(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("app_cors_allowed_origins", mode="before")
    @classmethod
    def normalize_cors_origins(cls, value: Union[str, list[str], None]) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return [origin.strip() for origin in value if origin.strip()]

    @field_validator("api_bearer_token", mode="before")
    @classmethod
    def normalize_bearer_token(cls, value: str) -> str:
        token = value.strip()
        if not token:
            raise ValueError("API_BEARER_TOKEN cannot be empty.")
        return token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
