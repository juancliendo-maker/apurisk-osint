"""Archive SQLite — Base de datos OSINT persistente.

Almacena todas las recolecciones para que la plataforma sea una BASE DE DATOS
HISTÓRICA consultable. Cada nueva ejecución acumula sin sobrescribir.

Esquema:
  - snapshots: cada ciclo de monitoreo (score, modo, métricas)
  - articulos: items recolectados (medios, RSS, GDELT, twitter, conflictos, PL)
  - alertas: alertas disparadas (con timestamp, regla, nivel, URL)
  - factores: matriz P×I por snapshot (para análisis temporal)

Permite consultas:
  - by_date_range(desde, hasta)
  - search(keyword)
  - by_source / by_region / by_categoria
  - persistencia_alertas (mismo título en múltiples días)
  - serie_temporal_score
"""
from __future__ import annotations
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generado TEXT NOT NULL,
    modo TEXT,
    score_global REAL,
    nivel TEXT,
    sentimiento REAL,
    n_articulos INTEGER,
    n_articulos_24h INTEGER,
    n_conflictos INTEGER,
    n_proyectos INTEGER,
    n_tweets INTEGER,
    UNIQUE(generado)
);

CREATE TABLE IF NOT EXISTS articulos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE CASCADE,
    source_id TEXT,
    source_name TEXT,
    category TEXT,
    title TEXT,
    summary TEXT,
    url TEXT,
    published TEXT,
    region TEXT,
    criticidad TEXT,
    capturado_en TEXT,
    UNIQUE(url, title)
);

CREATE INDEX IF NOT EXISTS idx_articulos_published ON articulos(published);
CREATE INDEX IF NOT EXISTS idx_articulos_source ON articulos(source_id);
CREATE INDEX IF NOT EXISTS idx_articulos_region ON articulos(region);

CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE CASCADE,
    nivel TEXT,
    categoria TEXT,
    regla TEXT,
    titulo TEXT,
    resumen TEXT,
    fuente TEXT,
    url TEXT,
    region TEXT,
    timestamp TEXT,
    accion TEXT,
    UNIQUE(snapshot_id, regla, titulo)
);

CREATE INDEX IF NOT EXISTS idx_alertas_titulo ON alertas(titulo);
CREATE INDEX IF NOT EXISTS idx_alertas_nivel ON alertas(nivel);
CREATE INDEX IF NOT EXISTS idx_alertas_timestamp ON alertas(timestamp);

CREATE TABLE IF NOT EXISTS factores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE CASCADE,
    factor_id TEXT,
    nombre TEXT,
    categoria TEXT,
    probabilidad INTEGER,
    impacto INTEGER,
    score REAL,
    nivel TEXT,
    tendencia TEXT,
    menciones_24h INTEGER
);

CREATE INDEX IF NOT EXISTS idx_factores_factor_id ON factores(factor_id);
CREATE INDEX IF NOT EXISTS idx_factores_score ON factores(score);

-- Reportes de caso (minera, gobierno, etc.) — generados semanalmente
-- y consolidados mensualmente. Almacena metadata + ruta PDF + JSON resumido
-- para búsqueda histórica y consolidación.
CREATE TABLE IF NOT EXISTS reportes_caso (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_generacion TEXT NOT NULL,
    plantilla TEXT NOT NULL,        -- minera | gobierno | minera_mensual | gobierno_mensual
    cliente TEXT,                    -- empresa o entidad solicitante
    solicitante TEXT,
    periodo TEXT,                    -- ej. "12/05/2026 — 19/05/2026"
    semana_iso INTEGER,
    mes INTEGER,
    año INTEGER,
    score_global REAL,
    nivel TEXT,
    pdf_path TEXT,                   -- ruta al PDF generado
    json_resumen TEXT,               -- JSON con resumen ejecutivo para búsqueda
    metadata TEXT,                   -- JSON con parámetros completos del caso
    UNIQUE(plantilla, cliente, semana_iso, año)
);

