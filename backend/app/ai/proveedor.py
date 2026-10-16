"""Acceso único al cliente de OpenRouter y a los modelos configurados."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.openrouter_client import OpenRouterClient
from app.core.config import get_settings
from app.models import ConfiguracionSistema

_cliente: OpenRouterClient | None = None


def get_cliente() -> OpenRouterClient:
    """Cliente compartido. Las pruebas lo sustituyen con `fijar_cliente` (OpenRouter mockeado)."""
    global _cliente
    if _cliente is None:
        s = get_settings()
        _cliente = OpenRouterClient(
            s.openrouter_api_key,
            base_url=s.openrouter_base_url,
            app_url=s.app_url,
            timeout_s=s.openrouter_timeout_s,
        )
    return _cliente


def fijar_cliente(cliente: OpenRouterClient | None) -> None:
    global _cliente
    _cliente = cliente


def modelos_configurados(db: Session) -> list[str]:
    """[principal, respaldo]. El administrador puede cambiarlos (configuracion_sistema);
    si el valor está vacío se usa la variable de entorno. Nunca hay modelos en el código."""
    s = get_settings()
    cfg = {
        c.clave: c.valor.strip()
        for c in db.scalars(
            select(ConfiguracionSistema).where(
                ConfiguracionSistema.clave.in_(["IA_MODELO_PRINCIPAL", "IA_MODELO_RESPALDO"])
            )
        )
    }
    principal = cfg.get("IA_MODELO_PRINCIPAL") or s.openrouter_model_principal
    respaldo = cfg.get("IA_MODELO_RESPALDO") or s.openrouter_model_respaldo
    return [m for m in (principal, respaldo) if m]


def max_tokens(tarea: str) -> int:
    s = get_settings()
    return s.openrouter_max_tokens_rutina if tarea == "rutina" else s.openrouter_max_tokens_riesgo
