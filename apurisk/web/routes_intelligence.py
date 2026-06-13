"""APURISK · web/routes_intelligence — Intelligence/Executive/Strategic briefs."""
from __future__ import annotations
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from .core import (
    OUTPUT_DIR, REPORTES_DIARIOS_DIR, _ultimo_snapshot_path, _esc_html,
    _executive_cache_es_fresca, _generar_executive_brief_fresh,
    EXECUTIVE_CACHE_FILE, EXECUTIVE_CACHE_TTL_HORAS, ApuriskArchive,
)

router = APIRouter()



@router.get("/intelligence", response_class=HTMLResponse)
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


@router.get("/api/intelligence/brief")
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


@router.get("/api/executive/brief")
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


@router.post("/api/executive/brief/regenerar")
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


@router.get("/executive", response_class=HTMLResponse)
async def executive_home():
    """Executive Home — vista premium C-level (Fase B del concepto).

    Renderiza el Executive Brief con estética navy intelligence (Stratfor-style).
    Consume el cache de 4h del brief; si no hay cache lo regenera.
    """
    try:
        # Obtener brief (cache o fresh)
        if _executive_cache_es_fresca():
            with open(EXECUTIVE_CACHE_FILE, encoding="utf-8") as f:
                brief = json.load(f)
        else:
            brief = _generar_executive_brief_fresh()

        # Render HTML
        try:
            from .reports.executive_view import render_executive_home
        except ImportError:
            from apurisk.reports.executive_view import render_executive_home
        return HTMLResponse(content=render_executive_home(brief))
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return HTMLResponse(
            content=f"""
            <html><body style="font-family:monospace;background:#0f172a;color:#f8fafc;padding:40px;">
              <h1 style="color:#ef4444;">Executive Home — Error</h1>
              <p>{_esc_html(str(e))}</p>
              <pre style="font-size:11px;color:#94a3b8;">{_esc_html(tb)}</pre>
              <a href="/dashboard" style="color:#3b82f6;">← Volver al dashboard</a>
            </body></html>
            """,
            status_code=500,
        )


# =====================================================================
# STRATEGIC DAILY BRIEF PDF — Producto C-level Capa 2 (Strategic Intelligence)
# =====================================================================
@router.get("/api/strategic/daily-brief/pdf")
async def strategic_daily_brief_pdf(
    force: bool = Query(False, description="Forzar regeneración del brief subyacente"),
):
    """Strategic Daily Brief PDF — primer producto Capa 2 (Strategic Intelligence).

    Genera un PDF C-level de 4 páginas derivado del Executive Brief
    (mismo motor que /executive HTML). Si el cache 4h está fresco lo usa;
    si no, regenera. Con `?force=true` ignora el cache.

    Estructura del PDF:
      1) Portada: Score Nacional + EDI + tendencias
      2) Executive Insight + Status nacional ampliado
      3) Top 5 Amenazas Prioritarias con narrativa LLM
      4) Outlook 30 días + Implicancias operacionales
    """
    try:
        # 1. Obtener brief (cache o regenerar)
        if force or not _executive_cache_es_fresca():
            brief = _generar_executive_brief_fresh()
        else:
            with open(EXECUTIVE_CACHE_FILE, encoding="utf-8") as f:
                brief = json.load(f)

        # 2. Generar PDF
        try:
            from .reports.strategic_daily_brief import generar_strategic_daily_brief_pdf
        except ImportError:
            from apurisk.reports.strategic_daily_brief import generar_strategic_daily_brief_pdf

        fecha_str = (brief.get("generado_en", "") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
        fecha_compact = fecha_str.replace("-", "")
        filename = f"reporte-diario-riesgo-politico-peru-{fecha_compact}.pdf"
        REPORTES_DIARIOS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(REPORTES_DIARIOS_DIR / filename)

        generar_strategic_daily_brief_pdf(output_path, brief)

        return FileResponse(
            output_path,
            media_type="application/pdf",
            filename=filename,
        )
    except HTTPException:
        raise
    except Exception as e:
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


# =====================================================================
# REPORTE 24H ON-DEMAND PDF — Capa 2 Strategic, sin cache, manual
# =====================================================================
@router.get("/api/strategic/last-24h/pdf")
async def strategic_last_24h_pdf():
    """Reporte 24 h de Riesgo Político · Perú — On-demand manual.

    Diferencias respecto al Reporte 06:00 AM:
      - Siempre regenera el brief (sin cache), datos frescos del momento
      - Cabecera dice "GENERADO HH:MM" en lugar de "FECHA DE CORTE 06:00"
      - Título: "Reporte 24h de Riesgo Político · Perú"
      - Footer: REPORTE 24H ON-DEMAND
      - Mismo motor LLM + misma plantilla visual (consistencia de marca)

    Caso de uso: briefing express durante el día sin esperar al ciclo siguiente.
    """
    try:
        # SIEMPRE regenerar — sin cache (es la diferencia clave con el Daily)
        brief = _generar_executive_brief_fresh()

        try:
            from .reports.strategic_daily_brief import generar_strategic_daily_brief_pdf
        except ImportError:
            from apurisk.reports.strategic_daily_brief import generar_strategic_daily_brief_pdf

        now = datetime.now()
        fecha_compact = now.strftime("%Y%m%d-%H%M")
        filename = f"reporte-24h-riesgo-politico-peru-{fecha_compact}.pdf"
        REPORTES_DIARIOS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(REPORTES_DIARIOS_DIR / filename)

        # Asegurar que el brief lleva la hora actual de generación (para cabecera)
        brief["generado_en"] = now.isoformat()

        generar_strategic_daily_brief_pdf(output_path, brief, modo="on_demand_24h")

        return FileResponse(
            output_path,
            media_type="application/pdf",
            filename=filename,
        )
    except HTTPException:
        raise
    except Exception as e:
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


@router.get("/api/executive/debug-snapshot")
async def executive_debug_snapshot():
    """Diagnóstico: muestra la estructura raíz del snapshot real (no su contenido completo,
    solo las claves y tipos) para entender por qué algunos campos no se leen bien."""
    snap_path = _ultimo_snapshot_path()
    if not snap_path:
        return {"error": "Sin snapshot disponible"}
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)

    def _resumen(v, depth=0):
        if depth > 2:
            return "..."
        if isinstance(v, dict):
            return {k: _resumen(vv, depth + 1) for k, vv in v.items()}
        if isinstance(v, list):
            return f"<list[{len(v)}] sample: {_resumen(v[0], depth+1) if v else 'empty'}>"
        if isinstance(v, str):
            return f"<str len={len(v)}> {v[:60]}"
        return f"<{type(v).__name__}> {v}"

    return {
        "snapshot_path": str(snap_path),
        "claves_raiz": list(snap.keys()),
        "riesgo_completo": snap.get("riesgo"),
        "matriz_riesgo_n": len(snap.get("matriz_riesgo", [])),
        "matriz_riesgo_sample_keys": list(snap["matriz_riesgo"][0].keys()) if snap.get("matriz_riesgo") else [],
        "alertas_n": len(snap.get("alertas", [])),
        "acled_events_n": len(snap.get("acled_events", [])),
        "crimen_items_n": len(snap.get("crimen_items", [])),
        "conflictos_n": len(snap.get("conflictos", [])),
        "acled_event_sample_keys": list(snap["acled_events"][0].keys()) if snap.get("acled_events") else [],
        "conflicto_sample_keys": list(snap["conflictos"][0].keys()) if snap.get("conflictos") else [],
    }


