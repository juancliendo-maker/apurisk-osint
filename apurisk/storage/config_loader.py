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
    """Devuelve {nombre_lower: calidad_efectiva} donde calidad_efectiva = calidad * peso_analista.

    El pipeline usa este valor como multiplicador único. Si peso_analista no existe
    (instancias antiguas), COALESCE lo devuelve como 1.0. Cacheado 5 min."""
    global _calidad_cache, _calidad_cache_ts
    ahora = time.time()
    if not forzar and _calidad_cache and (ahora - _calidad_cache_ts) < _CALIDAD_TTL:
        return _calidad_cache
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT nombre, calidad, COALESCE(peso_analista, 1.0) AS peso_analista "
                "FROM config_fuentes WHERE activo = 1"
            ).fetchall()
        _calidad_cache = {
            r["nombre"].strip().lower(): round(float(r["calidad"]) * float(r["peso_analista"]), 3)
            for r in rows
        }
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
                "SELECT id, nombre, url_feed, tipo, pais, calidad, "
                "COALESCE(peso_analista, 1.0) AS peso_analista, "
                "activo, categoria, notas, actualizado_en FROM config_fuentes "
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

_CAMPOS_EDITABLES = {"calidad", "peso_analista", "activo", "notas"}


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
        col = campo if campo != "peso_analista" else "COALESCE(peso_analista, 1.0)"
        fila = c.execute(
            f"SELECT {col} AS v FROM config_fuentes WHERE id = ?", (fuente_id,)
        ).fetchone()
        if fila is None:
            raise ValueError(f"Fuente {fuente_id} no existe")
        valor_anterior = fila["v"]

        # Normalización por tipo de campo
        if campo in ("calidad", "peso_analista"):
            vn = round(float(valor_nuevo), 2)
            if not (0.1 <= vn <= 2.0):
                raise ValueError(f"{campo} debe estar entre 0.1 y 2.0")
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


# ──────────────────────────────────────────────────────────────────────────────
# Factores: seed, lectura y escritura de pesos (Fase B Item 2)
# ──────────────────────────────────────────────────────────────────────────────

def seed_factores_si_vacio(db_path: str, factores_hardcode: list,
                           prob_base_dict: dict) -> int:
    """Si config_factores está vacía, la puebla desde FACTORES + PROB_BASE_FACTOR.

    factores_hardcode: lista de dicts con id, nombre, categoria, impacto_base.
    prob_base_dict: dict factor_id → prob_base.
    Devuelve número de filas insertadas (0 si ya había datos).
    """
    if not factores_hardcode:
        return 0
    try:
        with _conn(db_path) as c:
            ya = c.execute("SELECT COUNT(*) AS n FROM config_factores").fetchone()
            if ya and ya["n"] > 0:
                return 0
            insertadas = 0
            for i, f in enumerate(factores_hardcode):
                fid     = f.get("id") or ""
                nombre  = f.get("nombre") or fid
                cat     = f.get("categoria") or "General"
                impacto = int(f.get("impacto_base", 60))
                prob    = int(prob_base_dict.get(fid, 25))
                c.execute(
                    "INSERT INTO config_factores "
                    "(factor_id, nombre, categoria, pais, impacto_base, prob_base, activo, orden) "
                    "VALUES (?, ?, ?, 'PE', ?, ?, 1, ?)",
                    (fid, nombre, cat, impacto, prob, i),
                )
                insertadas += 1
            c.commit()
            return insertadas
    except Exception as e:
        print(f"[config_loader] seed_factores_si_vacio falló (no crítico): {e}")
        return 0


