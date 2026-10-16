from functools import lru_cache
from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=RAIZ / ".env", extra="ignore")

    env: str = "development"
    app_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "voleyia"
    db_user: str = "voleyia"
    db_password: str = ""

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    login_max_intentos: int = 5
    login_bloqueo_minutos: int = 15

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model_principal: str = ""
    openrouter_model_respaldo: str = ""
    openrouter_timeout_s: float = 15
    openrouter_max_tokens_rutina: int = 4000
    openrouter_max_tokens_riesgo: int = 2000

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        from urllib.parse import quote_plus

        return (
            f"mysql+pymysql://{self.db_user}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def es_produccion(self) -> bool:
        return self.env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
