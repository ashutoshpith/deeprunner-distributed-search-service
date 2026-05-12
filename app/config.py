from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    redis_url: str
    rate_limit_per_minute: int = 60
    cache_ttl_seconds: int = 60
    app_env: str = "development"

    @property
    def doc_cache_ttl_seconds(self) -> int:
        return 300  # 5 minutes for individual document cache


settings = Settings()