CREATE INDEX IF NOT EXISTS idx_reportes_plantilla ON reportes_caso(plantilla);
CREATE INDEX IF NOT EXISTS idx_reportes_cliente ON reportes_caso(cliente);
CREATE INDEX IF NOT EXISTS idx_reportes_fecha ON reportes_caso(fecha_generacion);
CREATE INDEX IF NOT EXISTS idx_reportes_año_mes ON reportes_caso(año, mes);
CREATE INDEX IF NOT EXISTS idx_reportes_semana ON reportes_caso(semana_iso, año);

-- =================================================================
-- Sprint 1.1 · Score Engine v2 (validación paralela)
-- Tabla para correr v1 y v2 en paralelo durante 7 días y comparar
-- antes de activar v2 oficialmente como motor de scoring.
-- =================================================================
CREATE TABLE IF NOT EXISTS scores_paralelos (
    fecha           TEXT PRIMARY KEY,           -- YYYY-MM-DD
    generado_en     TEXT NOT NULL,              -- ISO timestamp de cálculo

    -- Sistema v1 (legacy)
    score_v1        REAL,                       -- 0-100
    nivel_v1        TEXT,                       -- BAJO/MEDIO/ALTO

    -- Sistema v2 (nuevo)
    score_v2        REAL,                       -- 0-100 (score nacional general)
    nivel_v2        TEXT,                       -- semáforo 5 niveles
    score_v2_24h    REAL,                       -- riesgo táctico 24h
    score_v2_7d     REAL,                       -- presión coyuntural 7d
    score_v2_30d    REAL,                       -- tendencia operativa 30d
    score_v2_90d    REAL,                       -- riesgo estratégico 90d
    confidence_v2   REAL,                       -- 0-100 confianza analítica

    sub_scores_v2   TEXT,                       -- JSON con 5 dimensiones
    modificadores_v2 TEXT,                      -- JSON con detalle de factores

    -- Comparación
    delta_v2_v1     REAL,                       -- score_v2 - score_v1
    explicacion     TEXT,                       -- breve LLM del porqué de la diferencia

    -- Revisión humana (durante validación 7 días)
    revision_humana TEXT,                       -- nota del analista
    revision_decision TEXT,                     -- 'aprobado' | 'rechazado' | 'pendiente'
    revision_fecha  TEXT
);

