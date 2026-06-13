"""Autenticación de APURISK — usuarios, contraseñas y sesiones firmadas.

Diseño (Fase 1 del login, preparado para roles en Fase 2):
  - Usuarios en una SQLite propia (output/apurisk_auth.db), SEPARADA del
    archive de inteligencia. Vive en el disco persistente de Render y NO es
    descargable (el montaje /output bloquea los archivos .db).
  - Contraseñas guardadas con PBKDF2-HMAC-SHA256 + salt aleatorio por usuario.
    Solo stdlib → cero dependencias nuevas (no arriesga el plan de 512MB).
  - Sesiones STATELESS: token firmado con HMAC-SHA256 sobre APURISK_SECRET_KEY,
    con expiración. Viaja en una cookie HttpOnly; el servidor no guarda estado.
  - Cada usuario tiene un campo 'rol' desde el día 1, para habilitar los
    niveles de acceso (Fase 2) sin tener que migrar la tabla después.

El primer usuario administrador se crea automáticamente al arrancar, leyendo
las variables de entorno APURISK_ADMIN_USER y APURISK_ADMIN_PASSWORD (ver
seed_admin_desde_env). Una vez creado, esas variables ya no son necesarias.
"""
from __future__ import annotations

import os
import time
import json
import hmac
import base64
import sqlite3
import hashlib
import secrets
from pathlib import Path
from typing import Optional

# 200k iteraciones: equilibrio razonable seguridad/CPU para un login humano.
_PBKDF2_ITERS = 200_000


# ----------------------------------------------------------------------
# Almacenamiento (SQLite propia para auth)
# ----------------------------------------------------------------------
def _db_path() -> Path:
    out = Path(os.getenv("OUTPUT_DIR", "output"))
    out.mkdir(parents=True, exist_ok=True)
    return out / "apurisk_auth.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_db_path()))
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    """Crea la tabla de usuarios si no existe. Idempotente."""
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                salt     TEXT NOT NULL,
                hash     TEXT NOT NULL,
                rol      TEXT NOT NULL DEFAULT 'admin',
                creado   TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )


# ----------------------------------------------------------------------
# Contraseñas (PBKDF2-HMAC-SHA256)
# ----------------------------------------------------------------------
def _hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                             salt, _PBKDF2_ITERS)
    return dk.hex()


def crear_usuario(username: str, password: str, rol: str = "admin") -> None:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("username y password son obligatorios")
    salt = secrets.token_bytes(16)
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT INTO usuarios (username, salt, hash, rol) VALUES (?,?,?,?)",
            (username, salt.hex(), _hash(password, salt), rol),
        )


def actualizar_password(username: str, password: str) -> bool:
    salt = secrets.token_bytes(16)
    with _conn() as c:
        cur = c.execute(
            "UPDATE usuarios SET salt=?, hash=? WHERE username=?",
            (salt.hex(), _hash(password, salt), (username or "").strip()),
        )
        return cur.rowcount > 0


def verificar_credenciales(username: str, password: str) -> Optional[dict]:
    """Devuelve {'username','rol'} si las credenciales son válidas, o None."""
    username = (username or "").strip()
    with _conn() as c:
        row = c.execute("SELECT * FROM usuarios WHERE username=?",
                        (username,)).fetchone()
    if not row:
        # Hash 'dummy' para igualar el tiempo de cómputo y no filtrar por
        # temporización si el usuario existe o no.
        _hash(password or "", b"0123456789abcdef")
        return None
    calculado = _hash(password or "", bytes.fromhex(row["salt"]))
    if hmac.compare_digest(row["hash"], calculado):
        return {"username": row["username"], "rol": row["rol"]}
    return None


def existe_algun_usuario() -> bool:
    try:
        with _conn() as c:
            return c.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        return False


def listar_usuarios() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT username, rol, creado FROM usuarios ORDER BY username")]


def seed_admin_desde_env() -> Optional[str]:
    """Crea el admin inicial desde APURISK_ADMIN_USER / APURISK_ADMIN_PASSWORD
    si ese usuario aún no existe. Devuelve el username creado, o None si no
    había variables o el usuario ya existía (en cuyo caso NO se pisa su clave).
    """
    user = os.environ.get("APURISK_ADMIN_USER", "").strip()
    pwd = os.environ.get("APURISK_ADMIN_PASSWORD", "")
    if not user or not pwd:
        return None
    init_db()
    with _conn() as c:
        existe = c.execute("SELECT 1 FROM usuarios WHERE username=?",
                           (user,)).fetchone()
    if existe:
        return None
    crear_usuario(user, pwd, rol="admin")
    return user


# ----------------------------------------------------------------------
# Sesiones firmadas (stateless, HMAC-SHA256)
# ----------------------------------------------------------------------
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def crear_token_sesion(username: str, rol: str, secret: str, ttl_seg: int) -> str:
    payload = {"u": username, "r": rol, "exp": int(time.time()) + int(ttl_seg)}
    p = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), p.encode("ascii"),
                   hashlib.sha256).digest()
    return p + "." + _b64e(sig)


def verificar_token_sesion(token: str, secret: str) -> Optional[dict]:
    """Devuelve {'username','rol'} si el token es válido y no expiró, o None."""
    if not token or "." not in token or not secret:
        return None
    p, sig = token.split(".", 1)
    esperado = hmac.new(secret.encode("utf-8"), p.encode("ascii"),
                        hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_b64d(sig), esperado):
            return None
        payload = json.loads(_b64d(p))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return {"username": payload.get("u"), "rol": payload.get("r")}
