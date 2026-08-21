"""Application settings. Rejects non-test Razorpay credentials."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    database_url: str = Field(
        default=f"sqlite:///{DATA_DIR / 'checkout_agent.db'}",
        alias="DATABASE_URL",
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )

    demo_user_id: str = Field(default="demo_user_01", alias="DEMO_USER_ID")
    use_mock_catalog: bool = Field(default=True, alias="USE_MOCK_CATALOG")

    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    razorpay_key_id: str = Field(default="", alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str = Field(default="", alias="RAZORPAY_KEY_SECRET")
    razorpay_mode: str = Field(default="test", alias="RAZORPAY_MODE")

    @field_validator("razorpay_mode")
    @classmethod
    def mode_must_be_test(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized != "test":
            raise ValueError("RAZORPAY_MODE must be 'test' for this project")
        return normalized

    @model_validator(mode="after")
    def reject_live_razorpay_keys(self) -> "Settings":
        key_id = (self.razorpay_key_id or "").strip()
        if not key_id:
            return self
        if key_id.startswith("rzp_live_"):
            raise ValueError(
                "Live Razorpay keys are forbidden. Use rzp_test_… keys only."
            )
        if not key_id.startswith("rzp_test_"):
            raise ValueError(
                "RAZORPAY_KEY_ID must start with 'rzp_test_' (test mode only)."
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlite_path(self) -> Path:
        url = self.database_url
        if url.startswith("sqlite:///"):
            raw = url.removeprefix("sqlite:///")
            path = Path(raw)
            if not path.is_absolute():
                path = (ROOT_DIR / path).resolve()
            return path
        return DATA_DIR / "checkout_agent.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
