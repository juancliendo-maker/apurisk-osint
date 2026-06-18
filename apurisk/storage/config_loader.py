"""APURISK · storage/config_loader — Capa de acceso a configuración editable (Fase B).

Abstrae la lectura/escritura de las tablas config_* para que:
  1. El pipeline lea configuración desde BD con fallback a los valores hardcodeados.
  2. El panel admin escriba con auditoría (config_fuentes_log).
  3. La futura migración SQLite→Postgres sea transparente: toda la lógica
     SQLite-específica (INSERT OR IGNORE, etc.) vive SOLO aquí.

Patrón de degradación segura: si la BD está vacía o falla, las funciones de
lectura devuelven None / {} y el caller usa su fallback hardcodeado. Editar
configuración nunca puede dejar el pipeline sin datos.
"""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Conexión con reintentos (mitiga "database is locked" durante ciclos del scheduler)
# ──────────────────────────────────────────────────────────────────────────────

class LockTimeoutError(Exception):
    """La BD siguió bloqueada tras agotar los reintentos. El caller debe informar
    al usuario que su escritura NO se guardó."""


def _conn(db_path: str, timeout: float = 5.0) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, timeout=timeout)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _ejecutar_con_reintentos(db_path: str, fn, intentos: int = 3, backoff: float = 0.5):
    """Ejecuta fn(conn) dentro de una transacción, reintentando ante locks de SQLite.

    Reintenta `intentos` veces con backoff exponencial (0.5s, 1s, 2s).
    Si todos fallan por lock → LockTimeoutError (el caller informa al usuario).
    """
    ultimo_error = None
    for intento in range(intentos):
        try:
            with _conn(db_path) as c:
                resultado = fn(c)
                c.commit()
                return resultado
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                ultimo_error = e
                if intento < intentos - 1:
                    time.sleep(backoff * (2 ** intento))
                    continue
            raise
    raise LockTimeoutError(
        "La base de datos está ocupada (ciclo automático en curso). "
        "La operación NO se guardó."
    ) from ultimo_error


# ──────────────────────────────────────────────────────────────────────────────
# Seed: poblar config_fuentes desde config.yaml en el primer arranque
# ──────────────────────────────────────────────────────────────────────────────

def seed_fuentes_si_vacio(db_path: str, feeds_yaml: list, calidad_fn=None) -> int:
    """Si config_fuentes está vacía, la puebla desde la lista de feeds de config.yaml.

    calidad_fn: función opcional nombre→calidad (la de risk_matrix._calidad_fuente)
    para calcular la calidad inicial de cada fuente. Si no se pasa, usa 1.0.

    Devuelve el número de filas insertadas (0 si ya había datos).
    """
    if not feeds_yaml:
        return 0
    try:
        with _conn(db_path) as c:
            ya = c.execute("SELECT COUNT(*) AS n FROM config_fuentes").fetchone()
            if ya and ya["n"] > 0:
                return 0
            insertadas = 0
            for feed in feeds_yaml:
                fid    = feed.get("id") or ""
                nombre = feed.get("nombre") or fid
                url    = feed.get("url") or ""
                cat    = feed.get("categoria") or "medios"
                tipo   = "rss"
                calidad = 1.0
                if calidad_fn:
                    try:
                        calidad = float(calidad_fn(nombre))
                    except Exception:
                        calidad = 1.0
                c.execute(
                    "INSERT INTO config_fuentes "
                    "(nombre, url_feed, tipo, pais, calidad, activo, categoria, notas) "
                    "VALUES (?, ?, ?, 'PE', ?, 1, ?, ?)",
                    (nombre, url, tipo, calidad, cat,
                     f"Importada de config.yaml (id={fid})"),
                )
                insertadas += 1
            c.commit()
            return insertadas
    except Exception as e:
        print(f"[config_loader] seed_fuentes_si_vacio falló (no crítico): {e}")
        return 0


# ──────────────────────────────────────────────────────────────────────────────
# Lectura para el pipeline
# ──────────────────────────────────────────────────────────────────────────────

