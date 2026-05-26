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

from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    from .utils.timezone_pe import now_pe, now_pe_iso
    from .storage import ApuriskArchive
    from .main import run_once as pipeline_run_once
    from .analyzers.caso_analyzer import analizar_caso
    from .analyzers.riesgo_minera import analizar_riesgo_minera
    from .reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
        generar_reporte_caso_pdf,
    )
    from .reports.pdf_minera import generar_reporte_minera_pdf
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apurisk.utils.timezone_pe import now_pe, now_pe_iso
    from apurisk.storage import ApuriskArchive
    from apurisk.main import run_once as pipeline_run_once
    from apurisk.analyzers.caso_analyzer import analizar_caso
    from apurisk.analyzers.riesgo_minera import analizar_riesgo_minera
    from apurisk.reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
        generar_reporte_caso_pdf,
    )
    from apurisk.reports.pdf_minera import generar_reporte_minera_pdf


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


# =============================================================
# SCHEDULER SEMANAL MINERO — DESACTIVADO (mayo 2026)
# =============================================================
# Decisión del cliente: solo se archivan los reportes generados manualmente
# desde el formulario del dashboard. El scheduler automático que generaba
# un reporte cada lunes 06:00 AM PET y lo archivaba en SQLite + disco
# ha sido desactivado para mantener el archivo histórico curado por el
# analista (no contaminado con reportes genéricos automáticos).
#
# Si en el futuro se requiere reactivar (ej: reporte automático de
# referencia para clientes piloto), descomentar la función
# _scheduler_semanal_minera y la línea asyncio.create_task() en _startup.
# =============================================================


# =============================================================
# SCHEDULER DIARIO EJECUTIVO — 06:00 AM Lima (PET)
# =============================================================
# Genera UN único reporte ejecutivo PDF cada día a las 06:00 AM Lima.
# Contiene datos consolidados hasta esa hora del día.
# Se almacena en /output/reportes_diarios/.
# Limpieza retentiva: mantiene últimos 30 días.
#
# Los reportes manuales (generados desde el dashboard) siguen
# disponibles en formato PDF y DOCX vía endpoints REST.
# =============================================================

REPORTES_DIARIOS_DIR = OUTPUT_DIR / "reportes_diarios"
REPORTES_DIARIOS_DIR.mkdir(parents=True, exist_ok=True)


def _limpiar_reportes_diarios_viejos(retencion_dias: int = 30) -> int:
    """Elimina reportes diarios con más de N días de antigüedad."""
    from datetime import datetime as _dt
    eliminados = 0
    limite = _dt.now().timestamp() - retencion_dias * 86400
    for f in REPORTES_DIARIOS_DIR.glob("apurisk_reporte_diario_*.pdf"):
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
                eliminados += 1
        except Exception:
            pass
    return eliminados


async def _scheduler_diario_pdf():
    """Loop infinito que cada día a las 06:00 AM Lima genera 1 PDF ejecutivo.

    El PDF consolida los datos hasta las 06:00 AM y se guarda en
    /output/reportes_diarios/. NO genera DOCX/HTML/JSON adicionales.
    Los reportes manuales (vía dashboard) siguen con PDF+DOCX disponibles.
    """
    from datetime import timedelta as _td
    print("[scheduler-diario-pdf] iniciado · proxima corrida: hoy/manana 06:00 PET")
    while True:
        try:
            ahora = now_pe()
            # Calcular próximo 06:00 AM PET
            proximo = ahora.replace(hour=6, minute=0, second=0, microsecond=0)
            if ahora >= proximo:
                # Ya pasaron las 06:00 hoy, programar para mañana
                proximo += _td(days=1)
            espera_seg = (proximo - ahora).total_seconds()
            print(f"[scheduler-diario-pdf] próximo reporte diario: "
                  f"{proximo.isoformat()} (en {int(espera_seg/3600)}h "
                  f"{int((espera_seg%3600)/60)}m)")
            await asyncio.sleep(max(60, espera_seg))

            # Generar el PDF ejecutivo diario
            print(f"[scheduler-diario-pdf] generando reporte diario a las {now_pe_iso()}")
            try:
                snap_path = _ultimo_snapshot_path()
                if not snap_path:
                    print("[scheduler-diario-pdf] sin snapshot disponible, saltando")
                    continue
                with open(snap_path, encoding="utf-8") as f:
                    snap = json.load(f)

                # Limpieza retentiva ANTES de generar nuevo
                n_limpios = _limpiar_reportes_diarios_viejos(retencion_dias=30)
                if n_limpios > 0:
                    print(f"[scheduler-diario-pdf] {n_limpios} reportes >30d eliminados")

                # Nombre claro con fecha
                fecha = now_pe().strftime("%Y%m%d")
                filename = f"apurisk_reporte_diario_{fecha}_06h.pdf"
                pdf_path = REPORTES_DIARIOS_DIR / filename

                # Generar el PDF ejecutivo (formato compacto ≤3 páginas)
                generar_ejecutivo_pdf(str(pdf_path), snap, str(OUTPUT_DIR))
                print(f"[scheduler-diario-pdf] OK: {filename}")
            except Exception as e:
                print(f"[scheduler-diario-pdf] ERROR generando: {e}")
        except Exception as e:
            print(f"[scheduler-diario-pdf] ERROR ciclo: {e}")
            await asyncio.sleep(3600)  # espera 1h en error grave


