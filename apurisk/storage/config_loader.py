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


# ──────────────────────────────────────────────────────────────────────────────
# Motor de análisis dual — lectura de estructuras de configuración (Fase C)
# ──────────────────────────────────────────────────────────────────────────────

def cargar_factores_formula(db_path: str, motor: str = "osint") -> dict:
    """Devuelve {tipo_puntaje: [{nombre_factor, peso, descripcion}]} para el motor dado.

    Ejemplo: {"sustancia": [...], "ruido": [...]}
    {} si vacío/falla → el motor usa pesos hardcodeados (1.0 por defecto).
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT tipo_puntaje, nombre_factor, peso, descripcion "
                "FROM config_factores_formula "
                "WHERE motor=? AND activo=1 AND pais='PE' ORDER BY tipo_puntaje, id",
                (motor,),
            ).fetchall()
        result: dict = {}
        for r in rows:
            result.setdefault(r["tipo_puntaje"], []).append({
                "nombre_factor": r["nombre_factor"],
                "peso": float(r["peso"]),
                "descripcion": r["descripcion"] or "",
            })
        return result
    except Exception as e:
        print(f"[config_loader] cargar_factores_formula falló: {e}")
        return {}


def cargar_formula_semaforo(db_path: str, pais: str = "PE") -> list:
    """Devuelve [{factor, nombre, peso}] para la fórmula del semáforo.

    [] si vacío/falla → motor usa peso 1.0 para todos los factores.
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT factor, nombre, peso FROM config_formula_semaforo "
                "WHERE pais=? AND activo=1 ORDER BY id",
                (pais,),
            ).fetchall()
        return [{"factor": r["factor"], "nombre": r["nombre"], "peso": float(r["peso"])}
                for r in rows]
    except Exception as e:
        print(f"[config_loader] cargar_formula_semaforo falló: {e}")
        return []


def cargar_umbrales_semaforo(db_path: str, pais: str = "PE") -> list:
    """Devuelve los umbrales DUALES del semáforo ordenados por rango_min.

    Cada banda: {rango_min, rango_max, nivel_sugerido, color_hex,
                 nivel_secundario, color_secundario_hex}.
    nivel_secundario/color_secundario_hex son None en bandas de color único.
    [] si vacío/falla → motor usa umbrales hardcodeados.
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT rango_min, rango_max, nivel_sugerido, color_hex, "
                "nivel_secundario, color_secundario_hex "
                "FROM config_umbrales_semaforo "
                "WHERE pais=? AND activo=1 ORDER BY rango_min",
                (pais,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] cargar_umbrales_semaforo falló: {e}")
        return []


def cargar_activadores_rojo(db_path: str, pais: str = "PE") -> list:
    """Devuelve [{descripcion, tipo}] de activadores activos, ordenados por orden.

    tipo: 'absoluto' (dispara ROJO por sí mismo) | 'condicional' (depende del contexto).
    [] si vacío/falla → motor no dispara activadores automáticos.
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT descripcion, tipo FROM config_activadores_rojo "
                "WHERE pais=? AND activo=1 ORDER BY orden",
                (pais,),
            ).fetchall()
        return [{"descripcion": r["descripcion"], "tipo": r["tipo"]} for r in rows]
    except Exception as e:
        print(f"[config_loader] cargar_activadores_rojo falló: {e}")
        return []


def cargar_pisos_estructurales(db_path: str, pais: str = "PE") -> dict:
    """Devuelve {tema: piso} con el piso estructural definido por el analista.

    El eje Y de la Matriz B = max(piso, impacto_base). piso=0 → Y = impacto_base.
    {} si vacío/falla → el motor usa solo el impacto base de cada tema.
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT tema, piso FROM config_piso_estructural WHERE pais=?",
                (pais,),
            ).fetchall()
        return {r["tema"]: float(r["piso"]) for r in rows}
    except Exception as e:
        print(f"[config_loader] cargar_pisos_estructurales falló: {e}")
        return {}


def guardar_log_semaforo(db_path: str, campo: str, valor_anterior,
                         valor_nuevo, usuario: str, motivo: str = None) -> None:
    """Registra un cambio de calibración en config_semaforo_log (fire-and-forget)."""
    def _op(c: sqlite3.Connection) -> dict:
        c.execute(
            "INSERT INTO config_semaforo_log (campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "VALUES (?, ?, ?, ?, ?)",
            (campo, str(valor_anterior) if valor_anterior is not None else None,
             str(valor_nuevo), usuario, motivo),
        )
        return {"ok": True}
    try:
        _ejecutar_con_reintentos(db_path, _op)
    except Exception as e:
        print(f"[config_loader] guardar_log_semaforo falló (no crítico): {e}")


def actualizar_piso_estructural(db_path: str, tema: str, piso: float,
                                usuario: str, pais: str = "PE",
                                notas: str = None) -> dict:
    """Upsert del piso estructural de un tema (0-100). Registra en log. Devuelve {ok, accion}."""
    piso = max(0.0, min(100.0, float(piso)))

    def _op(c: sqlite3.Connection) -> dict:
        row = c.execute(
            "SELECT id, piso FROM config_piso_estructural WHERE pais=? AND tema=?",
            (pais, tema),
        ).fetchone()
        valor_anterior = row["piso"] if row else None
        if row:
            c.execute(
                "UPDATE config_piso_estructural SET piso=?, notas=?, "
                "actualizado_en=datetime('now') WHERE pais=? AND tema=?",
                (piso, notas, pais, tema),
            )
        else:
            c.execute(
                "INSERT INTO config_piso_estructural (pais, tema, piso, notas) "
                "VALUES (?, ?, ?, ?)",
                (pais, tema, piso, notas),
            )
        return {"ok": True, "valor_anterior": valor_anterior}

    r = _ejecutar_con_reintentos(db_path, _op)
    guardar_log_semaforo(db_path, f"piso:{tema}", r.get("valor_anterior"), piso,
                         usuario, notas)
    return r


def actualizar_parametro_semaforo(db_path: str, clave: str, valor: float,
                                  usuario: str, motivo: str = None) -> dict:
    """Actualiza un parámetro editable del semáforo en config_parametros.

    Registra el cambio en config_semaforo_log. Devuelve {ok, valor_anterior, valor_nuevo}.
    """
    def _op(c: sqlite3.Connection) -> dict:
        row = c.execute(
            "SELECT valor FROM config_parametros WHERE clave=?", (clave,)
        ).fetchone()
        valor_anterior = row["valor"] if row else None
        if row:
            c.execute(
                "UPDATE config_parametros SET valor=? WHERE clave=?",
                (str(valor), clave),
            )
        else:
            c.execute(
                "INSERT INTO config_parametros (clave, valor, tipo, descripcion, pais) "
                "VALUES (?, ?, 'float', 'Parámetro del semáforo', 'GLOBAL')",
                (clave, str(valor)),
            )
        return {"ok": True, "valor_anterior": valor_anterior, "valor_nuevo": str(valor)}

    r = _ejecutar_con_reintentos(db_path, _op)
    guardar_log_semaforo(db_path, clave, r.get("valor_anterior"), r["valor_nuevo"],
                         usuario, motivo)
    return r


def listar_log_semaforo(db_path: str, limite: int = 100) -> list:
    """Devuelve las últimas entradas del log de calibración del semáforo."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT campo, valor_anterior, valor_nuevo, usuario, motivo, cambiado_en "
                "FROM config_semaforo_log ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_log_semaforo falló: {e}")
        return []