def listar_factores_config(db_path: str) -> list:
    """Todos los factores de config_factores para la UI editable."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT id, factor_id, nombre, categoria, impacto_base, prob_base, activo, orden "
                "FROM config_factores ORDER BY orden, categoria, nombre"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_factores_config falló: {e}")
        return []


# Cache de pesos por factor_id (refrescado tras edición o cada 5 min)
_pesos_cache: dict[str, dict] = {}
_pesos_cache_ts: float = 0.0
_PESOS_TTL = 300.0  # 5 min


def cargar_pesos_override(db_path: str, forzar: bool = False) -> dict:
    """Devuelve {factor_id: {impacto_base, prob_base}} desde config_factores.
    Cacheado 5 min. {} si vacío/falla (pipeline usa hardcodeados)."""
    global _pesos_cache, _pesos_cache_ts
    ahora = time.time()
    if not forzar and _pesos_cache and (ahora - _pesos_cache_ts) < _PESOS_TTL:
        return _pesos_cache
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT factor_id, impacto_base, prob_base FROM config_factores WHERE activo = 1"
            ).fetchall()
        _pesos_cache = {
            r["factor_id"]: {"impacto_base": r["impacto_base"], "prob_base": r["prob_base"]}
            for r in rows
        }
        _pesos_cache_ts = ahora
        return _pesos_cache
    except Exception:
        return _pesos_cache or {}


def invalidar_pesos_cache() -> None:
    """Fuerza recarga del cache de pesos tras una edición."""
    global _pesos_cache_ts
    _pesos_cache_ts = 0.0


_CAMPOS_FACTORES_EDITABLES = {"impacto_base", "prob_base"}


def actualizar_factor_peso(db_path: str, factor_id: str, campo: str,
                           valor_nuevo, usuario: str, motivo: str = "") -> dict:
    """Actualiza impacto_base o prob_base de un factor.

    Validaciones: impacto_base ∈ [1, 100], prob_base ∈ [1, 95].
    Lanza ValueError o LockTimeoutError. Devuelve {ok, valor_anterior, valor_nuevo}.
    """
    if campo not in _CAMPOS_FACTORES_EDITABLES:
        raise ValueError(f"Campo no editable: {campo}")

    def _op(c: sqlite3.Connection) -> dict:
        fila = c.execute(
            f"SELECT {campo} AS v FROM config_factores WHERE factor_id = ?", (factor_id,)
        ).fetchone()
        if fila is None:
            raise ValueError(f"Factor '{factor_id}' no existe en config_factores")
        valor_anterior = fila["v"]

        vn = int(round(float(valor_nuevo)))
        if campo == "impacto_base":
            if not (1 <= vn <= 100):
                raise ValueError("impacto_base debe estar entre 1 y 100")
        else:  # prob_base
            if not (1 <= vn <= 95):
                raise ValueError("prob_base debe estar entre 1 y 95")

        c.execute(
            f"UPDATE config_factores SET {campo} = ? WHERE factor_id = ?",
            (vn, factor_id),
        )
        return {"ok": True, "valor_anterior": valor_anterior, "valor_nuevo": vn}

    resultado = _ejecutar_con_reintentos(db_path, _op)
    invalidar_pesos_cache()
    return resultado


# ──────────────────────────────────────────────────────────────────────────────
# Keywords por factor: seed, lectura y escritura (Fase B Item 3)
# ──────────────────────────────────────────────────────────────────────────────

_TIPOS_KEYWORD = {"fuerte", "contexto", "negacion"}


def seed_keywords_si_vacio(db_path: str, factores_hardcode: list) -> int:
    """Si config_keywords está vacía, la puebla desde FACTORES hardcodeados.

    factores_hardcode: lista de dicts con id, keywords_fuertes, keywords_contexto,
    keywords_negacion.
    Devuelve número de filas insertadas (0 si ya había datos).
    """
    if not factores_hardcode:
        return 0
    try:
        with _conn(db_path) as c:
            ya = c.execute("SELECT COUNT(*) AS n FROM config_keywords").fetchone()
            if ya and ya["n"] > 0:
                return 0
            insertadas = 0
            for f in factores_hardcode:
                fid = f.get("id") or ""
                for tipo, key in (
                    ("fuerte", "keywords_fuertes"),
                    ("contexto", "keywords_contexto"),
                    ("negacion", "keywords_negacion"),
                ):
                    for kw in (f.get(key) or []):
                        kw = kw.strip()
                        if not kw:
                            continue
                        c.execute(
                            "INSERT INTO config_keywords "
                            "(factor_id, tipo, keyword, pais, activo) "
                            "VALUES (?, ?, ?, 'PE', 1)",
                            (fid, tipo, kw),
                        )
                        insertadas += 1
            c.commit()
            return insertadas
    except Exception as e:
        print(f"[config_loader] seed_keywords_si_vacio falló (no crítico): {e}")
        return 0


def listar_keywords_factor(db_path: str, factor_id: str) -> list:
    """Keywords (activas e inactivas) de un factor para la UI editable."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT id, tipo, keyword, activo FROM config_keywords "
                "WHERE factor_id = ? ORDER BY tipo, id",
                (factor_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_keywords_factor falló: {e}")
        return []


# Cache de keywords por factor_id (refrescado tras edición o cada 5 min)
_kw_cache: dict[str, dict] = {}  # {factor_id: {fuerte:[...], contexto:[...], negacion:[...]}}
_kw_cache_ts: float = 0.0
_KW_TTL = 300.0  # 5 min


def cargar_keywords_override(db_path: str, forzar: bool = False) -> dict:
    """Devuelve {factor_id: {fuerte, contexto, negacion}} con keywords ACTIVAS.
    Cacheado 5 min. {} si vacío/falla → pipeline usa hardcodeados."""
    global _kw_cache, _kw_cache_ts
    ahora = time.time()
    if not forzar and _kw_cache and (ahora - _kw_cache_ts) < _KW_TTL:
        return _kw_cache
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT factor_id, tipo, keyword FROM config_keywords WHERE activo = 1"
            ).fetchall()
        result: dict = {}
        for r in rows:
            fid = r["factor_id"]
            if fid not in result:
                result[fid] = {"fuerte": [], "contexto": [], "negacion": []}
            if r["tipo"] in _TIPOS_KEYWORD:
                result[fid][r["tipo"]].append(r["keyword"])
        _kw_cache = result
        _kw_cache_ts = ahora
        return _kw_cache
    except Exception:
        return _kw_cache or {}