@app.on_event("startup")
async def _startup():
    # Limpieza AGRESIVA de archivos antiguos al iniciar el servicio.
    # Esto elimina la basura acumulada de deploys anteriores SIN esperar
    # al primer ciclo del scheduler (que tarda hasta 30 min en correr).
    try:
        try:
            from .main import _limpiar_archivos_viejos
        except ImportError:
            from apurisk.main import _limpiar_archivos_viejos
        n = _limpiar_archivos_viejos(
            OUTPUT_DIR,
            retencion_snapshots=5,
            retencion_dashboards=3,
            retencion_reportes_dias=30,
        )
        if n > 0:
            print(f"[startup] {n} archivos antiguos eliminados del disco")
        # También limpiar reportes diarios viejos (>30 días)
        n_diarios = _limpiar_reportes_diarios_viejos(retencion_dias=30)
        if n_diarios > 0:
            print(f"[startup] {n_diarios} reportes diarios >30d eliminados")
    except Exception as e:
        print(f"[startup] limpieza inicial falló: {e}")

    # Schedulers activos:
    # 1) Principal OSINT (cada 30 min): recolecta RSS y actualiza dashboard.html
    asyncio.create_task(_scheduler_loop())
    # 2) Diario PDF (06:00 AM Lima): genera 1 PDF ejecutivo diario
    asyncio.create_task(_scheduler_diario_pdf())
    # NOTA: scheduler semanal minero DESACTIVADO.
    # asyncio.create_task(_scheduler_semanal_minera())


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


@app.get("/intelligence", response_class=HTMLResponse)
async def intelligence_view(dias_baseline: int = Query(28, ge=7, le=180)):
    """Vista HTML profesional del Strategic Intelligence Brief.

    Renderiza los 8 outputs analíticos como una página visual estilo
    Bloomberg/Stratfor con diseño dark premium. Esta es la cara ejecutiva
    del motor de inteligencia — no JSON crudo.

    Para consumo programático usa /api/intelligence/brief.
    """
    snap_path = _ultimo_snapshot_path()
    if not snap_path:
        return HTMLResponse(
            "<html><body><h1>Sin snapshot disponible</h1></body></html>",
            status_code=503
        )
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)
    archive = None
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if db_path.exists():
        try:
            archive = ApuriskArchive(str(db_path))
        except Exception:
            pass
    try:
        try:
            from .analyzers.intelligence_engine import generar_intelligence_brief
            from .reports.intelligence_view import render_intelligence_html
        except ImportError:
            from apurisk.analyzers.intelligence_engine import generar_intelligence_brief
            from apurisk.reports.intelligence_view import render_intelligence_html
        brief = generar_intelligence_brief(snap, archive=archive,
                                              dias_baseline=dias_baseline)
        html = render_intelligence_html(brief, snap)
        return HTMLResponse(content=html, headers={
            "Content-Type": "text/html; charset=utf-8"
        })
    except Exception as e:
        return HTMLResponse(
            f"<html><body><h1>Error</h1><pre>{e}</pre></body></html>",
            status_code=500
        )


