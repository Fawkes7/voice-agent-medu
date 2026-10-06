from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


def _to_asyncpg_url(url: str) -> str:
    """Railway 等环境常提供 postgresql://，SQLAlchemy 异步引擎需 postgresql+asyncpg://。"""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    return url


# provider -> (OpenAI-compatible base URL, API key setting name, default model)
LLM_PROVIDERS: dict[str, tuple[str | None, str, str]] = {
    "openai": (None, "OPENAI_API_KEY", "gpt-4.1"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini-2.5-flash",
    ),
    "qwen": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
        "qwen-max",
    ),
}


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/spagent"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@localhost:5432/spagent"
    # 仅当连接串含 sslmode=verify-ca 或 verify-full 时生效：false 则仍走 TLS，但不校验证书链。
    # sslmode=require 默认不校验链（与 libpq 一致）；若仍遇 SSL 报错可再设 false。
    DATABASE_SSL_VERIFY: bool = True
    # LLM backend for all agents: openai | gemini | qwen
    LLM_PROVIDER: str = "qwen"
    # 留空则使用 LLM_PROVIDERS 里该 provider 的默认模型
    LLM_MODEL: str = ""
    # 留空则使用 provider 默认地址；Qwen 国际站可设为 https://dashscope-intl.aliyuncs.com/compatible-mode/v1
    LLM_BASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    # 旧配置名：provider=qwen 且未设 LLM_MODEL 时仍生效
    QWEN_MODEL: str = ""
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Voice I/O for multi-agent consultation (ASR = Scribe v2 Realtime, TTS = Flash v2.5)
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_STT_MODEL: str = "scribe_v2_realtime"
    ELEVENLABS_TTS_MODEL: str = "eleven_flash_v2_5"
    ELEVENLABS_STT_LANGUAGE: str = "zh"
    # Sarah / Rachel — multilingual voices that work with Flash v2.5
    ELEVENLABS_VOICE_ID_PATIENT: str = "EXAVITQu4vr4xnSDxMaL"
    ELEVENLABS_VOICE_ID_TUTOR: str = "21m00Tcm4TlvDq8ikWAM"

    model_config = {"env_file": ".env", "extra": "ignore"}

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_async_database_url(cls, v: object) -> object:
        if isinstance(v, str):
            return _to_asyncpg_url(v)
        return v

    @field_validator("LLM_PROVIDER", mode="before")
    @classmethod
    def normalize_llm_provider(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip().lower()
            if v not in LLM_PROVIDERS:
                raise ValueError(
                    f"LLM_PROVIDER must be one of {sorted(LLM_PROVIDERS)}, got {v!r}"
                )
        return v

    @property
    def llm_api_key(self) -> str:
        return getattr(self, LLM_PROVIDERS[self.LLM_PROVIDER][1]).strip()

    @property
    def llm_model(self) -> str:
        if self.LLM_MODEL.strip():
            return self.LLM_MODEL.strip()
        if self.LLM_PROVIDER == "qwen" and self.QWEN_MODEL.strip():
            return self.QWEN_MODEL.strip()
        return LLM_PROVIDERS[self.LLM_PROVIDER][2]

    @property
    def llm_base_url(self) -> str | None:
        return self.LLM_BASE_URL.strip() or LLM_PROVIDERS[self.LLM_PROVIDER][0]

    @model_validator(mode="after")
    def derive_sync_url_if_needed(self) -> "Settings":
        # 未单独配置 DATABASE_URL_SYNC 时，从异步 URL 推导 psycopg2 用的同步连接串
        default_sync = "postgresql://postgres:postgres@localhost:5432/spagent"
        if self.DATABASE_URL_SYNC == default_sync and self.DATABASE_URL.startswith(
            "postgresql+asyncpg://"
        ):
            self.DATABASE_URL_SYNC = "postgresql://" + self.DATABASE_URL.removeprefix(
                "postgresql+asyncpg://"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