def invalidar_keywords_cache() -> None:
    """Fuerza recarga del cache de keywords tras una edición."""
    global _kw_cache_ts
    _kw_cache_ts = 0.0


def agregar_keyword(db_path: str, factor_id: str, tipo: str,
                    keyword: str, usuario: str) -> dict:
    """Añade una nueva keyword a config_keywords.

    Lanza ValueError si tipo inválido, keyword vacía o duplicada.
    Lanza LockTimeoutError si BD ocupada.
    """
    if tipo not in _TIPOS_KEYWORD:
        raise ValueError(f"tipo debe ser fuerte, contexto o negacion (recibido: '{tipo}')")
    kw = keyword.strip().lower()
    if not kw:
        raise ValueError("La keyword no puede estar vacía")
    if len(kw) > 200:
        raise ValueError("Keyword demasiado larga (máx 200 caracteres)")

    def _op(c: sqlite3.Connection) -> dict:
        existe = c.execute(
            "SELECT id FROM config_keywords WHERE factor_id=? AND tipo=? AND keyword=?",
            (factor_id, tipo, kw),
        ).fetchone()
        if existe:
            # Reactivar si estaba inactiva
            c.execute(
                "UPDATE config_keywords SET activo=1 WHERE factor_id=? AND tipo=? AND keyword=?",
                (factor_id, tipo, kw),
            )
            return {"ok": True, "accion": "reactivada", "keyword": kw}
        c.execute(
            "INSERT INTO config_keywords (factor_id, tipo, keyword, pais, activo) "
            "VALUES (?, ?, ?, 'PE', 1)",
            (factor_id, tipo, kw),
        )
        return {"ok": True, "accion": "creada", "keyword": kw}

    resultado = _ejecutar_con_reintentos(db_path, _op)
    invalidar_keywords_cache()
    return resultado


def desactivar_keyword(db_path: str, kw_id: int, usuario: str) -> dict:
    """Desactiva (soft-delete) una keyword por su id primario.

    No borra el registro para mantener trazabilidad.
    Lanza ValueError si no existe. Lanza LockTimeoutError si BD ocupada.
    """
    def _op(c: sqlite3.Connection) -> dict:
        fila = c.execute(
            "SELECT factor_id, tipo, keyword FROM config_keywords WHERE id=?", (kw_id,)
        ).fetchone()
        if fila is None:
            raise ValueError(f"Keyword id={kw_id} no existe")
        c.execute("UPDATE config_keywords SET activo=0 WHERE id=?", (kw_id,))
        return {"ok": True, "keyword": fila["keyword"], "tipo": fila["tipo"]}

    resultado = _ejecutar_con_reintentos(db_path, _op)
    invalidar_keywords_cache()
    return resultado