@app.get("/api/intelligence/brief")
async def intelligence_brief(dias_baseline: int = Query(28, ge=7, le=180)):
    """Strategic Intelligence Brief — 8 outputs analíticos.

    Devuelve el producto analítico completo:
      - strategic_assessment (narrativa de analista senior)
      - convergencias detectadas
      - anomalías estadísticas
      - silencios institucionales inusuales
      - indicators_warnings (I&W de doctrina inteligencia)
      - stakeholder_movement (quién se movió esta semana)
      - comparative_benchmark (vs histórico propio y región andina)
      - strategic_recommendation (acción priorizada)

    Args:
      dias_baseline: ventana histórica para baselines (default 28 días).
    """
    snap_path = _ultimo_snapshot_path()
    if not snap_path:
        raise HTTPException(status_code=503, detail="Sin snapshot disponible.")
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)
    archive = None
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if db_path.exists():
        try:
            archive = ApuriskArchive(str(db_path))
        except Exception:
            pass
    try:
        try:
            from .analyzers.intelligence_engine import generar_intelligence_brief
        except ImportError:
            from apurisk.analyzers.intelligence_engine import generar_intelligence_brief
        brief = generar_intelligence_brief(snap, archive=archive,
                                              dias_baseline=dias_baseline)
        return brief
    except Exception as e:
        raise HTTPException(status_code=500,
                              detail=f"Error generando intelligence brief: {e}")


# =====================================================================
# EXECUTIVE BRIEF — Síntesis ejecutiva C-level con cache 4h
# =====================================================================

EXECUTIVE_CACHE_FILE = OUTPUT_DIR / "executive_brief_cache.json"
EXECUTIVE_CACHE_TTL_HORAS = 4


