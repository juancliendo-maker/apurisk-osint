"""APURISK · web/routes_dashboard — Núcleo: dashboard, status, snapshot, refresh."""
from __future__ import annotations
import json
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .core import (
    OUTPUT_DIR, SERVER_VERSION, REFRESH_SECONDS, _state,
    _ultimo_snapshot_path, ApuriskArchive,
)

try:
    from ..utils.timezone_pe import now_pe_iso
    from ..main import run_once as pipeline_run_once
except ImportError:
    from apurisk.utils.timezone_pe import now_pe_iso
    from apurisk.main import run_once as pipeline_run_once

router = APIRouter()

# ======================================================================
# Endpoints
# ======================================================================
@router.get("/", response_class=HTMLResponse)
async def root():
    """Redirige a /dashboard."""
    return RedirectResponse(url="/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Sirve el dashboard.html más reciente."""
    dash = OUTPUT_DIR / "dashboard.html"
    if not dash.exists():
        return HTMLResponse(
            content=(
                "<html><body style='font-family: sans-serif; padding: 40px;'>"
                "<h1>APURISK OSINT está iniciando…</h1>"
                "<p>El primer ciclo del scheduler aún no completa. "
                "Recarga en unos segundos.</p>"
                "<p><a href='/api/status'>Ver estado del scheduler</a></p>"
                "</body></html>"
            ),
            status_code=503,
        )
    return HTMLResponse(content=dash.read_text(encoding="utf-8"))


@router.get("/api/status")
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
        "service": "APURISK OSINT — Strategic Intelligence for Complex Decisions",
        "powered_by": "THALOS",
        "future_product": "APURISK SIM-CRISIS",
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


@router.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    """Health check para Render/K8s/load balancers/UptimeRobot.
    Acepta GET y HEAD (UptimeRobot Free usa HEAD por default)."""
    return {"status": "ok", "now": now_pe_iso()}


@router.get("/api/snapshot")
async def snapshot_json():
    """Devuelve el snapshot JSON más reciente."""
    snap_path = _ultimo_snapshot_path()
    if not snap_path:
        raise HTTPException(status_code=503, detail="Aún no hay snapshot disponible")
    with open(snap_path, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/refresh")
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
