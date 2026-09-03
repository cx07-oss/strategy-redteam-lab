"""Typed, environment-based settings for the optional backend application."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration; credentials are supplied only by environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://strategy_redteam:strategy_redteam@localhost:5432/strategy_redteam"
    )
    app_env: str = "development"
    log_level: str = "INFO"
    dataset_root: Path = Field(default=Path("tests/fixtures/offline-cache/manifests"))
    cors_origins: str = "http://localhost:3000"