def _executive_cache_es_fresca() -> bool:
    """True si el cache existe y tiene menos de TTL horas."""
    if not EXECUTIVE_CACHE_FILE.exists():
        return False
    try:
        with open(EXECUTIVE_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        valido_hasta = data.get("valido_hasta", "")
        if not valido_hasta:
            return False
        # Comparar con ahora en PET
        try:
            from .utils.timezone_pe import now_pe, parse_to_pe
        except ImportError:
            from apurisk.utils.timezone_pe import now_pe, parse_to_pe
        vh = parse_to_pe(valido_hasta) or datetime.fromisoformat(valido_hasta)
        return now_pe() < vh
    except Exception:
        return False


def _generar_executive_brief_fresh() -> dict:
    """Regenera el brief ejecutivo (corrida costosa con LLM)."""
    snap_path = _ultimo_snapshot_path()
    if not snap_path:
        raise HTTPException(status_code=503, detail="Sin snapshot disponible.")
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)

    archive = None
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if db_path.exists():
        try:
            archive = ApuriskArchive(str(db_path))
        except Exception:
            pass

    # 1. Intelligence brief (insumo)
    try:
        from .analyzers.intelligence_engine import generar_intelligence_brief
    except ImportError:
        from apurisk.analyzers.intelligence_engine import generar_intelligence_brief
    intel = generar_intelligence_brief(snap, archive=archive, dias_baseline=28)

    # 2. Executive synthesis (lo nuevo)
    try:
        from .analyzers.executive_synthesis import sintetizar_executive_brief
    except ImportError:
        from apurisk.analyzers.executive_synthesis import sintetizar_executive_brief
    brief = sintetizar_executive_brief(snap, intel)

    # 3. Persistir cache
    try:
        with open(EXECUTIVE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(brief, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # No fatal; el brief se devuelve igual aunque el cache falle
        pass

    return brief


@app.get("/api/executive/brief")
async def executive_brief(force: bool = Query(False, description="Forzar regeneración ignorando cache 4h")):
    """Executive Brief — síntesis ejecutiva C-level con los 7 bloques del concepto:
    status nacional, amenazas prioritarias, critical alerts, hotspots,
    implicancias operacionales, outlook 30d, executive insight.

    Cache de 4 horas. Pasa `?force=true` para regenerar manualmente.
    """
    if not force and _executive_cache_es_fresca():
        with open(EXECUTIVE_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(
            content=data,
            media_type="application/json; charset=utf-8",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    try:
        brief = _generar_executive_brief_fresh()
        return JSONResponse(
            content=brief,
            media_type="application/json; charset=utf-8",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except HTTPException:
        raise
    except Exception as e:
        # Modo debug temporal — incluye traceback para localizar la causa
        import traceback
        tb = traceback.format_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(e).__name__,
                "error_msg": str(e),
                "traceback": tb.splitlines()[-15:],
            }
        )


@app.post("/api/executive/brief/regenerar")
async def executive_brief_regenerar():
    """Endpoint manual para forzar regeneración. Misma respuesta que GET?force=true."""
    try:
        brief = _generar_executive_brief_fresh()
        return {"status": "ok", "regenerado_en": brief.get("generado_en"),
                "valido_hasta": brief.get("valido_hasta"), "brief": brief}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/executive/llm-test")
async def executive_llm_test(modelo: str = Query(None, description="Override del modelo (default env var APURISK_LLM_MODEL o claude-haiku-4-5)")):
    """Diagnóstico: hace UNA llamada de prueba al LLM y devuelve resultado o error.

    Útil para diagnosticar por qué llamadas fallan en producción.
    Si `modelo` se pasa, prueba ese modelo específicamente.
    """
    import os, traceback
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    resultado = {
        "api_key_presente": bool(api_key),
        "api_key_largo_caracteres": len(api_key) if api_key else 0,
        "api_key_prefix": api_key[:12] + "..." if len(api_key) > 12 else "(vacío o corto)",
        "modelo_intentado": modelo or os.environ.get("APURISK_LLM_MODEL", "claude-haiku-4-5-20251001"),
    }
    if not api_key:
        resultado["status"] = "FAIL"
        resultado["error"] = "ANTHROPIC_API_KEY no está en env vars"
        return resultado
    try:
        from anthropic import Anthropic
    except ImportError as e:
        resultado["status"] = "FAIL"
        resultado["error"] = f"Paquete anthropic no instalado: {e}"
        return resultado

    modelo_use = resultado["modelo_intentado"]
    try:
        client = Anthropic(api_key=api_key, timeout=15)
        respuesta = client.messages.create(
            model=modelo_use,
            max_tokens=50,
            messages=[{"role": "user",
                       "content": "Responde solo con la palabra: OK"}],
        )
        texto = respuesta.content[0].text.strip() if respuesta.content else ""
        resultado["status"] = "SUCCESS"
        resultado["respuesta"] = texto
        resultado["input_tokens"] = respuesta.usage.input_tokens
        resultado["output_tokens"] = respuesta.usage.output_tokens
        resultado["modelo_usado"] = respuesta.model
        return resultado
    except Exception as e:
        resultado["status"] = "FAIL"
        resultado["error_type"] = type(e).__name__
        resultado["error_msg"] = str(e)[:500]
        resultado["traceback_tail"] = traceback.format_exc().splitlines()[-8:]
        return resultado


@app.get("/api/executive/status")
async def executive_status():
    """Estado del cache del executive brief (para debug)."""
    estado = {
        "cache_existe": EXECUTIVE_CACHE_FILE.exists(),
        "cache_fresco": _executive_cache_es_fresca(),
        "ttl_horas": EXECUTIVE_CACHE_TTL_HORAS,
    }
    if estado["cache_existe"]:
        try:
            with open(EXECUTIVE_CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            estado["generado_en"] = data.get("generado_en")
            estado["valido_hasta"] = data.get("valido_hasta")
            estado["llm_modo"] = data.get("llm_modo")
        except Exception:
            pass
    # LLM disponibilidad
    try:
        from .utils.llm_client import llm_disponible, estado_uso
    except ImportError:
        from apurisk.utils.llm_client import llm_disponible, estado_uso
    estado["llm_api_key_presente"] = llm_disponible()
    estado["llm_uso_runtime"] = estado_uso()
    return estado


@app.get("/api/reportes-diarios")
async def listar_reportes_diarios():
    """Lista los PDFs ejecutivos diarios generados automáticamente a las 06:00 AM.

    Cada PDF contiene la consolidación del día. Retención automática: 30 días.
    """
    archivos = []
    for f in sorted(REPORTES_DIARIOS_DIR.glob("apurisk_reporte_diario_*.pdf"),
                     key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            stat = f.stat()
            archivos.append({
                "nombre": f.name,
                "tamaño_kb": round(stat.st_size / 1024, 1),
                "fecha_generacion": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "url_descarga": f"/api/reportes-diarios/{f.name}",
            })
        except Exception:
            continue
    return {
        "count": len(archivos),
        "retencion_dias": 30,
        "siguiente_generacion": "Diaria a las 06:00 AM Lima (PET)",
        "formato": "PDF únicamente",
        "reportes": archivos,
    }


@app.get("/api/reportes-diarios/{filename}")
async def descargar_reporte_diario(filename: str):
    """Descarga un PDF de reporte diario por nombre."""
    # Sanity check: solo PDFs y solo dentro del directorio
    if not filename.endswith(".pdf") or "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nombre inválido")
    pdf_path = REPORTES_DIARIOS_DIR / filename
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
    )


@app.post("/api/limpiar-archive-contaminado")
async def limpiar_archive_contaminado():
    """Limpia del archive SQLite las alertas y artículos contaminados.

    Elimina del archive histórico cualquier registro que:
      - Sea de otro país LATAM (Bolivia, Argentina, etc.)
      - Sea contenido deportivo o de farándula
      - Tenga URL apuntando a dominios .ar, .bo, .cl, .br, etc.

    Esto resuelve el problema de datos viejos archivados antes del fix
    del filtro de país que siguen apareciendo en las pestañas.
    """
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="Sin archive.")

    try:
        try:
            from .utils.content_filter import es_contenido_irrelevante
        except ImportError:
            from apurisk.utils.content_filter import es_contenido_irrelevante

        archive = ApuriskArchive(str(db_path))
        eliminados_articulos = 0
        eliminados_alertas = 0

        with archive._conn() as c:
            # Limpiar artículos contaminados
            articulos = c.execute("""
                SELECT id, title, summary, url, source_id
                FROM articulos
            """).fetchall()
            ids_borrar = []
            for art in articulos:
                faux = {
                    "title": art["title"] or "",
                    "summary": art["summary"] or "",
                    "url": art["url"] or "",
                    "source_id": art["source_id"] or "",
                }
                if es_contenido_irrelevante(faux):
                    ids_borrar.append(art["id"])
            for art_id in ids_borrar:
                c.execute("DELETE FROM articulos WHERE id = ?", (art_id,))
                eliminados_articulos += 1

            # Limpiar alertas contaminadas
            alertas = c.execute("""
                SELECT id, titulo, resumen, url
                FROM alertas
            """).fetchall()
            ids_borrar = []
            for alt in alertas:
                faux = {
                    "title": alt["titulo"] or "",
                    "summary": alt["resumen"] or "",
                    "url": alt["url"] or "",
                    "source_id": "",
                }
                if es_contenido_irrelevante(faux):
                    ids_borrar.append(alt["id"])
            for alt_id in ids_borrar:
                c.execute("DELETE FROM alertas WHERE id = ?", (alt_id,))
                eliminados_alertas += 1

            c.commit()

        return {
            "status": "ok",
            "articulos_eliminados": eliminados_articulos,
            "alertas_eliminadas": eliminados_alertas,
            "mensaje": (f"Limpieza del archive completa. {eliminados_articulos} artículos "
                        f"y {eliminados_alertas} alertas eliminados del SQLite. "
                        f"El próximo refresh del dashboard mostrará solo datos limpios."),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")


@app.post("/api/limpiar-archivos")
async def limpiar_archivos(
    retencion_snapshots: int = Query(5, ge=1, le=100),
    retencion_dashboards: int = Query(3, ge=1, le=50),
    retencion_reportes_dias: int = Query(30, ge=1, le=365),
):
    """Limpia archivos automáticos antiguos del disco.

    Parámetros (todos opcionales):
      - retencion_snapshots: mantener N snapshots JSON más recientes (default 5)
      - retencion_dashboards: mantener N dashboards HTML más recientes (default 3)
      - retencion_reportes_dias: conservar reportes bajo demanda hasta N días (default 30)

    PRESERVADOS siempre: dashboard.html, apurisk_archive.db, reportes_caso/
    """
    try:
        from .main import _limpiar_archivos_viejos
    except ImportError:
        from apurisk.main import _limpiar_archivos_viejos
    try:
        eliminados = _limpiar_archivos_viejos(
            OUTPUT_DIR,
            retencion_snapshots=retencion_snapshots,
            retencion_dashboards=retencion_dashboards,
            retencion_reportes_dias=retencion_reportes_dias,
        )
        # Calcular espacio liberado aproximado
        return {
            "status": "ok",
            "archivos_eliminados": eliminados,
            "retencion": {
                "snapshots": retencion_snapshots,
                "dashboards": retencion_dashboards,
                "reportes_dias": retencion_reportes_dias,
            },
            "nota": "Reportes en /reportes_caso/ (riesgo minero) se preservan siempre.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en limpieza: {e}")


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
# RIESGO POLÍTICO MINERO — generación y archivo de reportes
# ======================================================================
REPORTES_DIR = OUTPUT_DIR / "reportes_caso"
REPORTES_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/riesgo-minera/generar")
async def generar_riesgo_minera(request: Request):
    """Genera un reporte semanal de Riesgo Político Minero ad-hoc.

    Soporta dos formatos de body:

    1) **JSON** (sin archivos):
       {
         "empresa": "Sector Minero Peruano",
         "departamentos": ["Apurímac", "Cusco"],
         "alcance": "nacional",
         "periodo_dias": 7,
         "hipotesis": "...",
         "urls_adjuntas": ["https://...", "..."]
       }

    2) **multipart/form-data** (con archivos PDF/DOCX/TXT/MD):
       - Mismos campos como form fields
       - Campo "documentos" con uno o más archivos
       - Los documentos se procesan y su texto se inyecta al motor analítico

    Devuelve el PDF directamente y archiva en SQLite.
    """
    parametros = {}
    documentos_procesados = []
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        # === MODO MULTIPART (con archivos) ===
        try:
            from .utils.document_extractor import extract_document
        except ImportError:
            from apurisk.utils.document_extractor import extract_document

        form = await request.form()

        # Extraer campos de texto
        parametros["empresa"] = form.get("empresa") or "Sector Minero Peruano"
        # departamentos puede venir como JSON string o como múltiples campos
        deps_raw = form.get("departamentos") or ""
        if deps_raw:
            try:
                deps_parsed = json.loads(deps_raw)
                if isinstance(deps_parsed, list):
                    parametros["departamentos"] = deps_parsed
                else:
                    parametros["departamentos"] = None
            except json.JSONDecodeError:
                # CSV simple: "Apurimac,Cusco"
                parametros["departamentos"] = [d.strip() for d in deps_raw.split(",") if d.strip()]
        parametros["alcance"] = form.get("alcance") or "nacional"
        try:
            parametros["periodo_dias"] = int(form.get("periodo_dias") or 7)
        except (TypeError, ValueError):
            parametros["periodo_dias"] = 7
        parametros["solicitante"] = form.get("solicitante") or "Cliente piloto"
        parametros["hipotesis"] = form.get("hipotesis") or ""

        # URLs: aceptar como JSON o como texto multilínea
        urls_raw = form.get("urls_adjuntas") or ""
        urls_list = []
        if urls_raw:
            try:
                p = json.loads(urls_raw)
                if isinstance(p, list):
                    urls_list = [u.strip() for u in p if u and u.strip()]
            except json.JSONDecodeError:
                urls_list = [u.strip() for u in urls_raw.split("\n") if u.strip()]
        parametros["urls_adjuntas"] = urls_list

        # Procesar archivos adjuntos
        # FastAPI form() devuelve UploadFile o str; iteramos sobre items con key="documentos"
        files = form.getlist("documentos") if hasattr(form, "getlist") else []
        for upload in files:
            if hasattr(upload, "filename") and hasattr(upload, "read"):
                try:
                    file_bytes = await upload.read()
                    ct = getattr(upload, "content_type", "") or ""
                    doc = extract_document(upload.filename, ct, file_bytes)
                    documentos_procesados.append(doc)
                    print(f"  [riesgo-minera] documento: {doc['nombre']} "
                          f"({doc['tipo']}, {doc['caracteres']} chars)"
                          + (f" — ERROR: {doc['error']}" if doc.get("error") else ""))
                except Exception as e:
                    print(f"  [warn] error procesando archivo: {e}")
        parametros["documentos_adjuntos"] = documentos_procesados

    else:
        # === MODO JSON (sin archivos) ===
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        parametros = payload or {}
    # Cargar snapshot actual
    snap = None
    snap_path = _ultimo_snapshot_path()
    if snap_path:
        try:
            with open(snap_path, encoding="utf-8") as f:
                snap = json.load(f)
        except Exception as e:
            print(f"[warn] no se pudo cargar snapshot: {e}")

    # Cargar archive
    archive = None
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if db_path.exists():
        try:
            archive = ApuriskArchive(str(db_path))
        except Exception as e:
            print(f"[warn] archive no disponible: {e}")

    # URL fetcher para procesar URLs aportadas por el analista
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

    # Ejecutar análisis (pasando url_fetcher para procesar URLs adjuntas)
    try:
        analisis = analizar_riesgo_minera(
            parametros, archive=archive, snapshot_actual=snap,
            url_fetcher=_url_fetcher,
        )
    except Exception as e:
        raise HTTPException(status_code=500,
                              detail=f"Error en análisis minero: {e}")

    # Generar PDF
    meta = analisis["metadata"]
    ts = now_pe().strftime("%Y%m%d_%H%M%S")
    safe_cliente = "".join(c if c.isalnum() else "_"
                            for c in meta.get("empresa", "generico")[:30]).strip("_")
    filename = f"riesgo_minera_{safe_cliente}_W{meta['semana_iso']}_{meta['año']}_{ts}.pdf"
    pdf_path = REPORTES_DIR / filename
    try:
        generar_reporte_minera_pdf(str(pdf_path), analisis)
    except Exception as e:
        raise HTTPException(status_code=500,
                              detail=f"Error generando PDF minero: {e}")

    # Archivar en SQLite
    if archive:
        try:
            archive.archivar_reporte_caso(
                reporte_meta=meta,
                pdf_path=str(pdf_path),
                json_resumen=analisis["seccion_1_resumen_ejecutivo"],
                parametros=parametros,
            )
        except Exception as e:
            print(f"[warn] no se pudo archivar reporte: {e}")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
    )


@app.get("/api/reportes")
async def listar_reportes(
    plantilla: Optional[str] = Query(None),
    cliente: Optional[str] = Query(None),
    año: Optional[int] = Query(None),
    mes: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Lista reportes archivados con filtros opcionales."""
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if not db_path.exists():
        raise HTTPException(status_code=503,
                              detail="Archivo histórico aún no disponible")
    archive = ApuriskArchive(str(db_path))
    if keyword:
        rows = archive.buscar_reportes(keyword, plantilla=plantilla, limit=limit)
    else:
        rows = archive.listar_reportes(
            plantilla=plantilla, cliente=cliente,
            año=año, mes=mes, limit=limit,
        )
    return {
        "count": len(rows),
        "stats": archive.stats_reportes(),
        "results": rows,
    }


@app.get("/api/reportes/{reporte_id}/pdf")
async def descargar_reporte(reporte_id: int):
    """Descarga el PDF de un reporte archivado."""
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="Archivo no disponible")
    archive = ApuriskArchive(str(db_path))
    rows = archive.listar_reportes(limit=1000)
    reporte = next((r for r in rows if r["id"] == reporte_id), None)
    if not reporte:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    pdf_path = reporte.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=404,
                              detail="PDF físico no encontrado en disco")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=Path(pdf_path).name,
    )


@app.get("/riesgo-minera", response_class=HTMLResponse)
async def riesgo_minera_form():
    """Formulario HTML para generar reporte de Riesgo Político Minero."""
    html = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8" />
<title>APURISK · Riesgo Político Minero</title>
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
  input[type="text"], input[type="number"], textarea, select {
    width: 100%; padding: 10px 12px; background: var(--bg-2); color: var(--txt-0);
    border: 1px solid var(--bg-3); border-radius: 8px; font-family: inherit; font-size: 13px;
  }
  input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }
  .checks { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 6px; }
  .checks label { display:flex; align-items:center; gap:6px; margin: 0; font-weight: normal; font-size: 12px; cursor: pointer; }
  .checks input { width: 14px; height: 14px; }
  .btn {
    margin-top: 22px; background: linear-gradient(90deg, var(--accent), var(--accent-2));
    color: var(--bg-0); border: none; padding: 14px 28px; border-radius: 8px;
    font-weight: 700; font-size: 14px; letter-spacing: .5px;
    cursor: pointer; width: 100%; text-transform: uppercase;
  }
  .btn:hover { opacity: 0.85; }
  .btn:disabled { background: var(--bg-3); color: var(--txt-2); cursor: not-allowed; opacity: 1; }
  .status { margin-top: 18px; padding: 12px; border-radius: 8px; font-size: 13px; display: none; }
  .status.loading { background: rgba(56,189,248,0.1); color: var(--accent); display: block;
                    border-left: 3px solid var(--accent); }
  .status.error { background: rgba(239,68,68,0.1); color: var(--critico); display: block;
                  border-left: 3px solid var(--critico); }
  .status.success { background: rgba(34,197,94,0.1); color: var(--bajo); display: block;
                    border-left: 3px solid var(--bajo); }
  .nav { display: flex; gap: 14px; margin-bottom: 18px; font-size: 13px; }
  .nav a { color: var(--accent); text-decoration: none; }
  .help { color: var(--txt-2); font-size: 11px; margin-top: 4px; }
  .info-box { background: rgba(56,189,248,0.08); border-left: 3px solid var(--accent);
              padding: 12px 14px; border-radius: 4px; margin-bottom: 18px;
              font-size: 12px; color: var(--txt-1); line-height: 1.6; }
</style>
</head>
<body>
  <div class="nav">
    <a href="/dashboard">← Dashboard</a>
    <a href="/api/reportes" target="_blank">Reportes archivados</a>
  </div>
  <h1>⛏️ Riesgo Político Minero — Reporte Semanal</h1>
  <div class="subtitle">
    Genera un reporte de 12 secciones (~15 páginas PDF) con análisis OSINT
    estructurado del sector minero peruano.
  </div>

  <div class="info-box">
    <strong>Plantilla genérica nacional</strong> — configurable por empresa y departamentos.
    Incluye 8 factores P×I propietarios mineros, mapeo de stakeholders, escenarios prospectivos
    y recomendaciones operativas. Generación automática programada cada <strong>lunes 6:00 AM</strong> Lima.
  </div>

  <div class="container">
    <form id="form-minera">
      <label>Empresa / Cliente: <small>(opcional, default: Sector Minero Peruano)</small></label>
      <input type="text" name="empresa" placeholder="Ej: Las Bambas, Antamina, Yanacocha o nombre del cliente" />

      <label>Departamentos de operación: <small>(selecciona los relevantes)</small></label>
      <div class="checks">
        <label><input type="checkbox" name="dep" value="Apurímac" /> Apurímac</label>
        <label><input type="checkbox" name="dep" value="Áncash" /> Áncash</label>
        <label><input type="checkbox" name="dep" value="Arequipa" /> Arequipa</label>
        <label><input type="checkbox" name="dep" value="Cajamarca" /> Cajamarca</label>
        <label><input type="checkbox" name="dep" value="Cusco" /> Cusco</label>
        <label><input type="checkbox" name="dep" value="Junín" /> Junín</label>
        <label><input type="checkbox" name="dep" value="La Libertad" /> La Libertad</label>
        <label><input type="checkbox" name="dep" value="Madre de Dios" /> Madre de Dios</label>
        <label><input type="checkbox" name="dep" value="Moquegua" /> Moquegua</label>
        <label><input type="checkbox" name="dep" value="Pasco" /> Pasco</label>
        <label><input type="checkbox" name="dep" value="Piura" /> Piura</label>
        <label><input type="checkbox" name="dep" value="Puno" /> Puno</label>
        <label><input type="checkbox" name="dep" value="Tacna" /> Tacna</label>
      </div>
      <div class="help">Si no seleccionas ninguno, se considera alcance nacional con todos los departamentos mineros.</div>

      <label>Alcance del reporte:</label>
      <select name="alcance">
        <option value="nacional" selected>Nacional</option>
        <option value="regional">Regional (departamentos seleccionados)</option>
      </select>

      <label>Ventana temporal de análisis (días):</label>
      <input type="number" name="periodo_dias" value="7" min="1" max="30" />
      <div class="help">7 = última semana (default). 14 = quincena. 30 = último mes.</div>

      <label>Solicitante: <small>(opcional)</small></label>
      <input type="text" name="solicitante" placeholder="Tu nombre o ID interno" />

      <button type="submit" class="btn" id="btn-submit">⛏️ Generar reporte PDF semanal</button>
      <div id="status" class="status"></div>
    </form>
  </div>

<script>
  document.getElementById('form-minera').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = document.getElementById('btn-submit');
    const status = document.getElementById('status');
    const fd = new FormData(ev.target);
    const departamentos = fd.getAll('dep');
    const payload = {
      empresa: fd.get('empresa') || 'Sector Minero Peruano',
      departamentos: departamentos.length ? departamentos : null,
      alcance: fd.get('alcance'),
      periodo_dias: parseInt(fd.get('periodo_dias') || '7'),
      solicitante: fd.get('solicitante') || 'Cliente piloto',
    };
    btn.disabled = true;
    btn.textContent = '⏳ Generando reporte...';
    status.className = 'status loading';
    status.textContent = 'Procesando: análisis OSINT, factores P×I, escenarios, generación de PDF...';
    try {
      const resp = await fetch('/api/riesgo-minera/generar', {
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
      const filename = m ? m[1] : 'riesgo_minera.pdf';
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
      status.className = 'status success';
      status.textContent = '✓ Reporte generado, descargado y archivado.';
    } catch (e) {
      status.className = 'status error';
      status.textContent = '✗ Error: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = '⛏️ Generar reporte PDF semanal';
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