def cargar_parametros_semaforo(db_path: str) -> dict:
    """Devuelve los parámetros editables de la Matriz B con defaults seguros.

    Claves: umbral_x, umbral_y, coef_actividad, coef_simultaneidad, bonus_max.
    Si la BD no responde, devuelve los defaults documentados.
    """
    defaults = {
        "umbral_x": 25.0,
        "umbral_y": 65.0,
        "coef_actividad": 8.0,
        "coef_simultaneidad": 3.5,
        "bonus_max": 15.0,
        "x_max_viz": 0.0,   # 0 = escala dinámica del eje X (máximo real + margen)
        # Urgencia por velocidad + Score "temperatura del momento"
        "vel_urgente": 30.0,
        "vel_prioritario": 10.0,
        "piso_gravedad": 65.0,
        "urgencia_ref": 50.0,
        # Urgencia combinada (gravedad + actividad + velocidad) con intensidad graduada
        "act_urgente": 10.0,
        "act_prioritario": 5.0,
        "peso_gravedad": 0.6,
        "peso_actividad": 0.25,
        "peso_velocidad": 0.15,
        "act_ref": 15.0,
        "vel_ref": 5.0,
        "coef_sim_idx": 0.03,
    }
    mapa = {
        "SEMAFORO_UMBRAL_ACTIVIDAD_X": "umbral_x",
        "SEMAFORO_UMBRAL_GRAVEDAD_Y": "umbral_y",
        "SCORE_B_COEF_ACTIVIDAD": "coef_actividad",
        "SCORE_B_COEF_SIMULTANEIDAD": "coef_simultaneidad",
        "SCORE_B_BONUS_MAX": "bonus_max",
        "SEMAFORO_X_MAX_VIZ": "x_max_viz",
        "SEMAFORO_VELOCIDAD_URGENTE": "vel_urgente",
        "SEMAFORO_VELOCIDAD_PRIORITARIO": "vel_prioritario",
        "SCORE_B_PISO_GRAVEDAD": "piso_gravedad",
        "SCORE_B_URGENCIA_REF": "urgencia_ref",
        "SEMAFORO_ACTIVIDAD_URGENTE": "act_urgente",
        "SEMAFORO_ACTIVIDAD_PRIORITARIO": "act_prioritario",
        "URGENCIA_PESO_GRAVEDAD": "peso_gravedad",
        "URGENCIA_PESO_ACTIVIDAD": "peso_actividad",
        "URGENCIA_PESO_VELOCIDAD": "peso_velocidad",
        "URGENCIA_ACT_REF": "act_ref",
        "URGENCIA_VEL_REF": "vel_ref",
        "SCORE_B_COEF_SIM_IDX": "coef_sim_idx",
    }
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT clave, valor FROM config_parametros WHERE clave IN "
                "('SEMAFORO_UMBRAL_ACTIVIDAD_X','SEMAFORO_UMBRAL_GRAVEDAD_Y',"
                "'SCORE_B_COEF_ACTIVIDAD','SCORE_B_COEF_SIMULTANEIDAD','SCORE_B_BONUS_MAX',"
                "'SEMAFORO_X_MAX_VIZ','SEMAFORO_VELOCIDAD_URGENTE','SEMAFORO_VELOCIDAD_PRIORITARIO',"
                "'SCORE_B_PISO_GRAVEDAD','SCORE_B_URGENCIA_REF',"
                "'SEMAFORO_ACTIVIDAD_URGENTE','SEMAFORO_ACTIVIDAD_PRIORITARIO',"
                "'URGENCIA_PESO_GRAVEDAD','URGENCIA_PESO_ACTIVIDAD','URGENCIA_PESO_VELOCIDAD',"
                "'URGENCIA_ACT_REF','URGENCIA_VEL_REF','SCORE_B_COEF_SIM_IDX')",
            ).fetchall()
        for r in rows:
            k = mapa.get(r["clave"])
            if k:
                try:
                    defaults[k] = float(r["valor"])
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        print(f"[config_loader] cargar_parametros_semaforo falló: {e}")
    return defaults


def guardar_resultado_analisis(db_path: str, articulo_id: int, motor: str,
                               score_sustancia: float = None, score_ruido: float = None,
                               score_semaforo: float = None, nivel_semaforo: str = None,
                               activador_rojo: bool = False, resultado_json: str = None,
                               pais: str = "PE") -> dict:
    """Guarda o actualiza el resultado de un motor para un artículo (upsert).

    Unicidad: (articulo_id, motor). Si ya existe, sobrescribe todos los campos.
    Lanza LockTimeoutError si BD ocupada. Devuelve {ok, accion}.
    """
    def _op(c: sqlite3.Connection) -> dict:
        existe = c.execute(
            "SELECT id FROM resultados_analisis WHERE articulo_id=? AND motor=?",
            (articulo_id, motor),
        ).fetchone()
        if existe:
            c.execute(
                "UPDATE resultados_analisis SET pais=?, score_sustancia=?, score_ruido=?, "
                "score_semaforo=?, nivel_semaforo=?, activador_rojo=?, resultado_json=?, "
                "procesado_en=datetime('now') WHERE articulo_id=? AND motor=?",
                (pais, score_sustancia, score_ruido, score_semaforo, nivel_semaforo,
                 1 if activador_rojo else 0, resultado_json, articulo_id, motor),
            )
            return {"ok": True, "accion": "actualizado"}
        c.execute(
            "INSERT INTO resultados_analisis "
            "(articulo_id, motor, pais, score_sustancia, score_ruido, "
            "score_semaforo, nivel_semaforo, activador_rojo, resultado_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (articulo_id, motor, pais, score_sustancia, score_ruido,
             score_semaforo, nivel_semaforo, 1 if activador_rojo else 0, resultado_json),
        )
        return {"ok": True, "accion": "creado"}

    return _ejecutar_con_reintentos(db_path, _op)


