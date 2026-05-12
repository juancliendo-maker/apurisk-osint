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

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    from .utils.timezone_pe import now_pe, now_pe_iso
    from .storage import ApuriskArchive
    from .main import run_once as pipeline_run_once
    from .analyzers.caso_analyzer import analizar_caso
    from .reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
        generar_reporte_caso_pdf,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apurisk.utils.timezone_pe import now_pe, now_pe_iso
    from apurisk.storage import ApuriskArchive
    from apurisk.main import run_once as pipeline_run_once
    from apurisk.analyzers.caso_analyzer import analizar_caso
    from apurisk.reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
        generar_reporte_caso_pdf,
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
# ANÁLISIS DE CASO (input analista → reporte PDF)
# ======================================================================
@app.post("/api/analisis-caso")
async def analisis_caso_post(payload: dict = Body(...)):
    """Recibe input del analista y devuelve PDF analítico estructurado.

    Body JSON:
      {
        "caso": "Descripción del caso a monitorear",
        "comentario": "Comentario/hipótesis del analista",
        "urls": ["https://...", "https://..."],
        "periodo": "últimos 7 días",
        "profundidad": "BREVE" | "ESTÁNDAR" | "PROFUNDO",
        "regiones_actores": "Apurímac, Las Bambas, comunidades campesinas",
        "solicitante": "Juan Liendo"
      }

    Devuelve el PDF descargable directamente.
    """
    caso = (payload.get("caso") or "").strip()
    if not caso:
        raise HTTPException(status_code=400, detail="El campo 'caso' es obligatorio")

    # Cargar archive si existe
    archive = None
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if db_path.exists():
        try:
            archive = ApuriskArchive(str(db_path))
        except Exception as e:
            print(f"[warn] no se pudo cargar archive: {e}")

    # Cargar snapshot actual
    snap = None
    snap_path = _ultimo_snapshot_path()
    if snap_path:
        try:
            with open(snap_path, encoding="utf-8") as f:
                snap = json.load(f)
        except Exception as e:
            print(f"[warn] no se pudo cargar snapshot: {e}")

    # URL fetcher opcional (solo en producción con red)
    def _url_fetcher(url: str) -> str | None:
        try:
            import requests
            r = requests.get(url, timeout=10,
                              headers={"User-Agent": "Mozilla/5.0 APURISK-OSINT/1.0"})
            if r.status_code == 200:
                return r.text
        except Exception:
            return None
        return None

    # Ejecutar análisis
    try:
        analisis = analizar_caso(payload, archive=archive, snapshot_actual=snap,
                                   url_fetcher=_url_fetcher)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en análisis: {e}")

    # Generar PDF
    ts = now_pe().strftime("%Y%m%d_%H%M%S")
    safe_id = "".join(c if c.isalnum() else "_" for c in caso[:40]).strip("_") or "caso"
    filename = f"apurisk_analisis_caso_{safe_id}_{ts}.pdf"
    salida = OUTPUT_DIR / filename
    try:
        generar_reporte_caso_pdf(str(salida), analisis)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando PDF: {e}")

    return FileResponse(
        path=str(salida),
        media_type="application/pdf",
        filename=filename,
    )


@app.get("/analisis", response_class=HTMLResponse)
async def analisis_form():
    """Sirve el formulario HTML para que el analista solicite un análisis de caso."""
    html = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8" />
<title>APURISK · Análisis de Caso OSINT</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  :root {
    --bg-0:#0a0e1a; --bg-1:#0f172a; --bg-2:#1e293b; --bg-3:#334155;
    --txt-0:#f1f5f9; --txt-1:#cbd5e1; --txt-2:#94a3b8;
    --accent:#38bdf8; --accent-2:#a78bfa;
    --critico:#ef4444; --bajo:#22c55e;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
         background: var(--bg-0); color: var(--txt-0); font-size: 14px;
         padding: 30px 20px; max-width: 880px; margin: 0 auto; }
  h1 { font-size: 24px; color: var(--txt-0); margin-bottom: 6px;
       background: linear-gradient(90deg, var(--accent), var(--accent-2));
       -webkit-background-clip: text; -webkit-text-fill-color: transparent;
       background-clip: text; }
  .subtitle { color: var(--txt-2); font-size: 13px; margin-bottom: 24px; }
  .container { background: var(--bg-1); border: 1px solid var(--bg-3); border-radius: 12px; padding: 24px; }
  label { display: block; margin-top: 14px; margin-bottom: 6px; font-weight: 600; font-size: 13px; color: var(--txt-1); }
  label small { color: var(--txt-2); font-weight: normal; font-size: 11px; margin-left: 6px; }
  input[type="text"], textarea, select {
    width: 100%; padding: 10px 12px; background: var(--bg-2); color: var(--txt-0);
    border: 1px solid var(--bg-3); border-radius: 8px; font-family: inherit; font-size: 13px;
    transition: border .15s;
  }
  input[type="text"]:focus, textarea:focus, select:focus {
    outline: none; border-color: var(--accent);
  }
  textarea { min-height: 80px; resize: vertical; }
  .btn {
    margin-top: 22px; background: linear-gradient(90deg, var(--accent), var(--accent-2));
    color: var(--bg-0); border: none; padding: 14px 28px; border-radius: 8px;
    font-weight: 700; font-size: 14px; letter-spacing: .5px;
    cursor: pointer; width: 100%; text-transform: uppercase;
    transition: opacity .15s;
  }
  .btn:hover { opacity: 0.85; }
  .btn:disabled { background: var(--bg-3); color: var(--txt-2); cursor: not-allowed; opacity: 1;}
  .status { margin-top: 18px; padding: 12px; border-radius: 8px;
            font-size: 13px; display: none; }
  .status.loading { background: rgba(56,189,248,0.1); color: var(--accent); display: block;
                    border-left: 3px solid var(--accent); }
  .status.error { background: rgba(239,68,68,0.1); color: var(--critico); display: block;
                  border-left: 3px solid var(--critico); }
  .status.success { background: rgba(34,197,94,0.1); color: var(--bajo); display: block;
                    border-left: 3px solid var(--bajo); }
  .nav { display: flex; gap: 14px; margin-bottom: 18px; font-size: 13px; }
  .nav a { color: var(--accent); text-decoration: none; }
  .nav a:hover { text-decoration: underline; }
  .help { color: var(--txt-2); font-size: 11px; margin-top: 4px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 600px) { .row { grid-template-columns: 1fr; } }
