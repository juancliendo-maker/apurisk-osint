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