def listar_resultados_articulo(db_path: str, articulo_id: int) -> list:
    """Devuelve los resultados de todos los motores para un artículo dado."""
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT motor, score_sustancia, score_ruido, score_semaforo, "
                "nivel_semaforo, activador_rojo, procesado_en "
                "FROM resultados_analisis WHERE articulo_id=? ORDER BY motor",
                (articulo_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_resultados_articulo falló: {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Registro de actores — Capas 1 y 2
# ──────────────────────────────────────────────────────────────────────────────

# Niveles estratégicos: código → (nombre, valor_base_default)
NIVELES_ACTOR = {
    "I":    ("Estructural",  95),
    "II":   ("Sistémico",    85),
    "III":  ("Determinante", 72),
    "IV":   ("Relevante",    60),
    "V":    ("Incidente",    48),
    "VI":   ("Emergente",    36),
    "VII":  ("Latente",      24),
    "VIII": ("Periférico",   12),
}

TIPOS_ACTOR = ("formal", "fáctico", "territorial", "informal")


def calcular_peso_actor(nivel_base: float, crits: dict) -> tuple[float, float]:
    """Calcula (capacidad_efectiva, peso_calculado) a partir del nivel_base y los 6 criterios.

    crits: {decision, recursos, articulacion, legitimidad, resiliencia, proyeccion}
    Fórmula: cap = [(d+r+a)×2 + (l+rs+p)] / 45
             peso = nivel_base × (0.5 + 0.5 × cap)
    """
    d  = max(1, min(5, int(crits.get("decision",    3))))
    r  = max(1, min(5, int(crits.get("recursos",    3))))
    a  = max(1, min(5, int(crits.get("articulacion",3))))
    l  = max(1, min(5, int(crits.get("legitimidad", 3))))
    rs = max(1, min(5, int(crits.get("resiliencia", 3))))
    p  = max(1, min(5, int(crits.get("proyeccion",  3))))
    cap = ((d + r + a) * 2 + (l + rs + p)) / 45.0
    peso = nivel_base * (0.5 + 0.5 * cap)
    return round(cap, 4), round(peso, 1)


def cargar_niveles_base(db_path: str) -> dict:
    """Devuelve {nivel: valor_base} leyendo config_parametros. Usa defaults si falla."""
    defaults = {k: float(v) for k, (_, v) in NIVELES_ACTOR.items()}
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT clave, valor FROM config_parametros "
                "WHERE clave LIKE 'ACTOR_NIVEL_%_BASE'"
            ).fetchall()
        for r in rows:
            # clave = 'ACTOR_NIVEL_II_BASE' → nivel = 'II'
            partes = r["clave"].split("_")
            if len(partes) == 4:
                nivel = partes[2]
                if nivel in defaults:
                    try:
                        defaults[nivel] = float(r["valor"])
                    except (TypeError, ValueError):
                        pass
    except Exception as e:
        print(f"[config_loader] cargar_niveles_base falló: {e}")
    return defaults


def crear_actor(db_path: str, datos: dict, usuario: str) -> dict:
    """Inserta un nuevo actor. Calcula y persiste peso_calculado. Devuelve {ok, id}."""
    niveles = cargar_niveles_base(db_path)
    nivel = datos.get("nivel", "IV")
    nivel_base = float(datos.get("nivel_base", niveles.get(nivel, 60)))
    nivel_base_manual = int(bool(datos.get("nivel_base_manual", False)))
    crits = {
        "decision": datos.get("crit_decision", 3),
        "recursos": datos.get("crit_recursos", 3),
        "articulacion": datos.get("crit_articulacion", 3),
        "legitimidad": datos.get("crit_legitimidad", 3),
        "resiliencia": datos.get("crit_resiliencia", 3),
        "proyeccion": datos.get("crit_proyeccion", 3),
    }
    cap, peso = calcular_peso_actor(nivel_base, crits)

    def _op(c: sqlite3.Connection) -> dict:
        c.execute(
            "INSERT INTO config_actores "
            "(pais, nombre, tipo, nivel, nivel_base, nivel_base_manual, "
            "crit_decision, crit_recursos, crit_articulacion, "
            "crit_legitimidad, crit_resiliencia, crit_proyeccion, "
            "capacidad_efectiva, peso_calculado, territorio, alias, activo, notas_analista) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datos.get("pais", "PE"), datos["nombre"].strip(),
             datos.get("tipo", "formal"), nivel, nivel_base, nivel_base_manual,
             crits["decision"], crits["recursos"], crits["articulacion"],
             crits["legitimidad"], crits["resiliencia"], crits["proyeccion"],
             cap, peso,
             datos.get("territorio", "nacional"),
             datos.get("alias") or None,
             1,
             datos.get("notas_analista") or None),
        )
        actor_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Temas relacionados
        for tema in datos.get("temas", []):
            c.execute(
                "INSERT OR IGNORE INTO config_actor_temas (actor_id, tema, pais) VALUES (?,?,?)",
                (actor_id, tema, datos.get("pais", "PE")),
            )
        c.execute(
            "INSERT INTO config_actores_log "
            "(actor_id, actor_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "VALUES (?,?,'creado',NULL,?,?,?)",
            (actor_id, datos["nombre"].strip(),
             f"peso={peso}", usuario, "creación"),
        )
        return {"ok": True, "id": actor_id}

    return _ejecutar_con_reintentos(db_path, _op)


def obtener_actor(db_path: str, actor_id: int) -> dict | None:
    """Devuelve el dict completo de un actor + su lista de temas. None si no existe."""
    try:
        with _conn(db_path) as c:
            row = c.execute(
                "SELECT * FROM config_actores WHERE id=?", (actor_id,)
            ).fetchone()
            if not row:
                return None
            actor = dict(row)
            temas = c.execute(
                "SELECT tema FROM config_actor_temas WHERE actor_id=? ORDER BY tema",
                (actor_id,),
            ).fetchall()
            actor["temas"] = [t["tema"] for t in temas]
        return actor
    except Exception as e:
        print(f"[config_loader] obtener_actor falló: {e}")
        return None


def listar_actores(db_path: str, pais: str = "PE",
                   solo_activos: bool = False) -> list:
    """Devuelve lista de actores ordenada por peso DESC, con sus temas."""
    try:
        filtro = "WHERE a.pais=?" + (" AND a.activo=1" if solo_activos else "")
        with _conn(db_path) as c:
            rows = c.execute(
                f"SELECT a.*, GROUP_CONCAT(t.tema, ',') as temas_str "
                f"FROM config_actores a "
                f"LEFT JOIN config_actor_temas t ON t.actor_id=a.id "
                f"{filtro} "
                f"GROUP BY a.id ORDER BY a.peso_calculado DESC",
                (pais,),
            ).fetchall()
        result = []
        for r in rows:
            a = dict(r)
            ts = a.pop("temas_str", "") or ""
            a["temas"] = [t for t in ts.split(",") if t]
            result.append(a)
        return result
    except Exception as e:
        print(f"[config_loader] listar_actores falló: {e}")
        return []


def actualizar_actor(db_path: str, actor_id: int, datos: dict,
                     usuario: str, motivo: str = None) -> dict:
    """Actualiza campos de un actor, recalcula peso, registra cambios en log.

    Devuelve {ok, peso_nuevo, cap_nueva}.
    """
    niveles = cargar_niveles_base(db_path)

    def _op(c: sqlite3.Connection) -> dict:
        old = c.execute("SELECT * FROM config_actores WHERE id=?", (actor_id,)).fetchone()
        if not old:
            raise ValueError(f"Actor {actor_id} no existe")
        old = dict(old)

        nivel = datos.get("nivel", old["nivel"])
        # nivel_base: usa el del formulario si viene; si no, hereda el del actor
        if "nivel_base" in datos and datos.get("nivel_base_manual"):
            nivel_base = float(datos["nivel_base"])
            nivel_base_manual = 1
        elif "nivel_base" in datos and not datos.get("nivel_base_manual"):
            nivel_base = float(datos["nivel_base"])
            nivel_base_manual = 0
        else:
            nivel_base = old["nivel_base"]
            nivel_base_manual = old["nivel_base_manual"]

        crits = {
            "decision":    int(datos.get("crit_decision",    old["crit_decision"])),
            "recursos":    int(datos.get("crit_recursos",    old["crit_recursos"])),
            "articulacion":int(datos.get("crit_articulacion",old["crit_articulacion"])),
            "legitimidad": int(datos.get("crit_legitimidad", old["crit_legitimidad"])),
            "resiliencia": int(datos.get("crit_resiliencia", old["crit_resiliencia"])),
            "proyeccion":  int(datos.get("crit_proyeccion",  old["crit_proyeccion"])),
        }
        cap, peso = calcular_peso_actor(nivel_base, crits)

        c.execute(
            "UPDATE config_actores SET "
            "nombre=?, tipo=?, nivel=?, nivel_base=?, nivel_base_manual=?, "
            "crit_decision=?, crit_recursos=?, crit_articulacion=?, "
            "crit_legitimidad=?, crit_resiliencia=?, crit_proyeccion=?, "
            "capacidad_efectiva=?, peso_calculado=?, territorio=?, "
            "alias=?, notas_analista=?, actualizado_en=datetime('now') WHERE id=?",
            (datos.get("nombre", old["nombre"]).strip(),
             datos.get("tipo", old["tipo"]),
             nivel, nivel_base, nivel_base_manual,
             crits["decision"], crits["recursos"], crits["articulacion"],
             crits["legitimidad"], crits["resiliencia"], crits["proyeccion"],
             cap, peso,
             datos.get("territorio", old["territorio"]),
             datos.get("alias", old.get("alias")) or None,
             datos.get("notas_analista", old["notas_analista"]) or None,
             actor_id),
        )
        # Actualizar temas si vienen en datos
        if "temas" in datos:
            c.execute("DELETE FROM config_actor_temas WHERE actor_id=?", (actor_id,))
            for tema in datos["temas"]:
                c.execute(
                    "INSERT OR IGNORE INTO config_actor_temas (actor_id, tema, pais) "
                    "VALUES (?,?,?)",
                    (actor_id, tema, datos.get("pais", old["pais"])),
                )
        # Log: registra campo por campo solo los que cambiaron
        cambios = []
        for campo_log, v_old, v_new in [
            ("nivel",        old["nivel"],        nivel),
            ("nivel_base",   old["nivel_base"],   nivel_base),
            ("crit_decision",old["crit_decision"],crits["decision"]),
            ("crit_recursos",old["crit_recursos"],crits["recursos"]),
            ("crit_articulacion",old["crit_articulacion"],crits["articulacion"]),
            ("crit_legitimidad",old["crit_legitimidad"],crits["legitimidad"]),
            ("crit_resiliencia",old["crit_resiliencia"],crits["resiliencia"]),
            ("crit_proyeccion",old["crit_proyeccion"],crits["proyeccion"]),
            ("peso_calculado",old["peso_calculado"],peso),
        ]:
            if str(v_old) != str(v_new):
                cambios.append((campo_log, str(v_old), str(v_new)))
        for campo_log, va, vn in cambios:
            c.execute(
                "INSERT INTO config_actores_log "
                "(actor_id, actor_nombre, campo, valor_anterior, valor_nuevo, "
                "usuario, motivo) VALUES (?,?,?,?,?,?,?)",
                (actor_id, old["nombre"], campo_log, va, vn, usuario, motivo),
            )
        return {"ok": True, "peso_nuevo": peso, "cap_nueva": cap,
                "n_cambios": len(cambios)}

    return _ejecutar_con_reintentos(db_path, _op)