CREATE INDEX IF NOT EXISTS idx_scores_paralelos_fecha ON scores_paralelos(fecha);
CREATE INDEX IF NOT EXISTS idx_scores_paralelos_decision ON scores_paralelos(revision_decision);
"""


class ApuriskArchive:
    """Wrapper de SQLite para persistencia de datos OSINT."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Si el filesystem no soporta SQLite locking (algunos FUSE mounts),
        # caemos a una DB en el directorio temporal del sistema y la copiamos.
        try:
            self._init_schema()
        except sqlite3.OperationalError as e:
            if "disk I/O" in str(e) or "locked" in str(e).lower():
                import tempfile
                fallback = Path(tempfile.gettempdir()) / "apurisk_archive.db"
                print(f"  [info] SQLite en {db_path} no soporta locks, usando fallback: {fallback}")
                self.db_path = str(fallback)
                self._fallback_active = True
                self._original_path = db_path
                self._init_schema()
            else:
                raise

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ============== Inserción ==============
    def archivar_snapshot(self, snapshot: dict) -> int:
        """Persiste un snapshot completo (con artículos, alertas y factores)."""
        riesgo = snapshot.get("riesgo", {})
        with self._conn() as c:
            cur = c.cursor()
            # Insert snapshot (idempotente por timestamp)
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO snapshots
                       (generado, modo, score_global, nivel, sentimiento,
                        n_articulos, n_articulos_24h, n_conflictos, n_proyectos, n_tweets)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot.get("generado"),
                        snapshot.get("modo"),
                        riesgo.get("global"),
                        riesgo.get("nivel"),
                        riesgo.get("sentimiento_promedio"),
                        snapshot.get("n_articulos", 0),
                        snapshot.get("n_articulos_24h", 0),
                        snapshot.get("n_conflictos", 0),
                        snapshot.get("n_proyectos", 0),
                        snapshot.get("n_tweets", 0),
                    ),
                )
            except sqlite3.IntegrityError:
                pass

            cur.execute("SELECT id FROM snapshots WHERE generado=?", (snapshot.get("generado"),))
            row = cur.fetchone()
            if not row:
                return -1
            snap_id = row["id"]

            now_capturado = datetime.now().isoformat(timespec="seconds")

            # Articulos (de varias fuentes)
            for a in snapshot.get("articulos", []):
                try:
                    cur.execute(
                        """INSERT OR IGNORE INTO articulos
                           (snapshot_id, source_id, source_name, category, title, summary,
                            url, published, region, criticidad, capturado_en)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (snap_id, a.get("source_id"), a.get("source_name"), a.get("category"),
                         a.get("title"), a.get("summary"), a.get("url"), a.get("published"),
                         a.get("region"), a.get("criticidad"), now_capturado)
                    )
                except sqlite3.IntegrityError:
                    pass

            for c_ in snapshot.get("conflictos", []):
                try:
                    raw = c_.get("raw", {}) or {}
                    cur.execute(
                        """INSERT OR IGNORE INTO articulos
                           (snapshot_id, source_id, source_name, category, title, summary,
                            url, published, region, criticidad, capturado_en)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (snap_id, c_.get("source_id"), c_.get("source_name"), "estado",
                         c_.get("title"), c_.get("summary"), c_.get("url"), c_.get("published"),
                         c_.get("region") or raw.get("region"),
                         c_.get("criticidad") or raw.get("severidad"), now_capturado)
                    )
                except sqlite3.IntegrityError:
                    pass

            # Alertas
            for a in snapshot.get("alertas", []):
                try:
                    cur.execute(
                        """INSERT OR IGNORE INTO alertas
                           (snapshot_id, nivel, categoria, regla, titulo, resumen, fuente, url,
                            region, timestamp, accion)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (snap_id, a.get("nivel"), a.get("categoria"), a.get("regla"),
                         a.get("titulo"), a.get("resumen"), a.get("fuente"),
                         a.get("url"), a.get("region"), a.get("timestamp"), a.get("accion"))
                    )
                except sqlite3.IntegrityError:
                    pass

            # Factores
            for f in snapshot.get("matriz_riesgo", []):
                cur.execute(
                    """INSERT INTO factores
                       (snapshot_id, factor_id, nombre, categoria, probabilidad, impacto,
                        score, nivel, tendencia, menciones_24h)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (snap_id, f.get("id"), f.get("nombre"), f.get("categoria"),
                     f.get("probabilidad"), f.get("impacto"), f.get("score"),
                     f.get("nivel"), f.get("tendencia"), f.get("menciones_24h", 0))
                )

            return snap_id

    # ============== Consulta ==============
    def stats(self) -> dict:
        with self._conn() as c:
            n_snap = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            n_art = c.execute("SELECT COUNT(*) FROM articulos").fetchone()[0]
            n_alert = c.execute("SELECT COUNT(*) FROM alertas").fetchone()[0]
            n_fac = c.execute("SELECT COUNT(*) FROM factores").fetchone()[0]
            primer = c.execute("SELECT MIN(generado) FROM snapshots").fetchone()[0]
            ultimo = c.execute("SELECT MAX(generado) FROM snapshots").fetchone()[0]
        return {"snapshots": n_snap, "articulos": n_art, "alertas": n_alert,
                "factores": n_fac, "primer": primer, "ultimo": ultimo}

    def search_articulos(self, keyword: str = None, region: str = None,
                          source_id: str = None, desde: str = None, hasta: str = None,
                          limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM articulos WHERE 1=1"
        params = []
        if keyword:
            sql += " AND (title LIKE ? OR summary LIKE ?)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        if region:
            sql += " AND region=?"
            params.append(region)
        if source_id:
            sql += " AND source_id=?"
            params.append(source_id)
        if desde:
            sql += " AND published >= ?"
            params.append(desde)
        if hasta:
            sql += " AND published <= ?"
            params.append(hasta)
        sql += " ORDER BY published DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            return [dict(row) for row in c.execute(sql, params).fetchall()]

    def search_alertas(self, nivel: str = None, regla: str = None,
                        keyword: str = None, desde: str = None, hasta: str = None,
                        limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM alertas WHERE 1=1"
        params = []
        if nivel:
            sql += " AND nivel=?"
            params.append(nivel)
        if regla:
            sql += " AND regla=?"
            params.append(regla)
        if keyword:
            sql += " AND (titulo LIKE ? OR resumen LIKE ?)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        if desde:
            sql += " AND timestamp >= ?"
            params.append(desde)
        if hasta:
            sql += " AND timestamp <= ?"
            params.append(hasta)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            return [dict(row) for row in c.execute(sql, params).fetchall()]

    def serie_temporal_score(self, dias: int = 30) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT generado, score_global, nivel, n_articulos_24h,
                          n_conflictos
                   FROM snapshots
                   WHERE generado >= datetime('now', ?)
                   ORDER BY generado""",
                (f'-{dias} days',)
            ).fetchall()
        return [dict(r) for r in rows]

    def alertas_persistentes(self, dias: int = 7, min_dias: int = 2) -> list[dict]:
        """Devuelve alertas que aparecen en >= min_dias diferentes."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT
                       titulo,
                       MAX(nivel) AS nivel,
                       MAX(categoria) AS categoria,
                       MAX(regla) AS regla,
                       MAX(url) AS url,
                       COUNT(*) AS ocurrencias,
                       COUNT(DISTINCT date(timestamp)) AS dias_activo,
                       MIN(timestamp) AS primera,
                       MAX(timestamp) AS ultima
                   FROM alertas
                   WHERE timestamp IS NOT NULL
                     AND timestamp >= datetime('now', ?)
                   GROUP BY titulo
                   HAVING dias_activo >= ?
                   ORDER BY dias_activo DESC, ocurrencias DESC""",
                (f'-{dias} days', min_dias)
            ).fetchall()
        return [dict(r) for r in rows]


    def export_to(self, dest_path: str):
        """Copia la DB activa a otro path (útil cuando se usa fallback)."""
        import shutil
        try:
            shutil.copy2(self.db_path, dest_path)
            return True
        except Exception:
            return False

    # ============== REPORTES DE CASO (minera, gobierno) ==============
    def archivar_reporte_caso(self, reporte_meta: dict, pdf_path: str,
                                json_resumen: dict, parametros: dict) -> int:
        """Registra un reporte generado en la base de datos.

        Args:
            reporte_meta: dict del 'metadata' del análisis
            pdf_path: ruta al PDF generado
            json_resumen: dict con resumen ejecutivo (sección 1)
            parametros: parámetros del caso (cliente, departamentos, etc.)
        Returns:
            id del reporte registrado
        """
        with self._conn() as c:
            cur = c.cursor()
            cur.execute(
                """INSERT OR REPLACE INTO reportes_caso
                   (fecha_generacion, plantilla, cliente, solicitante, periodo,
                    semana_iso, mes, año, score_global, nivel,
                    pdf_path, json_resumen, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    reporte_meta.get("generado"),
                    reporte_meta.get("tipo", "desconocido"),
                    reporte_meta.get("empresa", "—"),
                    reporte_meta.get("solicitante", "—"),
                    reporte_meta.get("periodo", "—"),
                    int(reporte_meta.get("semana_iso", 0)),
                    int(reporte_meta.get("mes", 0)),
                    int(reporte_meta.get("año", 0)),
                    float(json_resumen.get("score_global", 0)),
                    json_resumen.get("nivel", "—"),
                    pdf_path,
                    json.dumps(json_resumen, ensure_ascii=False, default=str),
                    json.dumps(parametros, ensure_ascii=False, default=str),
                ),
            )
            return cur.lastrowid or -1

    def listar_reportes(self, plantilla: str = None, cliente: str = None,
                          año: int = None, mes: int = None,
                          limit: int = 100) -> list[dict]:
        """Lista reportes archivados con filtros opcionales."""
        sql = "SELECT * FROM reportes_caso WHERE 1=1"
        params = []
        if plantilla:
            sql += " AND plantilla = ?"
            params.append(plantilla)
        if cliente:
            sql += " AND cliente LIKE ?"
            params.append(f"%{cliente}%")
        if año:
            sql += " AND año = ?"
            params.append(año)
        if mes:
            sql += " AND mes = ?"
            params.append(mes)
        sql += " ORDER BY fecha_generacion DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def buscar_reportes(self, keyword: str, plantilla: str = None,
                          limit: int = 50) -> list[dict]:
        """Busca reportes por keyword en el json_resumen."""
        sql = ("SELECT * FROM reportes_caso WHERE "
                "(cliente LIKE ? OR json_resumen LIKE ?)")
        params = [f"%{keyword}%", f"%{keyword}%"]
        if plantilla:
            sql += " AND plantilla = ?"
            params.append(plantilla)
        sql += " ORDER BY fecha_generacion DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def reportes_de_mes(self, año: int, mes: int,
                          plantilla: str = "riesgo_minera_semanal") -> list[dict]:
        """Reportes semanales del mes especificado, para consolidación mensual."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM reportes_caso
                   WHERE año = ? AND mes = ? AND plantilla = ?
                   ORDER BY semana_iso ASC""",
                (año, mes, plantilla)
            ).fetchall()
        return [dict(r) for r in rows]

    def ultimo_reporte_semanal(self, plantilla: str, cliente: str = None) -> dict | None:
        """Devuelve el reporte semanal más reciente para un cliente/plantilla."""
        sql = "SELECT * FROM reportes_caso WHERE plantilla = ?"
        params = [plantilla]
        if cliente:
            sql += " AND cliente = ?"
            params.append(cliente)
        sql += " ORDER BY fecha_generacion DESC LIMIT 1"
        with self._conn() as c:
            row = c.execute(sql, params).fetchone()
        return dict(row) if row else None

    def stats_reportes(self) -> dict:
        """Métricas agregadas de reportes archivados."""
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM reportes_caso").fetchone()[0]
            por_plantilla = c.execute(
                "SELECT plantilla, COUNT(*) AS n FROM reportes_caso GROUP BY plantilla"
            ).fetchall()
            ultimo = c.execute(
                "SELECT MAX(fecha_generacion) FROM reportes_caso"
            ).fetchone()[0]
        return {
            "total_reportes": total,
            "por_plantilla": {r["plantilla"]: r["n"] for r in por_plantilla},
            "ultimo_reporte": ultimo,
        }


def archive_from_outputs(outputs_dir: str, db_path: str = None) -> dict:
    """Importa todos los snapshots JSON existentes en outputs/ al SQLite.

    Función one-time para migrar datos previos. Idempotente.
    """
    if db_path is None:
        db_path = str(Path(outputs_dir) / "apurisk_archive.db")
    archive = ApuriskArchive(db_path)
    p = Path(outputs_dir)
    n = 0
    for f in sorted(p.glob("apurisk_snapshot_*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            archive.archivar_snapshot(data)
            n += 1
        except Exception as e:
            print(f"  [warn] {f.name}: {e}")
    return archive.stats()