# ──────────────────────────────────────────────────────────────────────────────
# Ingesta manual de URLs (Fase B Item 4)
# ──────────────────────────────────────────────────────────────────────────────

TRIGGER_B2_DIARIO = 10  # ingestas/día que sugieren migrar a Postgres


def guardar_ingesta_manual(db_path: str, url: str, titulo: str, resumen: str,
                           fuente: str, categoria: str, published: str,
                           usuario: str) -> dict:
    """Persiste una URL ingresada manualmente en ingestas_manuales.

    published: ISO 8601 en hora Lima (PET).
    Lanza ValueError si url vacía o duplicada (ya pendiente).
    Lanza LockTimeoutError si BD ocupada.
    """
    url = url.strip()
    if not url:
        raise ValueError("La URL no puede estar vacía")
    if len(url) > 2000:
        raise ValueError("URL demasiado larga (máx 2000 caracteres)")

    def _op(c: sqlite3.Connection) -> dict:
        pendiente = c.execute(
            "SELECT id FROM ingestas_manuales WHERE url=? AND procesada=0",
            (url,),
        ).fetchone()
        if pendiente:
            raise ValueError("Esta URL ya está en la cola de procesamiento (pendiente).")
        c.execute(
            "INSERT INTO ingestas_manuales "
            "(url, titulo, resumen, fuente, categoria, published, procesada, ingresada_por) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (url, (titulo or "")[:500], (resumen or "")[:2000],
             (fuente or "")[:200], categoria or "medios", published, usuario),
        )
        return {"ok": True}

    return _ejecutar_con_reintentos(db_path, _op)


def cargar_ingestas_pendientes(db_path: str) -> list:
    """Devuelve ingestas_manuales con procesada=0 como lista de dicts.
    [] si no hay pendientes o falla (nunca rompe el pipeline)."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT id, url, titulo, resumen, fuente, categoria, published "
                "FROM ingestas_manuales WHERE procesada=0 ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] cargar_ingestas_pendientes falló: {e}")
        return []


def marcar_ingestas_procesadas(db_path: str, ids: list[int]) -> int:
    """Marca las ingestas con los ids dados como procesadas=1.
    Devuelve número de filas actualizadas. Nunca rompe el pipeline."""
    if not ids:
        return 0
    try:
        with _conn(db_path) as c:
            placeholders = ",".join("?" * len(ids))
            c.execute(
                f"UPDATE ingestas_manuales SET procesada=1, procesada_en=datetime('now') "
                f"WHERE id IN ({placeholders})",
                ids,
            )
            n = c.execute("SELECT changes()").fetchone()[0]
            c.commit()
        return n
    except Exception as e:
        print(f"[config_loader] marcar_ingestas_procesadas falló: {e}")
        return 0


def contar_ingestas(db_path: str) -> dict:
    """Devuelve {hoy: int, semana: int} de ingestas manuales (hora UTC)."""
    try:
        with _conn(db_path) as c:
            hoy = c.execute(
                "SELECT COUNT(*) AS n FROM ingestas_manuales "
                "WHERE date(ingresada_en) = date('now')"
            ).fetchone()
            semana = c.execute(
                "SELECT COUNT(*) AS n FROM ingestas_manuales "
                "WHERE ingresada_en >= datetime('now', '-7 days')"
            ).fetchone()
        return {"hoy": hoy["n"] if hoy else 0, "semana": semana["n"] if semana else 0}
    except Exception:
        return {"hoy": 0, "semana": 0}


# Alias para compatibilidad con código anterior
def contar_ingestas_hoy(db_path: str) -> int:
    return contar_ingestas(db_path)["hoy"]


def listar_ingestas(db_path: str, limite: int = 50) -> list:
    """Últimas ingestas manuales para la UI del panel admin."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT id, url, titulo, fuente, categoria, published, "
                "procesada, procesada_en, ingresada_por, ingresada_en "
                "FROM ingestas_manuales ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_ingestas falló: {e}")
        return []