def toggle_actor(db_path: str, actor_id: int, usuario: str) -> dict:
    """Activa o desactiva un actor. Devuelve {ok, activo_nuevo}."""
    def _op(c: sqlite3.Connection) -> dict:
        row = c.execute(
            "SELECT activo, nombre FROM config_actores WHERE id=?", (actor_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Actor {actor_id} no existe")
        nuevo = 1 - row["activo"]
        c.execute(
            "UPDATE config_actores SET activo=?, actualizado_en=datetime('now') WHERE id=?",
            (nuevo, actor_id),
        )
        c.execute(
            "INSERT INTO config_actores_log "
            "(actor_id, actor_nombre, campo, valor_anterior, valor_nuevo, usuario) "
            "VALUES (?,?,'activo',?,?,?)",
            (actor_id, row["nombre"], str(row["activo"]), str(nuevo), usuario),
        )
        return {"ok": True, "activo_nuevo": nuevo}

    return _ejecutar_con_reintentos(db_path, _op)


def propagar_nivel_base(db_path: str, nivel: str, valor_nuevo: float,
                        usuario: str, motivo: str = None) -> dict:
    """Actualiza nivel_base en config_parametros y propaga a todos los actores del nivel
    que NO tengan nivel_base_manual=1. Recalcula peso_calculado de cada uno.
    Registra un log por actor afectado. Devuelve {ok, n_afectados, valor_anterior}.
    """
    clave = f"ACTOR_NIVEL_{nivel}_BASE"

    def _op(c: sqlite3.Connection) -> dict:
        row = c.execute("SELECT valor FROM config_parametros WHERE clave=?",
                        (clave,)).fetchone()
        valor_anterior = float(row["valor"]) if row else None
        # Actualizar parámetro
        if row:
            c.execute("UPDATE config_parametros SET valor=? WHERE clave=?",
                      (str(valor_nuevo), clave))
        else:
            c.execute(
                "INSERT INTO config_parametros (clave, valor, tipo, descripcion, pais) "
                "VALUES (?,?,'float','Valor base de nivel actor','GLOBAL')",
                (clave, str(valor_nuevo)),
            )
        # Propagar a actores del nivel que no tienen ajuste manual
        actores = c.execute(
            "SELECT id, nombre, crit_decision, crit_recursos, crit_articulacion, "
            "crit_legitimidad, crit_resiliencia, crit_proyeccion "
            "FROM config_actores WHERE nivel=? AND nivel_base_manual=0 AND activo=1",
            (nivel,),
        ).fetchall()
        for a in actores:
            crits = {
                "decision":     a["crit_decision"],
                "recursos":     a["crit_recursos"],
                "articulacion": a["crit_articulacion"],
                "legitimidad":  a["crit_legitimidad"],
                "resiliencia":  a["crit_resiliencia"],
                "proyeccion":   a["crit_proyeccion"],
            }
            cap, peso = calcular_peso_actor(valor_nuevo, crits)
            old_peso = c.execute(
                "SELECT peso_calculado FROM config_actores WHERE id=?", (a["id"],)
            ).fetchone()["peso_calculado"]
            c.execute(
                "UPDATE config_actores SET nivel_base=?, capacidad_efectiva=?, "
                "peso_calculado=?, actualizado_en=datetime('now') WHERE id=?",
                (valor_nuevo, cap, peso, a["id"]),
            )
            c.execute(
                "INSERT INTO config_actores_log "
                "(actor_id, actor_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
                "VALUES (?,?,'nivel_base',?,?,?,?)",
                (a["id"], a["nombre"], str(valor_anterior), str(valor_nuevo),
                 usuario, f"propagación nivel {nivel}: {motivo or 'cambio de parámetro'}"),
            )
            c.execute(
                "INSERT INTO config_actores_log "
                "(actor_id, actor_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
                "VALUES (?,?,'peso_calculado',?,?,?,?)",
                (a["id"], a["nombre"], str(old_peso), str(peso),
                 usuario, f"recálculo por propagación nivel {nivel}"),
            )
        return {"ok": True, "n_afectados": len(actores),
                "valor_anterior": valor_anterior, "valor_nuevo": valor_nuevo}

    return _ejecutar_con_reintentos(db_path, _op)


def emparejar_entidad_con_actor(
    entidad: str, actores: list[dict]
) -> dict | None:
    """Busca en la lista de actores el que coincide con la entidad detectada en prensa.

    Estrategia (en orden de preferencia):
      1. Coincidencia exacta (case-insensitive) con actor.nombre
      2. Coincidencia de substring en actor.alias (campo CSV separado por comas)
      3. Coincidencia de substring con actor.nombre

    Devuelve el dict del actor si hay match, None si no hay ninguno.
    """
    entidad_lower = entidad.lower().strip()
    # Paso 1: coincidencia exacta por nombre
    for actor in actores:
        if actor.get("nombre", "").lower().strip() == entidad_lower:
            return actor
    # Paso 2: substring en alias
    for actor in actores:
        alias_raw = actor.get("alias") or ""
        variantes = [v.strip().lower() for v in alias_raw.split(",") if v.strip()]
        for v in variantes:
            if v and (v in entidad_lower or entidad_lower in v):
                return actor
    # Paso 3: substring en nombre
    for actor in actores:
        nombre_lower = actor.get("nombre", "").lower()
        if nombre_lower and (nombre_lower in entidad_lower or entidad_lower in nombre_lower):
            return actor
    return None


def cargar_actores_visibles_por_tema(
    db_path: str,
    articulos_por_tema: dict,
    pais: str = "PE",
) -> dict:
    """Para cada tema, detecta la entidad más mencionada en sus artículos y la empareja
    con un actor de config_actores.

    articulos_por_tema: {tema: [article, ...]} — salida de detectar_temas()["articulos_por_tema"].

    Devuelve {tema: {
        "entidad_visible":   str,           # nombre tal como aparece en prensa
        "menciones_visible": int,
        "actor_match":       dict | None,   # actor de la base si hay emparejamiento
        "emparejado":        bool,
    }}.
    """
    from .entities import INSTITUCIONES, PARTIDOS, EMPRESAS_RIESGO, _find_all
    from collections import Counter

    actores_db: list[dict] = []
    try:
        actores_db = listar_actores(db_path, pais=pais, solo_activos=True)
    except Exception as e:
        print(f"[config_loader] cargar_actores_visibles_por_tema: {e}")

    resultado: dict = {}
    for tema, arts in articulos_por_tema.items():
        if not arts:
            continue
        conteo: Counter = Counter()
        todas_entidades = INSTITUCIONES + PARTIDOS + EMPRESAS_RIESGO
        for a in arts:
            text = (a.title or "") + " " + (a.summary or "")
            for ent in _find_all(text, todas_entidades):
                conteo[ent] += 1
        if not conteo:
            continue
        entidad_top, menciones = conteo.most_common(1)[0]
        actor_match = emparejar_entidad_con_actor(entidad_top, actores_db)
        resultado[tema] = {
            "entidad_visible": entidad_top,
            "menciones_visible": menciones,
            "actor_match": actor_match,
            "emparejado": actor_match is not None,
        }
    return resultado


def cargar_pa_por_tema(db_path: str, pais: str = "PE") -> dict:
    """Calcula PA_tema desde los actores activos vinculados a cada tema.

    Fórmula:
      PA_tema = peso_mayor + min(TOPE_BONUS, FACTOR_AGRAVANTE × n_adicionales)
      donde:
        peso_mayor   = max(peso_calculado) de actores activos vinculados al tema
        actores_fuertes = actores con peso_calculado >= UMBRAL_ACTOR_FUERTE
        n_adicionales   = len(actores_fuertes) - 1

    Devuelve {tema: {pa, origen, peso_mayor, actor_principal, actores_fuertes,
                     n_actores, n_adicionales, bonus}}.
    Temas sin actores vinculados NO aparecen en el dict (el caller usa fallback).
    """
    umbral = 70.0
    factor = 3.0
    tope   = 10.0
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT clave, valor FROM config_parametros "
                "WHERE clave IN ('UMBRAL_ACTOR_FUERTE','FACTOR_AGRAVANTE','TOPE_BONUS')"
            ).fetchall()
        for r in rows:
            try:
                val = float(r["valor"])
                if r["clave"] == "UMBRAL_ACTOR_FUERTE":
                    umbral = val
                elif r["clave"] == "FACTOR_AGRAVANTE":
                    factor = val
                elif r["clave"] == "TOPE_BONUS":
                    tope = val
            except (TypeError, ValueError):
                pass
    except Exception as e:
        print(f"[config_loader] cargar_pa_por_tema: fallo al leer params: {e}")

    resultado: dict = {}
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT t.tema, a.nombre, a.peso_calculado "
                "FROM config_actor_temas t "
                "JOIN config_actores a ON a.id = t.actor_id "
                "WHERE a.activo = 1 AND a.pais = ? "
                "ORDER BY t.tema, a.peso_calculado DESC",
                (pais,),
            ).fetchall()

        por_tema: dict[str, list] = {}
        for r in rows:
            tema = r["tema"]
            if tema not in por_tema:
                por_tema[tema] = []
            por_tema[tema].append({
                "nombre": r["nombre"],
                "peso": round(float(r["peso_calculado"]), 1),
            })

        for tema, actores in por_tema.items():
            peso_mayor = actores[0]["peso"]
            actores_fuertes = [a for a in actores if a["peso"] >= umbral]
            n_adicionales = max(0, len(actores_fuertes) - 1)
            bonus = round(min(tope, factor * n_adicionales), 1)
            pa = round(min(100.0, peso_mayor + bonus), 1)
            resultado[tema] = {
                "pa": pa,
                "origen": "real",
                "peso_mayor": peso_mayor,
                "actor_principal": actores[0]["nombre"],
                "actores_fuertes": actores_fuertes,
                "n_actores": len(actores),
                "n_adicionales": n_adicionales,
                "bonus": bonus,
            }
    except Exception as e:
        print(f"[config_loader] cargar_pa_por_tema: fallo al calcular: {e}")

    return resultado


