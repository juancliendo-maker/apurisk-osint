"""APURISK 1.0 — Servidor web para deploy 24/7.

Expone:
  GET  /                         → Redirige al dashboard
  GET  /dashboard                → dashboard.html actualizado automáticamente
  GET  /api/snapshot             → JSON del último snapshot
  GET  /api/buscar?keyword=...   → Búsqueda en archivo histórico SQLite
  GET  /api/reporte/{tipo}/{formato} → Genera y descarga reporte on-demand
                                     tipo:    ejecutivo | 24h | alertas | semanal | diario
                                     formato: pdf | docx | html
  GET  /api/refresh              → Forza re-ejecución manual del pipeline
  GET  /api/status               → Estado del scheduler y métricas
  GET  /healthz                  → Health check (para Render/K8s)

Background:
  El scheduler ejecuta el pipeline cada REFRESH_SECONDS (default 1800 = 30 min).
  En cada ciclo: recolecta → analiza → archiva en SQLite → regenera dashboard.

Variables de entorno:
  TWITTER_BEARER_TOKEN  - Token X API v2 (opcional, activa Twitter live)
  REFRESH_SECONDS       - Intervalo del scheduler (default 1800)
  PORT                  - Puerto (default 8080, Render setea automáticamente)
  OUTPUT_DIR            - Directorio output (default ./output)

Uso local:
  pip install -r requirements-server.txt
  uvicorn apurisk.server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations
import os
import json
import sys
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    from .utils.timezone_pe import now_pe, now_pe_iso
    from .storage import ApuriskArchive
    from .main import run_once as pipeline_run_once
    from .reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apurisk.utils.timezone_pe import now_pe, now_pe_iso
    from apurisk.storage import ApuriskArchive
    from apurisk.main import run_once as pipeline_run_once
    from apurisk.reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
    )


# Configuración
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "1800"))  # 30 min default
PORT = int(os.getenv("PORT", "8080"))
SERVER_VERSION = "1.0.0"

app = FastAPI(
    title="APURISK 1.0 — OSINT Riesgos Políticos del Perú",
    description=(
        "Plataforma de monitoreo en tiempo real. Auto-refresh cada "
        f"{REFRESH_SECONDS//60} minutos. Genera reportes on-demand en PDF, DOCX y HTML."
    ),
    version=SERVER_VERSION,
)

# Servir archivos estáticos del dashboard (HTML, JSON, PDFs, DOCX)
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


# Estado interno del scheduler
_state = {
    "scheduler_running": False,
    "last_run": None,
    "last_run_iso": None,
    "next_run_iso": None,
    "total_runs": 0,
    "errors": 0,
    "last_error": None,
}


# ======================================================================
# Background Scheduler
# ======================================================================
async def _scheduler_loop():
    """Loop infinito que ejecuta el pipeline cada REFRESH_SECONDS segundos."""
    import argparse
    print(f"[scheduler] iniciado · ciclo cada {REFRESH_SECONDS}s")
    _state["scheduler_running"] = True
    while True:
        try:
            print(f"[scheduler] ejecutando pipeline a las {now_pe_iso()}")
            args = argparse.Namespace(
                live=True, demo=False, config=None, watch=0, once=True
            )
            # run_once es síncrono → ejecutar en thread pool
            await asyncio.get_event_loop().run_in_executor(None, pipeline_run_once, args)
            _state["last_run"] = now_pe()
            _state["last_run_iso"] = now_pe_iso()
            _state["total_runs"] += 1
            _state["last_error"] = None
            from datetime import timedelta
            next_run = now_pe() + timedelta(seconds=REFRESH_SECONDS)
            _state["next_run_iso"] = next_run.isoformat(timespec="seconds")
            print(f"[scheduler] OK — total runs: {_state['total_runs']}")
        except Exception as e:
            _state["errors"] += 1
            _state["last_error"] = str(e)
            print(f"[scheduler] ERROR: {e}")
        await asyncio.sleep(REFRESH_SECONDS)


@app.on_event("startup")
async def _startup():
    # Lanzar el scheduler como tarea background
    asyncio.create_task(_scheduler_loop())


# ======================================================================
# Endpoints
# ======================================================================
@app.get("/", response_class=HTMLResponse)
async def root():
    """Redirige a /dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Sirve el dashboard.html más reciente."""
    dash = OUTPUT_DIR / "dashboard.html"
    if not dash.exists():
        return HTMLResponse(
            content=(
                "<html><body style='font-family: sans-serif; padding: 40px;'>"
                "<h1>APURISK 1.0 está iniciando…</h1>"
                "<p>El primer ciclo del scheduler aún no completa. "
                "Recarga en unos segundos.</p>"
                "<p><a href='/api/status'>Ver estado del scheduler</a></p>"
                "</body></html>"
            ),
            status_code=503,
        )
    return HTMLResponse(content=dash.read_text(encoding="utf-8"))


@app.get("/api/status")
async def status():
    """Estado del sistema y scheduler."""
    archive_path = OUTPUT_DIR / "apurisk_archive.db"
    archive_stats = {}
    if archive_path.exists():
        try:
            archive = ApuriskArchive(str(archive_path))
            archive_stats = archive.stats()
        except Exception as e:
            archive_stats = {"error": str(e)}

    snap_path = _ultimo_snapshot_path()
    snap_summary = None
    if snap_path:
        with open(snap_path, encoding="utf-8") as f:
            d = json.load(f)
        snap_summary = {
            "generado": d.get("generado"),
            "score_global": d.get("riesgo", {}).get("global"),
            "nivel": d.get("riesgo", {}).get("nivel"),
            "alertas_total": len(d.get("alertas", [])),
            "alertas_criticas": len([a for a in d.get("alertas", []) if a.get("nivel") == "CRÍTICA"]),
            "n_articulos_24h": d.get("n_articulos_24h"),
            "n_tweets": d.get("n_tweets"),
        }

    return {
        "service": "APURISK 1.0 — OSINT Perú",
        "version": SERVER_VERSION,
        "now": now_pe_iso(),
        "refresh_seconds": REFRESH_SECONDS,
        "scheduler": {
            "running": _state["scheduler_running"],
            "total_runs": _state["total_runs"],
            "errors": _state["errors"],
            "last_run": _state["last_run_iso"],
            "next_run": _state["next_run_iso"],
            "last_error": _state["last_error"],
        },
        "archive": archive_stats,
        "snapshot_actual": snap_summary,
    }


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    """Health check para Render/K8s/load balancers/UptimeRobot.
    Acepta GET y HEAD (UptimeRobot Free usa HEAD por default)."""
    return {"status": "ok", "now": now_pe_iso()}


@app.get("/api/snapshot")
async def snapshot_json():
    """Devuelve el snapshot JSON más reciente."""
    snap_path = _ultimo_snapshot_path()
    if not snap_path:
        raise HTTPException(status_code=503, detail="Aún no hay snapshot disponible")
    with open(snap_path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/refresh")
async def refresh():
    """Forza re-ejecución manual del pipeline (no espera al ciclo)."""
    import argparse
    args = argparse.Namespace(live=True, demo=False, config=None, watch=0, once=True)
    try:
        await asyncio.get_event_loop().run_in_executor(None, pipeline_run_once, args)
        _state["last_run_iso"] = now_pe_iso()
        return {"status": "ok", "generado": now_pe_iso()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/buscar")
async def buscar(
    keyword: Optional[str] = Query(None, description="Palabra clave"),
    region: Optional[str] = Query(None),
    fuente: Optional[str] = Query(None, description="source_id"),
    nivel: Optional[str] = Query(None, description="CRÍTICA | ALTA | MEDIA"),
    desde: Optional[str] = Query(None, description="ISO YYYY-MM-DD"),
    hasta: Optional[str] = Query(None, description="ISO YYYY-MM-DD"),
    tipo: str = Query("articulos", description="articulos | alertas | persistentes | score"),
    limit: int = Query(50, ge=1, le=500),
    dias: int = Query(7, ge=1, le=90),
    min_dias: int = Query(2, ge=1),
):
    """Búsqueda en archivo histórico SQLite."""
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="Archivo histórico aún no disponible")
    archive = ApuriskArchive(str(db_path))
    if tipo == "alertas":
        rows = archive.search_alertas(nivel=nivel, keyword=keyword, desde=desde, hasta=hasta, limit=limit)
    elif tipo == "persistentes":
        rows = archive.alertas_persistentes(dias=dias, min_dias=min_dias)
    elif tipo == "score":
        rows = archive.serie_temporal_score(dias=dias)
    else:
        rows = archive.search_articulos(
            keyword=keyword, region=region, source_id=fuente,
            desde=desde, hasta=hasta, limit=limit,
        )
    return {"tipo": tipo, "count": len(rows), "results": rows}


@app.get("/api/reporte/{tipo}/{formato}")
async def generar_reporte(tipo: str, formato: str):
    """Genera y devuelve un reporte on-demand.

    tipo:    ejecutivo | 24h | alertas | semanal | diario
    formato: pdf | docx | html
    """
    snap_path = _ultimo_snapshot_path()
    if not snap_path:
        raise HTTPException(status_code=503, detail="Sin snapshot disponible. Espera el primer ciclo.")
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)

    ts = now_pe().strftime("%Y%m%d_%H%M")
    filename = f"reporte_{tipo}_{ts}.{formato}"
    salida = OUTPUT_DIR / filename

    try:
        if tipo == "ejecutivo" and formato == "pdf":
            generar_ejecutivo_pdf(str(salida), snap, str(OUTPUT_DIR))
        elif tipo == "ejecutivo" and formato == "docx":
            generar_ejecutivo_docx(str(salida), snap, str(OUTPUT_DIR))
        elif tipo == "diario" and formato == "pdf":
            generar_reporte_diario_pdf(str(salida), snap)
        elif tipo == "semanal" and formato == "pdf":
            generar_reporte_semanal_pdf(str(salida), str(OUTPUT_DIR))
        elif tipo == "24h" and formato == "html":
            arts = snap.get("articulos", [])
            confs = snap.get("conflictos", [])
            alertas_24 = [a for a in snap.get("alertas", []) if a.get("ventana_24h")]
            generar_reporte_24h_html(str(salida), arts, confs, alertas_24,
                                      snap.get("riesgo", {}), snap.get("matriz_riesgo", []),
                                      snap.get("modo", "live"))
        elif tipo == "24h" and formato == "docx":
            arts = snap.get("articulos", [])
            confs = snap.get("conflictos", [])
            alertas_24 = [a for a in snap.get("alertas", []) if a.get("ventana_24h")]
            generar_reporte_24h_docx(str(salida), arts, confs, alertas_24,
                                      snap.get("riesgo", {}), snap.get("matriz_riesgo", []),
                                      snap.get("modo", "live"))
        elif tipo == "alertas" and formato == "html":
            generar_alertas_html(str(salida), snap.get("alertas", []), snap.get("modo", "live"))
        elif tipo == "alertas" and formato == "docx":
            generar_alertas_docx(str(salida), snap.get("alertas", []), snap.get("modo", "live"))
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Combinación no soportada: tipo='{tipo}' formato='{formato}'. "
                       f"Use ejecutivo/{pdf|docx}, 24h/{html|docx}, alertas/{html|docx}, "
                       f"diario/pdf, semanal/pdf."
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando reporte: {e}")

    media_types = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "html": "text/html"}
    return FileResponse(
        path=str(salida),
        media_type=media_types.get(formato, "application/octet-stream"),
        filename=filename,
    )


# ======================================================================
# Helpers
# ======================================================================
def _ultimo_snapshot_path() -> Optional[Path]:
    snaps = sorted(OUTPUT_DIR.glob("apurisk_snapshot_*.json"))
    return snaps[-1] if snaps else None


# Para correr local con: python -m apurisk.server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apurisk.server:app", host="0.0.0.0", port=PORT, reload=False)
