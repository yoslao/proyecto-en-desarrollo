from pydantic import BaseModel, ConfigDict, EmailStr


class LoginIn(BaseModel):
    correo: EmailStr
    contrasena: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expira_en_s: int
    rol: str
    nombre_completo: str


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    nombre_completo: str
    correo: str
    rol: str
