from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def utcnow() -> datetime:
    """Fecha-hora UTC sin zona (todas las marcas de tiempo se guardan en UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


def hashear(contrasena: str) -> str:
    return _pwd.hash(contrasena)


def verificar(contrasena: str, hash_: str) -> bool:
    return _pwd.verify(contrasena, hash_)


def crear_token(id_usuario: int, rol: str) -> tuple[str, int]:
    s = get_settings()
    expira = timedelta(minutes=s.jwt_expire_minutes)
    payload = {"sub": str(id_usuario), "rol": rol, "exp": datetime.now(UTC) + expira}
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm), int(
        expira.total_seconds()
    )


def decodificar_token(token: str) -> dict | None:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    except JWTError:
        return None
