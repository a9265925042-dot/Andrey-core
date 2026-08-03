"""Pydantic Settings — все секреты и тюнинг из .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Tokens
    wb_api_token: SecretStr | None = Field(
        None, description="WB Seller API Personal JWT (180 дней)"
    )
    mpstats_api_token: SecretStr | None = Field(
        None, description="MPStats Analytics v1 token"
    )

    # Cabinet
    wb_cabinet_cookies_path: str = "./data/cabinet_cookies.pkl"
    chrome_cookie_file: str | None = Field(
        None, description="Явный путь к Chrome Cookies файлу (не-Default профиль)"
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/wb-pool.db"

    # Brand markers (comma-separated, lowercase)
    my_brand_markers: str = ""

    @property
    def brand_markers_set(self) -> set[str]:
        return {m.strip().lower() for m in self.my_brand_markers.split(",") if m.strip()}

    # MPStats
    mpstats_parent_category: str = "Красота"

    # Logging
    log_level: str = "INFO"
    log_json: bool = False
    log_file_path: str | None = None

    # Discovery / cabinet tuning defaults (CLI flags override)
    discovery_serp_top: int = 100
    discovery_feedbacks_threshold: int = 300
    discovery_n_my_cards: int = 20
    cabinet_period_days: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