</style>
</head>
<body>
  <div class="nav">
    <a href="/dashboard">← Dashboard</a>
    <a href="/api/status" target="_blank">Status</a>
  </div>
  <h1>🔍 Análisis OSINT de Caso</h1>
  <div class="subtitle">
    Solicita un análisis estructurado de un evento o caso de riesgo político para Perú.
    El sistema procesa fuentes internas + URLs proporcionadas y genera un PDF de 14 secciones.
  </div>

  <div class="container">
    <form id="form-caso">
      <label>Caso a monitorear: <small>(obligatorio)</small></label>
      <textarea name="caso" required placeholder="Ej: Operativo militar en Huancavelica deja 5 civiles muertos. Cuestionamientos sobre el uso de fuerza letal por parte del Ejército."></textarea>

      <label>Comentario del analista / hipótesis inicial:</label>
      <textarea name="comentario" placeholder="Ej: Se sospecha que las víctimas eran agricultores sin vínculos con narcotráfico. La cobertura puede polarizarse."></textarea>

      <label>URLs de referencia (una por línea):</label>
      <textarea name="urls" placeholder="https://www.infobae.com/peru/2026/...&#10;https://rpp.pe/peru/..."></textarea>
      <div class="help">URLs específicas que quieres que se analicen prioritariamente. Opcional.</div>

      <div class="row">
        <div>
          <label>Periodo de monitoreo:</label>
          <select name="periodo">
            <option>últimas 24 horas</option>
            <option selected>últimos 7 días</option>
            <option>últimos 14 días</option>
            <option>últimos 30 días</option>
          </select>
        </div>
        <div>
          <label>Nivel de profundidad:</label>
          <select name="profundidad">
            <option value="BREVE">BREVE</option>
            <option value="ESTÁNDAR" selected>ESTÁNDAR</option>
            <option value="PROFUNDO">PROFUNDO</option>
          </select>
        </div>
      </div>

      <label>Regiones, actores o sectores de interés:</label>
      <input type="text" name="regiones_actores" placeholder="Ej: Apurímac, Las Bambas, comunidades campesinas, sector minero" />

      <label>Solicitante: <small>(opcional)</small></label>
      <input type="text" name="solicitante" placeholder="Tu nombre o ID interno" />

      <button type="submit" class="btn" id="btn-submit">📊 Generar reporte PDF</button>
      <div id="status" class="status"></div>
    </form>
  </div>

<script>
  document.getElementById('form-caso').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = document.getElementById('btn-submit');
    const status = document.getElementById('status');
    const fd = new FormData(ev.target);

    const payload = {
      caso: fd.get('caso'),
      comentario: fd.get('comentario'),
      urls: (fd.get('urls') || '').split('\\n').map(s => s.trim()).filter(s => s),
      periodo: fd.get('periodo'),
      profundidad: fd.get('profundidad'),
      regiones_actores: fd.get('regiones_actores'),
      solicitante: fd.get('solicitante'),
    };

    btn.disabled = true;
    btn.textContent = '⏳ Generando análisis...';
    status.className = 'status loading';
    status.textContent = 'Procesando: búsqueda interna, análisis de actores, scoring de riesgo, generación de PDF...';

    try {
      const resp = await fetch('/api/analisis-caso', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({detail: resp.statusText}));
        throw new Error(err.detail || 'Error desconocido');
      }
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const cd = resp.headers.get('content-disposition') || '';
      const m = cd.match(/filename="?([^";]+)"?/);
      const filename = m ? m[1] : 'apurisk_analisis_caso.pdf';
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);

      status.className = 'status success';
      status.textContent = '✓ Reporte generado y descargado. Puedes generar otro caso o volver al dashboard.';
    } catch (e) {
      status.className = 'status error';
      status.textContent = '✗ Error: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = '📊 Generar reporte PDF';
    }
  });
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


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