def cargar_feeds_efectivos(db_path: str) -> Optional[list]:
    """Devuelve los feeds ACTIVOS desde config_fuentes como lista de feed_cfg
    {id, nombre, url, categoria} — el mismo formato que consume RSSMediaCollector.

    Devuelve None si la tabla está vacía o falla → el caller usa config.yaml.
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT id, nombre, url_feed, categoria FROM config_fuentes "
                "WHERE activo = 1 AND url_feed IS NOT NULL AND url_feed != '' "
                "ORDER BY id"
            ).fetchall()
        if not rows:
            return None
        return [
            {"id": f"cf_{r['id']}", "nombre": r["nombre"],
             "url": r["url_feed"], "categoria": r["categoria"] or "medios"}
            for r in rows
        ]
    except Exception as e:
        print(f"[config_loader] cargar_feeds_efectivos falló → fallback yaml: {e}")
        return None


# Cache de calidad por nombre de fuente (refrescado por ciclo del pipeline).
_calidad_cache: dict[str, float] = {}
_calidad_cache_ts: float = 0.0
_CALIDAD_TTL = 300.0  # 5 min


def cargar_calidad_override(db_path: str, forzar: bool = False) -> dict:
    """Devuelve {nombre_lower: calidad} desde config_fuentes para que risk_matrix
    consulte calidades editadas. Cacheado 5 min. {} si vacío/falla (fallback al dict)."""
    global _calidad_cache, _calidad_cache_ts
    ahora = time.time()
    if not forzar and _calidad_cache and (ahora - _calidad_cache_ts) < _CALIDAD_TTL:
        return _calidad_cache
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT nombre, calidad FROM config_fuentes WHERE activo = 1"
            ).fetchall()
        _calidad_cache = {r["nombre"].strip().lower(): float(r["calidad"]) for r in rows}
        _calidad_cache_ts = ahora
        return _calidad_cache
    except Exception:
        return _calidad_cache or {}


def invalidar_calidad_cache() -> None:
    """Fuerza recarga del cache de calidad tras una edición."""
    global _calidad_cache_ts
    _calidad_cache_ts = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Lectura para el panel admin
# ──────────────────────────────────────────────────────────────────────────────

def listar_fuentes(db_path: str) -> list:
    """Todas las fuentes (activas e inactivas) para la UI editable."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT id, nombre, url_feed, tipo, pais, calidad, activo, "
                "categoria, notas, actualizado_en FROM config_fuentes "
                "ORDER BY categoria, nombre"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_fuentes falló: {e}")
        return []


def listar_log_fuentes(db_path: str, limite: int = 100) -> list:
    """Historial de cambios de fuentes (auditoría)."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT l.id, l.fuente_id, l.campo, l.valor_anterior, l.valor_nuevo, "
                "l.usuario, l.motivo, l.cambiado_en, f.nombre AS fuente_nombre "
                "FROM config_fuentes_log l "
                "LEFT JOIN config_fuentes f ON l.fuente_id = f.id "
                "ORDER BY l.id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_log_fuentes falló: {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Escritura con auditoría (panel admin)
# ──────────────────────────────────────────────────────────────────────────────

_CAMPOS_EDITABLES = {"calidad", "activo", "notas"}


def actualizar_fuente(db_path: str, fuente_id: int, campo: str, valor_nuevo,
                      usuario: str, motivo: str = "") -> dict:
    """Actualiza un campo de una fuente y registra el cambio en config_fuentes_log,
    todo en la misma transacción (auditoría garantizada).

    Lanza ValueError si el campo no es editable, LockTimeoutError si la BD está
    ocupada tras los reintentos. Devuelve {ok, valor_anterior, valor_nuevo}.
    """
    if campo not in _CAMPOS_EDITABLES:
        raise ValueError(f"Campo no editable: {campo}")

    def _op(c: sqlite3.Connection) -> dict:
        fila = c.execute(
            f"SELECT {campo} AS v FROM config_fuentes WHERE id = ?", (fuente_id,)
        ).fetchone()
        if fila is None:
            raise ValueError(f"Fuente {fuente_id} no existe")
        valor_anterior = fila["v"]

        # Normalización por tipo de campo
        if campo == "calidad":
            vn = round(float(valor_nuevo), 2)
            if not (0.1 <= vn <= 2.0):
                raise ValueError("calidad debe estar entre 0.1 y 2.0")
        elif campo == "activo":
            vn = 1 if str(valor_nuevo) in ("1", "true", "True", "on") else 0
        else:  # notas
            vn = str(valor_nuevo)[:500]

        c.execute(
            f"UPDATE config_fuentes SET {campo} = ?, "
            "actualizado_en = datetime('now') WHERE id = ?",
            (vn, fuente_id),
        )
        c.execute(
            "INSERT INTO config_fuentes_log "
            "(fuente_id, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fuente_id, campo, str(valor_anterior), str(vn), usuario, motivo or None),
        )
        return {"ok": True, "valor_anterior": valor_anterior, "valor_nuevo": vn}

    resultado = _ejecutar_con_reintentos(db_path, _op)
    invalidar_calidad_cache()  # la próxima lectura del pipeline verá el cambio
    return resultado
