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

    # Merchant e-commerce API (Pointer 9). Empty base URL → mock JSON.
    store_api_base_url: str = Field(default="", alias="STORE_API_BASE_URL")
    store_api_key: str = Field(default="", alias="STORE_API_KEY")
    store_api_timeout_seconds: float = Field(default=8.0, alias="STORE_API_TIMEOUT_SECONDS")
    store_api_max_retries: int = Field(default=1, alias="STORE_API_MAX_RETRIES")
    store_fallback_to_mock: bool = Field(default=True, alias="STORE_FALLBACK_TO_MOCK")
    store_products_path: str = Field(default="/products", alias="STORE_PRODUCTS_PATH")
    store_product_path: str = Field(
        default="/products/{id}",
        alias="STORE_PRODUCT_PATH",
    )
    store_usual_order_path: str = Field(
        default="/customers/{user_id}/orders/latest",
        alias="STORE_USUAL_ORDER_PATH",
    )

    # Groq (OpenAI-compatible) for Pointer 8 agent + tool calling
    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")  # legacy alias
    llm_model: str = Field(
        default="openai/gpt-oss-120b",
        alias="LLM_MODEL",
    )
    llm_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        alias="LLM_BASE_URL",
    )

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
    def groq_model_sanity(self) -> "Settings":
        if self.llm_provider.lower() == "groq" and (
            "gpt-4" in self.llm_model.lower()
            or self.llm_model.startswith("o1")
            or self.llm_model == "llama-3.3-70b-versatile"
            or self.llm_model == "gpt-4o-mini"
        ):
            object.__setattr__(self, "llm_model", "openai/gpt-oss-120b")
        return self

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
    def effective_llm_api_key(self) -> str:
        """Groq key preferred; LLM_API_KEY kept for backward compatibility."""
        return (self.groq_api_key or self.llm_api_key or "").strip()

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
