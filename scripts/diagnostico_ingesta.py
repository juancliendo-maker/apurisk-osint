#!/usr/bin/env python3
"""Diagnóstico de SOLO LECTURA de la ingesta de noticias.

Uso en el shell de Render (desde la raíz del repo):
    python scripts/diagnostico_ingesta.py

No escribe nada. Resuelve la BD igual que la app (APURISK_DB_PATH u
OUTPUT_DIR/apurisk_archive.db) y muestra:
  · cuándo entró el último artículo real (capturado_en y published)
  · conteo total de artículos en la BD
  · ingesta por día (últimos 8 días) para ver el drenaje
  · últimas corridas del pipeline (tabla snapshots) como proxy del scheduler
"""
import os, sqlite3
from pathlib import Path
from datetime import datetime, timedelta


def _db_path() -> str:
    p = os.environ.get("APURISK_DB_PATH")
    if p:
        return p
    out = os.environ.get("OUTPUT_DIR", "output")
    return str(Path(out) / "apurisk_archive.db")


def _scalar(c, sql, args=()):
    try:
        row = c.execute(sql, args).fetchone()
        return row[0] if row else None
    except Exception as e:
        return f"(error: {e})"


def main():
    db = _db_path()
    print("=" * 60)
    print("DIAGNÓSTICO DE INGESTA (solo lectura)")
    print("=" * 60)
    print(f"BD: {db}")
    if not Path(db).exists():
        print("!! La BD no existe en esa ruta. Revisa OUTPUT_DIR/APURISK_DB_PATH.")
        return
    print(f"Tamaño BD: {Path(db).stat().st_size/1_048_576:.1f} MB")
    print(f"Hora UTC ahora: {datetime.utcnow().isoformat()}")
    print()

    c = sqlite3.connect(db)

    # ── Artículos ──
    print("── ARTÍCULOS ──")
    total = _scalar(c, "SELECT COUNT(*) FROM articulos")
    print(f"Total de artículos en la BD: {total}")
    print(f"Último capturado_en (ingesta): {_scalar(c, 'SELECT MAX(capturado_en) FROM articulos')}")
    print(f"Último published (fecha nota): {_scalar(c, 'SELECT MAX(published) FROM articulos')}")
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    print(f"Capturados en últimas 24h:     {_scalar(c, 'SELECT COUNT(*) FROM articulos WHERE capturado_en >= ?', (cutoff,))}")
    print()

    print("Ingesta por día (capturado_en, últimos 8 días):")
    try:
        rows = c.execute(
            "SELECT substr(capturado_en,1,10) d, COUNT(*) n FROM articulos "
            "WHERE capturado_en >= ? GROUP BY d ORDER BY d DESC",
            ((datetime.utcnow() - timedelta(days=8)).isoformat(),),
        ).fetchall()
        if rows:
            for d, n in rows:
                print(f"   {d}: {n}")
        else:
            print("   (ningún artículo capturado en los últimos 8 días)")
    except Exception as e:
        print(f"   (error: {e})")
    print()

    # ── Snapshots (proxy del scheduler) ──
    print("── SNAPSHOTS / SCHEDULER (proxy persistente) ──")
    print(f"Total de snapshots: {_scalar(c, 'SELECT COUNT(*) FROM snapshots')}")
    print(f"Último snapshot generado: {_scalar(c, 'SELECT MAX(generado) FROM snapshots')}")
    print(f"Snapshots en últimas 24h: {_scalar(c, 'SELECT COUNT(*) FROM snapshots WHERE generado >= ?', (cutoff,))}")
    print()
    print("Últimos 6 snapshots (generado · n_articulos · n_articulos_24h):")
    try:
        for g, na, na24 in c.execute(
            "SELECT generado, n_articulos, n_articulos_24h FROM snapshots "
            "ORDER BY generado DESC LIMIT 6"
        ).fetchall():
            print(f"   {g} · total={na} · 24h={na24}")
    except Exception as e:
        print(f"   (error: {e})")
    print()

    # ── Alertas (contexto del conteo inflado) ──
    print("── ALERTAS (contexto) ──")
    print(f"Total de alertas: {_scalar(c, 'SELECT COUNT(*) FROM alertas')}")
    print(f"Alertas con timestamp en últimas 24h: {_scalar(c, 'SELECT COUNT(*) FROM alertas WHERE timestamp >= ?', (cutoff,))}")
    print(f"Snapshots distintos en esas alertas 24h: {_scalar(c, 'SELECT COUNT(DISTINCT snapshot_id) FROM alertas WHERE timestamp >= ?', (cutoff,))}")
    print(f"Títulos distintos en esas alertas 24h:   {_scalar(c, 'SELECT COUNT(DISTINCT titulo) FROM alertas WHERE timestamp >= ?', (cutoff,))}")
    c.close()
    print()
    print("LECTURA: si 'último capturado_en' es de hace >24h y los snapshots")
    print("siguen apareciendo recientes, el scheduler corre pero la recolección")
    print("trae 0. Si 'títulos distintos' << 'alertas 24h', el conteo está")
    print("inflado por re-inserción cada ciclo (UNIQUE por snapshot_id).")


if __name__ == "__main__":
    main()