def _calcular_indice_cvo(peso: float,
                         interes: int, postura: int, antecedente: int,
                         ventana: int, contrapesos: int, recursos: int) -> float:
    """Índice de Activación Estratégica CVO (0-100).

    C = peso/100 (capacidad normalizada)
    V = (interes×2 + postura + antecedente) / 20  (voluntad, interes pesa doble)
    O = (ventana×2 + contrapesos + recursos) / 20  (oportunidad, ventana pesa doble)
    Índice = (C × V × O)^(1/3) × 100
    Si cualquiera es 0, el índice es 0.
    """
    import math as _math
    c = max(0.0, min(1.0, peso / 100.0))
    v = (interes * 2 + postura + antecedente) / 20.0
    o = (ventana * 2 + contrapesos + recursos) / 20.0
    if c <= 0 or v <= 0 or o <= 0:
        return 0.0
    return round(_math.pow(c * v * o, 1 / 3) * 100, 1)


def cargar_cvo_actor(db_path: str, actor_id: int) -> list:
    """Devuelve la lista de señales CVO por tema para un actor dado.

    Retorna [{tema, interes_directo, postura_declarada, antecedente_accion,
              ventana_coyuntural, ausencia_contrapesos, recursos_movilizables,
              indice_activacion}]
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT tema, interes_directo, postura_declarada, antecedente_accion, "
                "ventana_coyuntural, ausencia_contrapesos, recursos_movilizables, "
                "indice_activacion, divergencia_dinamica "
                "FROM config_actor_temas WHERE actor_id=? ORDER BY tema",
                (actor_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] cargar_cvo_actor falló: {e}")
        return []


def guardar_cvo_actor_tema(db_path: str, actor_id: int, tema: str,
                            senales: dict, peso_actor: float,
                            usuario: str) -> float:
    """Guarda las 6 señales CVO para un actor-tema y recalcula el índice.

    senales: {interes_directo, postura_declarada, antecedente_accion,
              ventana_coyuntural, ausencia_contrapesos, recursos_movilizables}
    Retorna el índice recalculado (0-100).
    """
    indice = _calcular_indice_cvo(
        peso_actor,
        int(senales.get("interes_directo", 3)),
        int(senales.get("postura_declarada", 3)),
        int(senales.get("antecedente_accion", 3)),
        int(senales.get("ventana_coyuntural", 3)),
        int(senales.get("ausencia_contrapesos", 3)),
        int(senales.get("recursos_movilizables", 3)),
    )
    with _conn(db_path) as c:
        c.execute(
            "UPDATE config_actor_temas SET "
            "interes_directo=?, postura_declarada=?, antecedente_accion=?, "
            "ventana_coyuntural=?, ausencia_contrapesos=?, recursos_movilizables=?, "
            "indice_activacion=? "
            "WHERE actor_id=? AND tema=?",
            (
                int(senales.get("interes_directo", 3)),
                int(senales.get("postura_declarada", 3)),
                int(senales.get("antecedente_accion", 3)),
                int(senales.get("ventana_coyuntural", 3)),
                int(senales.get("ausencia_contrapesos", 3)),
                int(senales.get("recursos_movilizables", 3)),
                indice,
                actor_id, tema,
            ),
        )
        # Log de trazabilidad
        c.execute(
            "INSERT INTO config_actores_log "
            "(actor_id, actor_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "SELECT ?, nombre, ?, NULL, ?, ?, ? FROM config_actores WHERE id=?",
            (actor_id, f"cvo:{tema}", f"índice={indice}", usuario,
             f"señales CVO editadas para {tema}", actor_id),
        )
    return indice


def listar_actores_por_activacion(db_path: str, tema: str,
                                   pais: str = "PE") -> list:
    """Actores vinculados a un tema, ordenados por índice_activacion DESC.

    Retorna [{id, nombre, nivel, tipo, peso_calculado, capacidad_efectiva,
              interes_directo, postura_declarada, antecedente_accion,
              ventana_coyuntural, ausencia_contrapesos, recursos_movilizables,
              indice_activacion (puede ser NULL → mostrar como pendiente),
              v_norm, o_norm}]
    """
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT a.id, a.nombre, a.nivel, a.tipo, a.peso_calculado, "
                "a.capacidad_efectiva, t.interes_directo, t.postura_declarada, "
                "t.antecedente_accion, t.ventana_coyuntural, t.ausencia_contrapesos, "
                "t.recursos_movilizables, t.indice_activacion, t.divergencia_dinamica, "
                "a.din_alianzas, a.din_financiamiento, a.din_territorio, "
                "a.din_instituciones, a.din_internacional, a.din_relevo_lideres, "
                "a.din_adaptacion "
                "FROM config_actor_temas t "
                "JOIN config_actores a ON a.id = t.actor_id "
                "WHERE t.tema=? AND a.pais=? AND a.activo=1 "
                "ORDER BY t.indice_activacion DESC NULLS LAST",
                (tema, pais),
            ).fetchall()
        par = cargar_parametros_trayectoria(db_path)
        result = []
        for r in rows:
            d = dict(r)
            v = (d["interes_directo"] * 2 + d["postura_declarada"] + d["antecedente_accion"]) / 20.0
            o = (d["ventana_coyuntural"] * 2 + d["ausencia_contrapesos"] + d["recursos_movilizables"]) / 20.0
            d["v_norm"] = round(v, 2)
            d["o_norm"] = round(o, 2)
            # Trayectoria de poder (Capa 4): base + divergencia × factor
            base = sum(int(d[k]) for k in _DIN_CAMPOS)
            div = int(d.get("divergencia_dinamica") or 0)
            en_tema = base + div * par["factor_div"]
            d["trayectoria_base"] = base
            d["divergencia_dinamica"] = div
            d["trayectoria_en_tema"] = en_tema
            d["trayectoria_etiqueta"] = etiqueta_trayectoria(
                en_tema, par["umbral_ascenso"], par["umbral_declive"])
            result.append(d)
        return result
    except Exception as e:
        print(f"[config_loader] listar_actores_por_activacion falló: {e}")
        return []


# ── Capa 4 Dinámica — trayectoria de poder ───────────────────────────────────

_DIN_CAMPOS = [
    "din_alianzas", "din_financiamiento", "din_territorio", "din_instituciones",
    "din_internacional", "din_relevo_lideres", "din_adaptacion",
]


def cargar_parametros_trayectoria(db_path: str) -> dict:
    """Umbrales y factor de divergencia de la Capa 4 con defaults seguros."""
    defaults = {"umbral_ascenso": 6.0, "umbral_declive": -6.0, "factor_div": 3.0}
    mapa = {
        "TRAYECTORIA_UMBRAL_ASCENSO": "umbral_ascenso",
        "TRAYECTORIA_UMBRAL_DECLIVE": "umbral_declive",
        "TRAYECTORIA_FACTOR_DIV": "factor_div",
    }
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT clave, valor FROM config_parametros WHERE clave IN "
                "('TRAYECTORIA_UMBRAL_ASCENSO','TRAYECTORIA_UMBRAL_DECLIVE',"
                "'TRAYECTORIA_FACTOR_DIV')",
            ).fetchall()
        for r in rows:
            k = mapa.get(r["clave"])
            if k:
                try:
                    defaults[k] = float(r["valor"])
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        print(f"[config_loader] cargar_parametros_trayectoria falló: {e}")
    return defaults


def etiqueta_trayectoria(valor: float, umbral_ascenso: float,
                         umbral_declive: float) -> str:
    """ASCENSO / ESTABLE / DECLIVE según umbrales editables."""
    if valor >= umbral_ascenso:
        return "ASCENSO"
    if valor <= umbral_declive:
        return "DECLIVE"
    return "ESTABLE"


def cargar_dinamica_actor(db_path: str, actor_id: int) -> dict:
    """Las 7 señales dinámicas del actor + trayectoria_base y etiqueta.

    Retorna {din_alianzas, ..., trayectoria_base, etiqueta}.
    """
    cols = ", ".join(_DIN_CAMPOS)
    base = {k: 0 for k in _DIN_CAMPOS}
    try:
        with _conn(db_path) as c:
            row = c.execute(
                f"SELECT {cols} FROM config_actores WHERE id=?",
                (actor_id,),
            ).fetchone()
        if row:
            base = {k: int(row[k]) for k in _DIN_CAMPOS}
    except Exception as e:
        print(f"[config_loader] cargar_dinamica_actor falló: {e}")
    trayectoria = sum(base.values())
    par = cargar_parametros_trayectoria(db_path)
    base["trayectoria_base"] = trayectoria
    base["etiqueta"] = etiqueta_trayectoria(
        trayectoria, par["umbral_ascenso"], par["umbral_declive"])
    return base


def guardar_dinamica_actor(db_path: str, actor_id: int,
                           senales: dict, usuario: str) -> int:
    """Guarda las 7 señales dinámicas del actor y devuelve trayectoria_base."""
    vals = {k: max(-2, min(2, int(senales.get(k, 0)))) for k in _DIN_CAMPOS}
    trayectoria = sum(vals.values())
    set_clause = ", ".join(f"{k}=?" for k in _DIN_CAMPOS)
    with _conn(db_path) as c:
        c.execute(
            f"UPDATE config_actores SET {set_clause}, "
            "actualizado_en=datetime('now') WHERE id=?",
            (*[vals[k] for k in _DIN_CAMPOS], actor_id),
        )
        c.execute(
            "INSERT INTO config_actores_log "
            "(actor_id, actor_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "SELECT ?, nombre, ?, NULL, ?, ?, ? FROM config_actores WHERE id=?",
            (actor_id, "dinamica", f"trayectoria_base={trayectoria}", usuario,
             "señales dinámicas (Capa 4) editadas", actor_id),
        )
    return trayectoria


def guardar_divergencia_tema(db_path: str, actor_id: int, tema: str,
                             divergencia: int) -> None:
    """Guarda la divergencia dinámica (-1/0/+1) de un actor-tema."""
    div = max(-1, min(1, int(divergencia)))
    with _conn(db_path) as c:
        c.execute(
            "UPDATE config_actor_temas SET divergencia_dinamica=? "
            "WHERE actor_id=? AND tema=?",
            (div, actor_id, tema),
        )


def listar_log_actores(db_path: str, actor_id: int = None,
                       limite: int = 100) -> list:
    """Devuelve el log de cambios de actores, filtrado por actor_id si se provee."""
    try:
        with _conn(db_path) as c:
            if actor_id:
                rows = c.execute(
                    "SELECT * FROM config_actores_log WHERE actor_id=? "
                    "ORDER BY id DESC LIMIT ?",
                    (actor_id, limite),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM config_actores_log ORDER BY id DESC LIMIT ?",
                    (limite,),
                ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_log_actores falló: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════
# PROYECCIÓN — Capa de futuro (30/60/90 días)
#   Proyección A: actividad mediática extrapolada por tendencia (sin quiebres).
#   Proyección B: gravedad actual + efectos de puntos de quiebre (sin tendencia).
# Solo LEE: globos_b del semáforo + las tablas de quiebres. No toca el motor.
# ═══════════════════════════════════════════════════════════════════════════

_PROY_HORIZONTES = [30, 60, 90]
_QUIEBRE_DIRECCIONES = {"sube", "baja", "no_toca"}
_QUIEBRE_INTENSIDADES = {"leve", "moderado", "fuerte"}
_QUIEBRE_DURACIONES = {"permanente", "temporal"}


def cargar_parametros_proyeccion(db_path: str) -> dict:
    """Parámetros editables de la proyección con defaults seguros."""
    defaults = {
        "alcance": 2.0, "decay_60d": 0.5, "decay_90d": 0.25,
        "pts_leve": 8.0, "pts_moderado": 18.0, "pts_fuerte": 30.0,
        "dilucion_dias": 30.0,
    }
    mapa = {
        "PROY_VEL_ALCANCE": "alcance",
        "PROY_DECAY_60D": "decay_60d",
        "PROY_DECAY_90D": "decay_90d",
        "QUIEBRE_PTS_LEVE": "pts_leve",
        "QUIEBRE_PTS_MODERADO": "pts_moderado",
        "QUIEBRE_PTS_FUERTE": "pts_fuerte",
        "QUIEBRE_DILUCION_DIAS": "dilucion_dias",
    }
    try:
        with _conn(db_path) as c:
            rows = c.execute(
                "SELECT clave, valor FROM config_parametros WHERE clave IN "
                "('PROY_VEL_ALCANCE','PROY_DECAY_60D','PROY_DECAY_90D',"
                "'QUIEBRE_PTS_LEVE','QUIEBRE_PTS_MODERADO','QUIEBRE_PTS_FUERTE',"
                "'QUIEBRE_DILUCION_DIAS')",
            ).fetchall()
        for r in rows:
            k = mapa.get(r["clave"])
            if k:
                try:
                    defaults[k] = float(r["valor"])
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        print(f"[config_loader] cargar_parametros_proyeccion falló: {e}")
    return defaults


def factor_tendencia(horizonte: int, par: dict) -> float:
    """FACTOR(H) acumulado de la amortiguación de tendencia (Proyección A).

    FACTOR(30) = ALCANCE · 1.0
    FACTOR(60) = ALCANCE · (1.0 + decay_60d)
    FACTOR(90) = ALCANCE · (1.0 + decay_60d + decay_90d)
    """
    alcance = par["alcance"]
    if horizonte <= 30:
        pesos = 1.0
    elif horizonte <= 60:
        pesos = 1.0 + par["decay_60d"]
    else:
        pesos = 1.0 + par["decay_60d"] + par["decay_90d"]
    return round(alcance * pesos, 4)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _pts_intensidad(intensidad: str, par: dict) -> float:
    return {
        "leve": par["pts_leve"],
        "moderado": par["pts_moderado"],
        "fuerte": par["pts_fuerte"],
    }.get(intensidad, par["pts_moderado"])


def efecto_quiebre_en_horizonte(efecto: dict, dias_hasta: int,
                                horizonte: int, par: dict) -> float:
    """Puntos (con signo) que un efecto de quiebre aporta a un horizonte H.

    efecto: {direccion, intensidad, duracion}
    dias_hasta: días entre hoy y la fecha del quiebre (puede ser negativo si ya pasó).
    Aplica solo si 0 <= dias_hasta <= horizonte (el quiebre cae DENTRO del horizonte).
    TEMPORAL: se diluye linealmente a 0 en dilucion_dias tras la fecha.
    PERMANENTE: se mantiene completo.
    """
    direccion = efecto.get("direccion", "no_toca")
    if direccion == "no_toca":
        return 0.0
    # El quiebre debe haber ocurrido en o antes del horizonte, y no en el pasado.
    if dias_hasta < 0 or dias_hasta > horizonte:
        return 0.0
    signo = 1.0 if direccion == "sube" else -1.0
    magnitud = _pts_intensidad(efecto.get("intensidad", "moderado"), par)
    if efecto.get("duracion", "permanente") == "temporal":
        dilucion = max(1.0, par["dilucion_dias"])
        dias_transcurridos = horizonte - dias_hasta   # días desde el quiebre al horizonte
        atenuacion = max(0.0, 1.0 - dias_transcurridos / dilucion)
    else:
        atenuacion = 1.0
    return round(signo * magnitud * atenuacion, 2)


def _dias_hasta(fecha_iso: str, hoy) -> int | None:
    """Días entre hoy (date) y una fecha ISO 'YYYY-MM-DD'. None si no parsea."""
    from datetime import date
    try:
        y, m, d = (int(x) for x in fecha_iso[:10].split("-"))
        return (date(y, m, d) - hoy).days
    except Exception:
        return None


def calcular_proyecciones(db_path: str, temas_datos: list, hoy=None) -> dict:
    """Calcula las proyecciones A y B para una lista de temas.

    temas_datos: [{tema, actividad, velocidad, gravedad}] tomado de globos_b.
    hoy: datetime.date (default = fecha de hoy). Inyectable para tests.

    Devuelve {
      par, horizontes,
      proyeccion_a: [{tema, hoy, h30, h60, h90}],          ← solo tendencia
      proyeccion_b: [{tema, base, h30, h60, h90,
                      efectos: {30:{base,quiebre,total}, 60:..., 90:...}}],
      quiebres: [resumen de quiebres activos que afectan B],
    }
    """
    from datetime import date
    if hoy is None:
        hoy = date.today()
    par = cargar_parametros_proyeccion(db_path)

    # Quiebres activos con sus efectos por tema
    quiebres = listar_puntos_quiebre(db_path, solo_activos=True, con_efectos=True)
    # Precalcular días-hasta por quiebre
    for q in quiebres:
        q["dias_hasta"] = _dias_hasta(q.get("fecha", ""), hoy)

    proy_a, proy_b = [], []
    for d in temas_datos:
        tema = d["tema"]
        act = float(d.get("actividad", 0.0))
        vel = float(d.get("velocidad", 0.0))
        grav = float(d.get("gravedad", 0.0))

        # ── Proyección A: tendencia pura, sin quiebres ──
        fila_a = {"tema": tema, "hoy": round(act, 1)}
        for h in _PROY_HORIZONTES:
            fila_a[f"h{h}"] = round(_clamp(act + vel * factor_tendencia(h, par)), 1)
        proy_a.append(fila_a)

        # ── Proyección B: gravedad actual + efectos de quiebre ──
        fila_b = {"tema": tema, "base": round(grav, 1), "efectos": {}}
        for h in _PROY_HORIZONTES:
            ajuste = 0.0
            detalle = []
            for q in quiebres:
                dh = q.get("dias_hasta")
                if dh is None:
                    continue
                ef = q["efectos"].get(tema)
                if not ef:
                    continue
                pts = efecto_quiebre_en_horizonte(ef, dh, h, par)
                if pts != 0.0:
                    ajuste += pts
                    detalle.append({"quiebre": q["nombre"], "pts": pts})
            total = round(_clamp(grav + ajuste), 1)
            fila_b["efectos"][h] = {
                "base": round(grav, 1),
                "quiebre": round(ajuste, 1),
                "total": total,
                "detalle": detalle,
            }
            fila_b[f"h{h}"] = total
        proy_b.append(fila_b)

    return {
        "par": par,
        "horizontes": _PROY_HORIZONTES,
        "proyeccion_a": proy_a,
        "proyeccion_b": proy_b,
        "quiebres": quiebres,
        "hoy": hoy.isoformat(),
    }


# ── CRUD de puntos de quiebre ────────────────────────────────────────────────

def listar_puntos_quiebre(db_path: str, pais: str = "PE",
                          solo_activos: bool = False,
                          con_efectos: bool = False) -> list:
    """Lista los puntos de quiebre. Con con_efectos=True añade {efectos: {tema: {...}}}."""
    try:
        with _conn(db_path) as c:
            q = "SELECT * FROM config_puntos_quiebre WHERE pais=?"
            args = [pais]
            if solo_activos:
                q += " AND activo=1"
            q += " ORDER BY fecha ASC"
            rows = [dict(r) for r in c.execute(q, args).fetchall()]
            if con_efectos:
                for r in rows:
                    efs = c.execute(
                        "SELECT tema, direccion, intensidad, duracion "
                        "FROM config_quiebre_efectos WHERE quiebre_id=?",
                        (r["id"],),
                    ).fetchall()
                    r["efectos"] = {e["tema"]: dict(e) for e in efs}
        return rows
    except Exception as e:
        print(f"[config_loader] listar_puntos_quiebre falló: {e}")
        return []


def obtener_punto_quiebre(db_path: str, quiebre_id: int) -> dict | None:
    """Devuelve un punto de quiebre con sus efectos por tema. None si no existe."""
    try:
        with _conn(db_path) as c:
            row = c.execute(
                "SELECT * FROM config_puntos_quiebre WHERE id=?", (quiebre_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            efs = c.execute(
                "SELECT tema, direccion, intensidad, duracion "
                "FROM config_quiebre_efectos WHERE quiebre_id=?",
                (quiebre_id,),
            ).fetchall()
            d["efectos"] = {e["tema"]: dict(e) for e in efs}
        return d
    except Exception as e:
        print(f"[config_loader] obtener_punto_quiebre falló: {e}")
        return None


def crear_punto_quiebre(db_path: str, datos: dict, usuario: str) -> dict:
    """Crea un punto de quiebre. Devuelve {ok, id}."""
    def _op(c: sqlite3.Connection) -> dict:
        c.execute(
            "INSERT INTO config_puntos_quiebre (nombre, fecha, pais, activo, notas) "
            "VALUES (?,?,?,?,?)",
            (datos["nombre"].strip(), datos["fecha"].strip(),
             datos.get("pais", "PE"), int(bool(datos.get("activo", 1))),
             datos.get("notas") or None),
        )
        qid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            "INSERT INTO config_quiebre_log "
            "(quiebre_id, quiebre_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "VALUES (?,?,'creado',NULL,?,?,?)",
            (qid, datos["nombre"].strip(), f"fecha={datos['fecha'].strip()}",
             usuario, "creación"),
        )
        return {"ok": True, "id": qid}
    return _ejecutar_con_reintentos(db_path, _op)


def actualizar_punto_quiebre(db_path: str, quiebre_id: int, datos: dict,
                             usuario: str, motivo: str = None) -> dict:
    """Actualiza nombre/fecha/notas de un quiebre. Devuelve {ok}."""
    def _op(c: sqlite3.Connection) -> dict:
        c.execute(
            "UPDATE config_puntos_quiebre SET nombre=?, fecha=?, notas=?, "
            "actualizado_en=datetime('now') WHERE id=?",
            (datos["nombre"].strip(), datos["fecha"].strip(),
             datos.get("notas") or None, quiebre_id),
        )
        c.execute(
            "INSERT INTO config_quiebre_log "
            "(quiebre_id, quiebre_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "VALUES (?,?,'editado',NULL,?,?,?)",
            (quiebre_id, datos["nombre"].strip(),
             f"fecha={datos['fecha'].strip()}", usuario, motivo or "edición"),
        )
        return {"ok": True}
    return _ejecutar_con_reintentos(db_path, _op)


def toggle_punto_quiebre(db_path: str, quiebre_id: int, usuario: str) -> dict:
    """Activa/desactiva un quiebre. Devuelve {ok, activo_nuevo}."""
    def _op(c: sqlite3.Connection) -> dict:
        row = c.execute(
            "SELECT nombre, activo FROM config_puntos_quiebre WHERE id=?",
            (quiebre_id,),
        ).fetchone()
        if not row:
            return {"ok": False}
        nuevo = 0 if row["activo"] else 1
        c.execute(
            "UPDATE config_puntos_quiebre SET activo=?, actualizado_en=datetime('now') "
            "WHERE id=?", (nuevo, quiebre_id),
        )
        c.execute(
            "INSERT INTO config_quiebre_log "
            "(quiebre_id, quiebre_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "VALUES (?,?,'activo',?,?,?,?)",
            (quiebre_id, row["nombre"], str(row["activo"]), str(nuevo),
             usuario, "toggle activo"),
        )
        return {"ok": True, "activo_nuevo": nuevo}
    return _ejecutar_con_reintentos(db_path, _op)


def eliminar_punto_quiebre(db_path: str, quiebre_id: int, usuario: str) -> dict:
    """Elimina un quiebre y sus efectos (CASCADE). Devuelve {ok}."""
    def _op(c: sqlite3.Connection) -> dict:
        row = c.execute(
            "SELECT nombre FROM config_puntos_quiebre WHERE id=?", (quiebre_id,)
        ).fetchone()
        nombre = row["nombre"] if row else f"#{quiebre_id}"
        c.execute("DELETE FROM config_puntos_quiebre WHERE id=?", (quiebre_id,))
        c.execute(
            "INSERT INTO config_quiebre_log "
            "(quiebre_id, quiebre_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "VALUES (?,?,'eliminado',?,NULL,?,?)",
            (quiebre_id, nombre, nombre, usuario, "eliminación"),
        )
        return {"ok": True}
    return _ejecutar_con_reintentos(db_path, _op)


def guardar_efectos_quiebre(db_path: str, quiebre_id: int,
                            efectos: dict, usuario: str) -> dict:
    """Reemplaza los efectos por tema de un quiebre.

    efectos: {tema: {direccion, intensidad, duracion}}. Temas con direccion
    'no_toca' se guardan igual (registro explícito de que no afecta).
    Devuelve {ok, n}.
    """
    def _op(c: sqlite3.Connection) -> dict:
        n = 0
        for tema, ef in efectos.items():
            direccion = ef.get("direccion", "no_toca")
            intensidad = ef.get("intensidad", "moderado")
            duracion = ef.get("duracion", "permanente")
            if direccion not in _QUIEBRE_DIRECCIONES:
                direccion = "no_toca"
            if intensidad not in _QUIEBRE_INTENSIDADES:
                intensidad = "moderado"
            if duracion not in _QUIEBRE_DURACIONES:
                duracion = "permanente"
            c.execute(
                "INSERT INTO config_quiebre_efectos "
                "(quiebre_id, tema, direccion, intensidad, duracion) VALUES (?,?,?,?,?) "
                "ON CONFLICT(quiebre_id, tema) DO UPDATE SET "
                "direccion=excluded.direccion, intensidad=excluded.intensidad, "
                "duracion=excluded.duracion",
                (quiebre_id, tema, direccion, intensidad, duracion),
            )
            n += 1
        c.execute(
            "INSERT INTO config_quiebre_log "
            "(quiebre_id, quiebre_nombre, campo, valor_anterior, valor_nuevo, usuario, motivo) "
            "SELECT ?, nombre, 'efectos', NULL, ?, ?, ? FROM config_puntos_quiebre WHERE id=?",
            (quiebre_id, f"{n} temas", usuario, "efectos por tema editados", quiebre_id),
        )
        return {"ok": True, "n": n}
    return _ejecutar_con_reintentos(db_path, _op)


def listar_log_quiebres(db_path: str, quiebre_id: int = None,
                        limite: int = 100) -> list:
    """Log de cambios de puntos de quiebre."""
    try:
        with _conn(db_path) as c:
            if quiebre_id:
                rows = c.execute(
                    "SELECT * FROM config_quiebre_log WHERE quiebre_id=? "
                    "ORDER BY id DESC LIMIT ?", (quiebre_id, limite),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM config_quiebre_log ORDER BY id DESC LIMIT ?",
                    (limite,),
                ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[config_loader] listar_log_quiebres falló: {e}")
        return []