@router.get("/api/executive/sutran-test")
async def executive_sutran_test():
    """Diagnóstico: hace fetch live al endpoint SUTRAN/MTC y devuelve
    cuántas alertas obtuvo, o el error exacto si falla.

    Sirve para verificar:
      1. Que el código del collector SUTRAN está deployado
      2. Que Render puede llegar a *.gob.pe
      3. Cuántas alertas hay AHORA en el MTC
    """
    import time
    t0 = time.time()
    resultado = {
        "endpoint": "https://gis.sutran.gob.pe/alerta_sutran/script_cgm/carga_xlsx.php?tipo=MAPA",
    }
    try:
        try:
            from .collectors.sutran import fetch_sutran_alertas
        except ImportError:
            from apurisk.collectors.sutran import fetch_sutran_alertas
        eventos = fetch_sutran_alertas(timeout=15)
        resultado["status"] = "OK"
        resultado["latencia_ms"] = round((time.time() - t0) * 1000)
        resultado["n_eventos"] = len(eventos)
        # Resumen por estado
        from collections import Counter
        if eventos:
            resultado["por_estado"] = dict(Counter(e["estado"] for e in eventos))
            resultado["por_motivo"] = dict(Counter(e["motivo"] for e in eventos))
            resultado["por_tipo_hotspot"] = dict(Counter(e["_tipo_hotspot_hint"] for e in eventos))
            # Primeras 3 muestras para validar el shape
            resultado["sample_eventos"] = [
                {
                    "titulo": e["titulo"][:140],
                    "estado": e["estado"],
                    "motivo": e["motivo"],
                    "region": e["region"],
                    "distrito": e["distrito"],
                    "km": e["kilometraje"],
                    "via": e["via_codigo"],
                    "lat": e["lat"],
                    "lon": e["lon"],
                    "tipo_hotspot": e["_tipo_hotspot_hint"],
                    "fuente": e["fuente"],
                }
                for e in eventos[:3]
            ]
        return resultado
    except ImportError as e:
        resultado["status"] = "FAIL_IMPORT"
        resultado["error"] = (f"Modulo apurisk.collectors.sutran no existe en el deploy "
                              f"actual. Push del codigo Tarea B no se hizo: {e}")
        return resultado
    except Exception as e:
        import traceback
        resultado["status"] = "FAIL_FETCH"
        resultado["error_type"] = type(e).__name__
        resultado["error_msg"] = str(e)[:500]
        resultado["latencia_ms"] = round((time.time() - t0) * 1000)
        resultado["traceback_tail"] = traceback.format_exc().splitlines()[-10:]
        return resultado


@router.get("/api/executive/llm-test")
async def executive_llm_test(modelo: str = Query(None, description="Override del modelo (default env var APURISK_LLM_MODEL o claude-haiku-4-5)")):
    """Diagnóstico: hace UNA llamada de prueba al LLM y devuelve resultado o error.

    Útil para diagnosticar por qué llamadas fallan en producción.
    Si `modelo` se pasa, prueba ese modelo específicamente.
    """
    import os, traceback
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    # Solo exponemos si la key está presente o no. NUNCA su longitud ni un
    # prefijo: aun unos pocos caracteres de una credencial ayudan a un
    # atacante y no aportan nada al diagnóstico real.
    resultado = {
        "api_key_presente": bool(api_key),
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
        # El traceback completo se registra en el log del servidor, no se
        # devuelve por HTTP (puede filtrar rutas internas / detalles del entorno).
        print("[llm-test] error:\n" + traceback.format_exc())
        return resultado


@router.get("/api/executive/status")
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
