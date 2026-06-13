"""APURISK · web/routes_diagnostics — Scores v1/v2, matrices P×I, EDI, diagnósticos."""
from __future__ import annotations
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from .core import (
    OUTPUT_DIR, _ultimo_snapshot_path, _get_archive, _esc_html, ApuriskArchive,
)

router = APIRouter()



# =====================================================================
# SCORE ENGINE v2 — Validación paralela (Sprint 1.8)
# =====================================================================
@router.get("/api/diagnostico/scores-paralelos")
async def scores_paralelos_listado(dias: int = Query(14, ge=1, le=90)):
    """Devuelve los últimos `dias` registros de la tabla scores_paralelos.

    Útil para auditar la corrida v1 vs v2 día a día durante la validación.
    """
    try:
        try:
            from .analyzers.risk_score_v2 import leer_scores_paralelos
        except ImportError:
            from apurisk.analyzers.risk_score_v2 import leer_scores_paralelos
        archive = _get_archive()
        rows = leer_scores_paralelos(archive, dias=dias)
        return {
            "ok": True,
            "dias_solicitados": dias,
            "n_filas": len(rows),
            "registros": rows,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/diagnostico/scores-paralelos/calcular-hoy")
async def scores_paralelos_calcular_hoy():
    """Trigger manual: calcula v1 y v2 con el último snapshot disponible
    y guarda la comparación en scores_paralelos.

    Para usar durante la fase de validación 7-14 días sin esperar al
    scheduler. Idempotente: si ya hay registro de hoy, lo actualiza.
    """
    try:
        try:
            from .analyzers.risk_score_v2 import ejecutar_score_paralelo
        except ImportError:
            from apurisk.analyzers.risk_score_v2 import ejecutar_score_paralelo

        # Cargar el último snapshot disponible
        snap_path = _ultimo_snapshot_path()
        if not snap_path:
            raise HTTPException(status_code=404,
                                  detail="No hay snapshots disponibles. Ejecuta /api/refresh primero.")
        with open(snap_path, encoding="utf-8") as f:
            snapshot = json.load(f)

        # Cargar config (ruta robusta al config.yaml en apurisk/)
        import yaml
        from pathlib import Path as _PathCfg
        _cfg_path = _PathCfg(__file__).parent / "config.yaml"
        with open(str(_cfg_path), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # EDI actual (para integración variable)
        try:
            try:
                from .analyzers.estado_derecho_index import calcular_edi
            except ImportError:
                from apurisk.analyzers.estado_derecho_index import calcular_edi
            archive = _get_archive()
            edi_data = calcular_edi(snapshot, archive=archive, intelligence_brief=None)
            edi_actual = edi_data.get("edi") if isinstance(edi_data, dict) else None
        except Exception:
            edi_actual = None
            archive = _get_archive()

        # Ejecutar paralelo
        resultado = ejecutar_score_paralelo(
            snapshot=snapshot,
            archive=archive,
            edi_actual=edi_actual,
            config=cfg,
            persistir=True,
        )
        return {
            "ok": True,
            "score_v1": resultado["score_v1"],
            "score_v2_resumen": {
                "score_nacional": resultado["score_v2"]["score_nacional"],
                "label": resultado["score_v2"]["label"],
                "confidence": resultado["score_v2"]["confidence"]["score"],
                "evento_critico": resultado["score_v2"]["evento_critico"]["detectado"],
                "n_eventos": resultado["score_v2"]["n_eventos_dedupeados"],
            },
            "delta_v2_v1": resultado["comparacion"]["delta_v2_v1"],
            "persistido": resultado["persistido"],
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(e).__name__,
                "error_msg": str(e),
                "traceback": traceback.format_exc().splitlines()[-12:],
            }
        )


@router.post("/api/diagnostico/scores-paralelos/{fecha}/revision")
async def scores_paralelos_revisar(
    fecha: str,
    decision: str = Query(..., description="aprobado | rechazado | pendiente"),
    nota: str = Query("", description="comentario libre del analista"),
):
    """Marca un día de scores_paralelos como revisado por el analista."""
    try:
        try:
            from .analyzers.risk_score_v2 import marcar_revision
        except ImportError:
            from apurisk.analyzers.risk_score_v2 import marcar_revision
        archive = _get_archive()
        ok = marcar_revision(archive, fecha=fecha, decision=decision, nota=nota)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró registro para fecha={fecha} (o decisión inválida)."
            )
        return {"ok": True, "fecha": fecha, "decision": decision, "nota": nota}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/diagnostico/scores-paralelos", response_class=HTMLResponse)
async def scores_paralelos_dashboard():
    """Dashboard HTML interno con comparación v1 vs v2 día a día."""
    try:
        try:
            from .analyzers.risk_score_v2 import leer_scores_paralelos
        except ImportError:
            from apurisk.analyzers.risk_score_v2 import leer_scores_paralelos
        archive = _get_archive()
        rows = leer_scores_paralelos(archive, dias=14)
        return HTMLResponse(content=_render_scores_paralelos_html(rows))
    except Exception as e:
        return HTMLResponse(
            content=f"<html><body style='font-family:monospace;background:#0f172a;color:#f8fafc;padding:40px;'>"
                    f"<h1 style='color:#ef4444;'>Scores Paralelos · Error</h1>"
                    f"<p>{_esc_html(str(e))}</p></body></html>",
            status_code=500,
        )


def _render_scores_paralelos_html(rows: list) -> str:
    """Renderiza tabla HTML simple para revisión humana."""
    if not rows:
        body_rows = ('<tr><td colspan="10" style="padding:24px;text-align:center;color:#94a3b8;">'
                     'Sin datos. Ejecuta <code>POST /api/diagnostico/scores-paralelos/calcular-hoy</code> '
                     'o espera al próximo ciclo del scheduler.</td></tr>')
    else:
        body_rows = ""
        for r in rows:
            # Helpers de formato — evita format-specifier condicional inválido
            def _fmt(v, fmt="{:.1f}"):
                return fmt.format(v) if isinstance(v, (int, float)) else "—"
            delta = r.get("delta_v2_v1") or 0
            delta_color = "#22c55e" if delta < 0 else "#ef4444" if delta > 5 else "#f59e0b"
            decision = r.get("revision_decision") or "pendiente"
            decision_color = {"aprobado": "#22c55e", "rechazado": "#ef4444"}.get(decision, "#94a3b8")
            conf = r.get("confidence_v2") or 0
            s_v1   = _fmt(r.get("score_v1"))
            s_v2   = _fmt(r.get("score_v2"))
            s_24h  = _fmt(r.get("score_v2_24h"))
            s_7d   = _fmt(r.get("score_v2_7d"))
            s_30d  = _fmt(r.get("score_v2_30d"))
            s_90d  = _fmt(r.get("score_v2_90d"))
            s_conf = _fmt(conf, "{:.0f}")
            body_rows += f"""
            <tr style='border-bottom:1px solid #1e293b;'>
              <td style='padding:8px;font-family:monospace;color:#cbd5e1;'>{r['fecha']}</td>
              <td style='padding:8px;text-align:right;color:#fbbf24;'>{s_v1}</td>
              <td style='padding:8px;text-align:right;color:#60a5fa;font-weight:bold;'>{s_v2}</td>
              <td style='padding:8px;text-align:right;color:{delta_color};'>{delta:+.1f}</td>
              <td style='padding:8px;text-align:right;color:#cbd5e1;'>{s_24h}</td>
              <td style='padding:8px;text-align:right;color:#cbd5e1;'>{s_7d}</td>
              <td style='padding:8px;text-align:right;color:#cbd5e1;'>{s_30d}</td>
              <td style='padding:8px;text-align:right;color:#cbd5e1;'>{s_90d}</td>
              <td style='padding:8px;text-align:right;color:#a855f7;'>{s_conf}</td>
              <td style='padding:8px;color:{decision_color};text-align:center;'>{decision.upper()}</td>
            </tr>
            """
    return f"""
    <html><head><title>THALOS · Scores Paralelos</title>
    <style>
      body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #f8fafc;
              padding: 32px; margin: 0; }}
      h1 {{ color: #60a5fa; margin-bottom: 4px; }}
      .subtitle {{ color: #94a3b8; margin-bottom: 24px; font-size: 13px; }}
      table {{ width: 100%; border-collapse: collapse; background: #1e293b;
                border-radius: 8px; overflow: hidden; }}
      th {{ background: #1e3a8a; color: white; padding: 12px 8px; text-align: left;
            font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
      td {{ font-size: 13px; }}
      .actions {{ margin-top: 24px; padding: 16px; background: #1e293b; border-radius: 8px; }}
      code {{ background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #a855f7; }}
    </style>
    </head><body>
      <h1>📊 Scores Paralelos · Validación v1 ↔ v2</h1>
      <div class="subtitle">Comparación diaria del motor de scoring durante validación paralela. Marca cada día como aprobado/rechazado para liberar v2 a producción.</div>
      <table>
        <thead><tr>
          <th>Fecha</th><th style='text-align:right;'>v1</th>
          <th style='text-align:right;'>v2</th><th style='text-align:right;'>Δ</th>
          <th style='text-align:right;'>24h</th><th style='text-align:right;'>7d</th>
          <th style='text-align:right;'>30d</th><th style='text-align:right;'>90d</th>
          <th style='text-align:right;'>Conf</th><th style='text-align:center;'>Revisión</th>
        </tr></thead>
        <tbody>{body_rows}</tbody>
      </table>
      <div class="actions">
        <strong>Acciones disponibles:</strong><br>
        · <code>POST /api/diagnostico/scores-paralelos/calcular-hoy</code> → trigger manual<br>
        · <code>POST /api/diagnostico/scores-paralelos/{{fecha}}/revision?decision=aprobado&nota=...</code><br>
        · <code>GET /api/diagnostico/scores-paralelos?dias=N</code> → JSON crudo
      </div>
    </body></html>
    """


# =====================================================================
# MATRIZ P×I 7 DÍAS CONSOLIDADA · Vista semanal de factores de riesgo
# =====================================================================
@router.get("/api/matriz/consolidada-7d")
async def matriz_consolidada_7d_api(
    dias: int = Query(7, ge=1, le=30),
    top_n: int | None = Query(None, ge=1, le=100),
):
    """Matriz consolidada de los últimos N días.

    Para cada factor de riesgo único calcula:
      · prob/impacto/score media + máx + percentil 90
      · slope de regresión + etiqueta de tendencia
      · velocidad (Δ último día)
      · serie completa de scores día a día
    """
    try:
        try:
            from .analyzers.matriz_consolidada_7d import construir_matriz_consolidada_7d
        except ImportError:
            from apurisk.analyzers.matriz_consolidada_7d import construir_matriz_consolidada_7d
        archive = _get_archive()
        return construir_matriz_consolidada_7d(archive, dias=dias, top_n=top_n)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/matriz-7d", response_class=HTMLResponse)
async def matriz_consolidada_7d_dashboard(dias: int = Query(7, ge=1, le=30)):
    """Dashboard HTML visualmente atractivo con la matriz consolidada."""
    try:
        try:
            from .analyzers.matriz_consolidada_7d import construir_matriz_consolidada_7d
        except ImportError:
            from apurisk.analyzers.matriz_consolidada_7d import construir_matriz_consolidada_7d
        archive = _get_archive()
        data = construir_matriz_consolidada_7d(archive, dias=dias)
        return HTMLResponse(content=_render_matriz_7d_html(data))
    except Exception as e:
        return HTMLResponse(
            content=f"<html><body style='font-family:monospace;background:#0f172a;color:#f8fafc;padding:40px;'>"
                    f"<h1 style='color:#ef4444;'>Matriz 7d · Error</h1>"
                    f"<p>{_esc_html(str(e))}</p></body></html>",
            status_code=500,
        )


def _render_matriz_7d_html(data: dict) -> str:
    """Renderiza la matriz como HTML con heatmap, sparklines y badges de tendencia."""
    factores = data.get("factores", [])
    periodo = data.get("periodo", {})
    n_corridas = data.get("n_corridas", 0)
    fechas = periodo.get("fechas", [])
    error = data.get("error")

    # Color por nivel consolidado (NAVY brand para el header)
    COLOR_NIVEL = {
        "CRÍTICO": "#ef4444",
        "ALTO":    "#f97316",
        "MEDIO":   "#f59e0b",
        "BAJO":    "#84cc16",
    }
    # Color del slope (tendencia)
    COLOR_TENDENCIA = {
        "escalada": "#dc2626", "ascenso":  "#f97316",
        "estable":  "#94a3b8", "descenso": "#22c55e",
        "caida":    "#16a34a",
    }

    if error:
        cuerpo_html = (f'<tr><td colspan="11" style="padding:32px;text-align:center;'
                       f'color:#94a3b8;">⚠ {error}</td></tr>')
    elif not factores:
        cuerpo_html = (f'<tr><td colspan="11" style="padding:32px;text-align:center;'
                       f'color:#94a3b8;">Sin factores de riesgo en los últimos {periodo.get("dias", 7)} días. '
                       f'Espera a que el scheduler corra ciclos OSINT.</td></tr>')
    else:
        filas = []
        for f in factores:
            nivel = f.get("nivel_consolidado", "BAJO")
            color_nivel = COLOR_NIVEL.get(nivel, "#94a3b8")
            tendencia = f.get("tendencia_label", "estable")
            color_t = COLOR_TENDENCIA.get(tendencia, "#94a3b8")
            arrow = f.get("tendencia_arrow", "→")
            serie = f.get("serie", [])
            velocidad = f.get("velocidad", 0.0)
            vel_color = "#ef4444" if velocidad > 2 else ("#22c55e" if velocidad < -2 else "#94a3b8")

            # Sparkline SVG (mini gráfico de la serie de 7 días)
            sparkline = ""
            if serie and len(serie) >= 2:
                vmin = min(serie)
                vmax = max(serie)
                rng = max(1.0, vmax - vmin)
                w, h = 90, 28
                puntos = []
                for i, v in enumerate(serie):
                    x = (i / (len(serie) - 1)) * (w - 2) + 1
                    y = h - 2 - ((v - vmin) / rng) * (h - 4)
                    puntos.append(f"{x:.1f},{y:.1f}")
                path = " ".join(puntos)
                ultimo_x = (w - 2) + 1
                ultimo_y = h - 2 - ((serie[-1] - vmin) / rng) * (h - 4)
                sparkline = (
                    f'<svg width="{w}" height="{h}" style="display:block;">'
                    f'<polyline points="{path}" fill="none" stroke="{color_nivel}" '
                    f'stroke-width="1.6" stroke-linejoin="round"/>'
                    f'<circle cx="{ultimo_x:.1f}" cy="{ultimo_y:.1f}" r="2.4" '
                    f'fill="{color_nivel}"/></svg>'
                )

            categoria = (f.get("categoria") or "—")[:18]
            filas.append(f"""
            <tr style="border-bottom:1px solid #1e293b;">
              <td style="padding:10px 8px;color:#f8fafc;font-weight:600;font-size:13px;">
                {_esc_html(f.get("nombre", ""))}
                <div style="color:#64748b;font-size:10px;margin-top:2px;text-transform:uppercase;letter-spacing:0.5px;">{_esc_html(categoria)}</div>
              </td>
              <td style="padding:10px 8px;text-align:center;">
                <span style="background:{color_nivel};color:white;padding:3px 10px;border-radius:10px;font-size:10.5px;font-weight:700;letter-spacing:0.5px;">{nivel}</span>
              </td>
              <td style="padding:10px 8px;text-align:right;color:#fbbf24;font-size:14px;font-weight:600;">{f.get("score_media", 0)}</td>
              <td style="padding:10px 8px;text-align:right;color:#cbd5e1;font-size:13px;">{f.get("score_max", 0)}</td>
              <td style="padding:10px 8px;text-align:right;color:#94a3b8;font-size:12px;">{f.get("score_p90", 0)}</td>
              <td style="padding:10px 8px;text-align:right;color:#a5b4fc;font-size:13px;">{f.get("prob_media", 0)}<span style="color:#475569;"> / {f.get("prob_max", 0)}</span></td>
              <td style="padding:10px 8px;text-align:right;color:#fda4af;font-size:13px;">{f.get("impacto_media", 0)}</td>
              <td style="padding:10px 8px;">{sparkline}</td>
              <td style="padding:10px 8px;text-align:center;">
                <span style="color:{color_t};font-size:16px;font-weight:bold;">{arrow}</span>
                <div style="color:{color_t};font-size:9.5px;text-transform:uppercase;letter-spacing:0.5px;margin-top:2px;">{tendencia}</div>
              </td>
              <td style="padding:10px 8px;text-align:right;color:{vel_color};font-size:12px;font-weight:600;">{velocidad:+.1f}</td>
              <td style="padding:10px 8px;text-align:center;color:#64748b;font-size:11px;">{f.get("n_apariciones", 0)}/{n_corridas}</td>
            </tr>
            """)
        cuerpo_html = "".join(filas)

    fechas_header = ""
    if fechas:
        fechas_header = (f'<span style="color:#64748b;font-size:12px;">'
                         f'Periodo: {fechas[0]} → {fechas[-1]} · {n_corridas} corridas diarias</span>')

    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"/>
<title>THALOS · Matriz P×I 7 días Consolidada</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
          background: #0f172a; color: #f8fafc; margin: 0; padding: 32px; }}
  h1 {{ color: #60a5fa; margin: 0 0 4px 0; font-size: 22px; }}
  .subtitle {{ color: #94a3b8; margin-bottom: 28px; font-size: 13px; }}
  .stats {{ display: flex; gap: 20px; margin-bottom: 24px; }}
  .stat {{ background: #1e293b; padding: 14px 20px; border-radius: 8px; border-left: 3px solid #60a5fa; }}
  .stat-label {{ font-size: 10px; text-transform: uppercase;
                  letter-spacing: 1px; color: #64748b; }}
  .stat-value {{ font-size: 26px; color: #f8fafc; font-weight: 700; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; background: #1e293b;
            border-radius: 12px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }}
  thead th {{ background: #1e3a8a; color: white; padding: 14px 8px;
              text-align: center; font-size: 10.5px; text-transform: uppercase;
              letter-spacing: 0.8px; }}
  thead th.left {{ text-align: left; }}
  thead th.right {{ text-align: right; }}
  .acciones {{ margin-top: 24px; padding: 16px; background: #1e293b;
                border-radius: 8px; font-size: 12px; color: #94a3b8; }}
  code {{ background: #0f172a; color: #a855f7; padding: 2px 6px; border-radius: 4px; }}
  a {{ color: #60a5fa; }}
</style>
</head><body>

<h1>🎯 Matriz P×I · Consolidada {periodo.get("dias", 7)} días</h1>
<div class="subtitle">
  Vista agregada de factores de riesgo del periodo. Score media + máx + percentil 90 + tendencia +
  velocidad por factor. Insumo principal del Reporte Semanal y Strategic Weekly Outlook.<br>
  {fechas_header}
</div>

<div class="stats">
  <div class="stat">
    <div class="stat-label">Factores</div>
    <div class="stat-value">{data.get("n_factores", 0)}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Corridas diarias</div>
    <div class="stat-value">{n_corridas}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Periodo</div>
    <div class="stat-value" style="font-size:14px;line-height:32px;">{periodo.get("dias", 7)} días</div>
  </div>
</div>

<table>
  <thead><tr>
    <th class="left">FACTOR · CATEGORÍA</th>
    <th>NIVEL</th>
    <th class="right">SCORE MEDIA</th>
    <th class="right">SCORE MÁX</th>
    <th class="right">P90</th>
    <th class="right">PROB (μ/máx)</th>
    <th class="right">IMPACTO</th>
    <th>SERIE 7d</th>
    <th>TENDENCIA</th>
    <th class="right">VELOC</th>
    <th>APARIC.</th>
  </tr></thead>
  <tbody>{cuerpo_html}</tbody>
</table>

<div class="acciones">
  <strong>Endpoints relacionados:</strong><br>
  · <code>GET /api/matriz/consolidada-7d?dias=N&amp;top_n=10</code> → JSON crudo<br>
  · <code>GET /matriz-7d?dias=14</code> → este mismo dashboard con otro periodo<br>
  · <a href="/diagnostico/scores-paralelos">/diagnostico/scores-paralelos</a> → validación v1 vs v2
</div>

</body></html>"""


# =====================================================================
# MATRIZ RETROSPECTIVA 7D · Quadrant Chart con vectores de movimiento
# =====================================================================
@router.get("/api/matriz/retrospectiva-7d")
async def matriz_retrospectiva_7d_api(dias: int = Query(7, ge=2, le=30)):
    """Matriz retrospectiva con tendencia direccional (ΔP, ΔI, VT, CT, MC, STF)."""
    try:
        try:
            from .analyzers.matriz_retrospectiva_7d import construir_matriz_retrospectiva_7d
        except ImportError:
            from apurisk.analyzers.matriz_retrospectiva_7d import construir_matriz_retrospectiva_7d
        archive = _get_archive()
        return construir_matriz_retrospectiva_7d(archive, dias=dias)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/matriz-retrospectiva-7d", response_class=HTMLResponse)
async def matriz_retrospectiva_7d_dashboard(dias: int = Query(7, ge=2, le=30)):
    """Dashboard HTML con Quadrant Chart P×I + trayectorias de movimiento."""
    try:
        try:
            from .analyzers.matriz_retrospectiva_7d import construir_matriz_retrospectiva_7d
        except ImportError:
            from apurisk.analyzers.matriz_retrospectiva_7d import construir_matriz_retrospectiva_7d
        archive = _get_archive()
        data = construir_matriz_retrospectiva_7d(archive, dias=dias)
        return HTMLResponse(content=_render_matriz_retrospectiva_html(data))
    except Exception as e:
        return HTMLResponse(
            content=f"<html><body style='font-family:monospace;background:#0f172a;color:#f8fafc;padding:40px;'>"
                    f"<h1 style='color:#ef4444;'>Matriz Retrospectiva · Error</h1>"
                    f"<p>{_esc_html(str(e))}</p></body></html>",
            status_code=500,
        )


def _render_matriz_retrospectiva_html(data: dict) -> str:
    """Renderiza el dashboard con Quadrant Chart SVG nativo + vectores de movimiento."""
    factores = data.get("factores", [])
    periodo = data.get("periodo", {})
    n_corridas = data.get("n_corridas", 0)
    top_mov = data.get("top_movedores", {})
    formulas = data.get("formulas", {})
    error = data.get("error")

    # === SVG Quadrant Chart ===
    # ViewBox 1600x900 — más amplio para que las burbujas respiren.
    # CSS hace que el SVG ocupe 100% del ancho disponible.
    W, H = 1600, 900
    PADX, PADY = 110, 100
    plot_w = W - 2 * PADX
    plot_h = 680

    def px(p):  # probabilidad → x svg
        return PADX + (p / 100.0) * plot_w
    def py(i):  # impacto → y svg (invertido)
        return PADY + plot_h - (i / 100.0) * plot_h

    # Cuadrantes con tintes sutiles
    quadrants = (
        # Alto-Alto (rojo claro)
        f'<rect x="{px(50)}" y="{py(100)}" width="{px(100)-px(50)}" height="{py(50)-py(100)}" fill="#fef2f2" fill-opacity="0.06"/>'
        # Alto-Bajo (ambar claro)
        f'<rect x="{px(50)}" y="{py(50)}" width="{px(100)-px(50)}" height="{py(0)-py(50)}" fill="#fef3c7" fill-opacity="0.04"/>'
        # Bajo-Alto (naranja claro)
        f'<rect x="{px(0)}" y="{py(100)}" width="{px(50)-px(0)}" height="{py(50)-py(100)}" fill="#ffedd5" fill-opacity="0.04"/>'
        # Bajo-Bajo (verde claro)
        f'<rect x="{px(0)}" y="{py(50)}" width="{px(50)-px(0)}" height="{py(0)-py(50)}" fill="#f0fdf4" fill-opacity="0.04"/>'
    )
    # Líneas divisorias
    lineas = (
        f'<line x1="{px(50)}" y1="{py(0)}" x2="{px(50)}" y2="{py(100)}" stroke="#475569" stroke-width="0.6" stroke-dasharray="3,3"/>'
        f'<line x1="{px(0)}" y1="{py(50)}" x2="{px(100)}" y2="{py(50)}" stroke="#475569" stroke-width="0.6" stroke-dasharray="3,3"/>'
    )
    # Ejes
    ejes = (
        f'<line x1="{PADX}" y1="{py(0)}" x2="{px(100)}" y2="{py(0)}" stroke="#94a3b8" stroke-width="1.2"/>'
        f'<line x1="{PADX}" y1="{py(0)}" x2="{PADX}" y2="{py(100)}" stroke="#94a3b8" stroke-width="1.2"/>'
    )
    # Marcas en ejes (más grandes para el viewBox ampliado)
    marcas = ""
    for v in (0, 25, 50, 75, 100):
        marcas += f'<line x1="{px(v)}" y1="{py(0)}" x2="{px(v)}" y2="{py(0)+8}" stroke="#64748b" stroke-width="1.5"/>'
        marcas += f'<text x="{px(v)}" y="{py(0)+32}" fill="#cbd5e1" font-size="18" font-weight="600" text-anchor="middle">{v}</text>'
        marcas += f'<line x1="{PADX-8}" y1="{py(v)}" x2="{PADX}" y2="{py(v)}" stroke="#64748b" stroke-width="1.5"/>'
        marcas += f'<text x="{PADX-14}" y="{py(v)+6}" fill="#cbd5e1" font-size="18" font-weight="600" text-anchor="end">{v}</text>'
    # Etiquetas de ejes — más prominentes
    etiq_ejes = (
        f'<text x="{px(50)}" y="{py(0)+70}" fill="#f8fafc" font-size="20" font-weight="bold" text-anchor="middle">PROBABILIDAD →</text>'
        f'<text transform="translate(35,{(py(0)+py(100))/2}) rotate(-90)" fill="#f8fafc" font-size="20" font-weight="bold" text-anchor="middle">IMPACTO →</text>'
    )
    # Cuadrante labels (esquinas) — más grandes y visibles
    labels_cuadrantes = (
        f'<text x="{px(98)}" y="{py(96)}" fill="#fca5a5" font-size="16" text-anchor="end" font-weight="bold" opacity="0.8">⚠ RIESGO CRÍTICO</text>'
        f'<text x="{px(2)}" y="{py(96)}" fill="#fdba74" font-size="14" font-weight="600" opacity="0.7">Alto impacto · Baja probabilidad</text>'
        f'<text x="{px(98)}" y="{py(4)+18}" fill="#fde68a" font-size="14" text-anchor="end" font-weight="600" opacity="0.7">Baja prob · Alto impacto</text>'
        f'<text x="{px(2)}" y="{py(4)+18}" fill="#86efac" font-size="14" font-weight="600" opacity="0.7">✓ RIESGO BAJO</text>'
    )

    # Vectores de movimiento por factor — burbujas mucho más grandes
    vectores = ""
    burbujas = ""
    etiquetas = ""
    for f in factores:
        p0, i0 = f["p_hace_7d"], f["i_hace_7d"]
        p1, i1 = f["p_actual"], f["i_actual"]
        color = f["tendencia_color"]
        # Radio MUCHO más grande (12-42 px en viewBox 1600)
        radio = max(18, min(48, 18 + abs(f["stf"]) / 2.5))

        # Vector (cola) — más gruesa
        if abs(p1 - p0) > 0.5 or abs(i1 - i0) > 0.5:
            vectores += (
                f'<line x1="{px(p0):.1f}" y1="{py(i0):.1f}" '
                f'x2="{px(p1):.1f}" y2="{py(i1):.1f}" '
                f'stroke="{color}" stroke-width="3.5" stroke-opacity="0.7" '
                f'stroke-linecap="round" />'
            )
            # Punto origen
            vectores += (
                f'<circle cx="{px(p0):.1f}" cy="{py(i0):.1f}" r="6" '
                f'fill="{color}" fill-opacity="0.35"/>'
            )

        # Burbuja actual
        nombre_esc = _esc_html(f["nombre"])
        cat_esc = _esc_html(f.get("categoria", ""))
        burbujas += (
            f'<g class="factor-group" data-factor-id="{_esc_html(f["factor_id"])}">'
            f'<circle cx="{px(p1):.1f}" cy="{py(i1):.1f}" r="{radio}" '
            f'fill="{color}" fill-opacity="0.88" stroke="#0f172a" stroke-width="3" '
            f'data-nombre="{nombre_esc}" '
            f'data-categoria="{cat_esc}" '
            f'data-p-actual="{f["p_actual"]}" data-p-hace="{f["p_hace_7d"]}" data-delta-p="{f["delta_p"]:+}" '
            f'data-i-actual="{f["i_actual"]}" data-i-hace="{f["i_hace_7d"]}" data-delta-i="{f["delta_i"]:+}" '
            f'data-score-actual="{f["score_actual"]}" data-score-hace="{f["score_hace_7d"]}" '
            f'data-mc="{f["mc"]:+}" data-vt="{f["vt"]:+.2f}" data-ct="{f["ct"]}" '
            f'data-stf="{f["stf"]:+}" data-tendencia="{f["tendencia_label"]}" '
            f'style="cursor:pointer; transition: r 0.2s, stroke-width 0.2s;"/>'
            f'</g>'
        )
        # Etiqueta del factor
        etiq_y = py(i1) - radio - 8
        nombre_corto = f["nombre"][:28] + ("…" if len(f["nombre"]) > 28 else "")
        etiquetas += (
            f'<text x="{px(p1):.1f}" y="{etiq_y:.1f}" '
            f'fill="#f8fafc" font-size="14" font-weight="700" '
            f'text-anchor="middle" style="pointer-events:none; '
            f'text-shadow: 0 0 4px #0f172a, 0 0 6px #0f172a;">{_esc_html(nombre_corto)}</text>'
        )

    chart_svg = f"""
    <svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" id="chart-svg"
         preserveAspectRatio="xMidYMid meet"
         style="background:#1e293b; border-radius:12px; width:100%; height:auto; display:block;">
      {quadrants}{lineas}{ejes}{marcas}{etiq_ejes}{labels_cuadrantes}
      {vectores}
      {burbujas}
      {etiquetas}
    </svg>
    """

    # === Top movedores ===
    def _render_mov(items: list, titulo: str, color: str) -> str:
        if not items:
            return f'<div style="color:#64748b;font-size:12px;">Ninguno detectado en el periodo.</div>'
        filas = ""
        for it in items[:4]:
            filas += (
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:8px 10px;background:#0f172a;border-radius:6px;margin-bottom:6px;border-left:3px solid {color};">'
                f'<div><div style="font-weight:600;color:#f8fafc;font-size:13px;">{_esc_html(it["nombre"])}</div>'
                f'<div style="color:#64748b;font-size:10px;text-transform:uppercase;">{_esc_html(it.get("categoria", ""))}</div></div>'
                f'<div style="text-align:right;"><div style="color:{color};font-weight:700;font-size:15px;">{it["stf"]:+.1f}</div>'
                f'<div style="color:#94a3b8;font-size:10px;">{it["tendencia_label"]}</div></div>'
                f'</div>'
            )
        return filas

    escalando_html = _render_mov(top_mov.get("escalando", []), "Escalando", "#ef4444")
    atenuandose_html = _render_mov(top_mov.get("atenuandose", []), "Atenuándose", "#22c55e")

    # === Fórmulas (panel colapsable) ===
    formulas_html = ""
    if formulas:
        umbrales_html = ""
        for k, v in formulas.get("umbrales", {}).items():
            umbrales_html += f'<div style="color:#cbd5e1;font-size:12px;margin:4px 0;font-family:monospace;">{_esc_html(k)} → <span style="color:#60a5fa;">{_esc_html(v)}</span></div>'
        formulas_html = f"""
        <details style="margin-top:24px;background:#1e293b;border-radius:8px;padding:16px;">
          <summary style="cursor:pointer;color:#60a5fa;font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:0.8px;">📐 Cómo se calcula · Fórmulas explícitas</summary>
          <div style="margin-top:14px;padding:12px;background:#0f172a;border-radius:6px;font-family:monospace;font-size:12.5px;line-height:1.9;color:#cbd5e1;">
            <div><span style="color:#a855f7;">ΔP</span> = {_esc_html(formulas.get("delta_p", ""))}</div>
            <div><span style="color:#a855f7;">ΔI</span> = {_esc_html(formulas.get("delta_i", ""))}</div>
            <div><span style="color:#a855f7;">VT</span> = {_esc_html(formulas.get("vt", ""))}</div>
            <div><span style="color:#a855f7;">CT</span> = {_esc_html(formulas.get("ct", ""))}</div>
            <div><span style="color:#a855f7;">MC</span> = {_esc_html(formulas.get("mc", ""))}</div>
            <div style="margin-top:8px;color:#fbbf24;font-weight:600;">STF = {_esc_html(formulas.get("stf", ""))}</div>
          </div>
          <div style="margin-top:14px;padding-top:12px;border-top:1px solid #334155;">
            <div style="color:#94a3b8;font-size:11px;text-transform:uppercase;margin-bottom:8px;">Umbrales de clasificación</div>
            {umbrales_html}
          </div>
        </details>
        """

    # === Error o sin datos ===
    if error:
        contenido = f'<div style="background:#7f1d1d;padding:24px;border-radius:8px;color:#fee2e2;">⚠ {_esc_html(error)}</div>'
    elif not factores:
        contenido = (
            '<div style="background:#1e293b;padding:48px;border-radius:12px;text-align:center;color:#94a3b8;">'
            f'<div style="font-size:48px;margin-bottom:12px;">📊</div>'
            f'<div style="font-size:14px;">Sin factores con suficiente historia en los últimos {periodo.get("dias", 7)} días.</div>'
            '<div style="font-size:12px;margin-top:8px;color:#64748b;">Se necesitan al menos 2 corridas del scheduler OSINT.</div>'
            '</div>'
        )
    else:
        contenido = f"""
        <!-- CHART ANCHO COMPLETO -->
        <div style="width:100%;">
          {chart_svg}
        </div>

        <!-- Hint debajo del chart -->
        <div style="margin-top:14px;padding:12px 18px;background:#0f172a;border-radius:8px;color:#cbd5e1;font-size:13px;line-height:1.7;">
          💡 <strong>Cómo leer el mapa:</strong> cada burbuja es un factor de riesgo en su posición ACTUAL (probabilidad × impacto).
          La <strong style="color:#fbbf24;">cola</strong> conecta con donde estaba hace {periodo.get("dias", 7)} días.
          El <strong style="color:#fbbf24;">color</strong> indica la dirección de la tendencia.
          El <strong style="color:#fbbf24;">tamaño</strong> es proporcional a |STF| (magnitud del cambio).
          Haz <strong style="color:#fbbf24;">hover</strong> sobre cualquier burbuja para ver todas las métricas detalladas.
        </div>

        <!-- LEYENDAS DEBAJO DEL GRÁFICO -->
        <div style="display:grid;grid-template-columns: 1fr 1fr; gap:20px; margin-top:24px;">
          <div style="background:#1e293b;border-radius:10px;padding:18px;border-top:3px solid #ef4444;">
            <div style="color:#ef4444;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px;display:flex;align-items:center;gap:8px;">
              🔥 Top Escalando · presión creciente
            </div>
            {escalando_html}
          </div>
          <div style="background:#1e293b;border-radius:10px;padding:18px;border-top:3px solid #22c55e;">
            <div style="color:#22c55e;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px;display:flex;align-items:center;gap:8px;">
              🌿 Top Atenuándose · presión cediendo
            </div>
            {atenuandose_html}
          </div>
        </div>

        <!-- LEYENDA DE COLORES -->
        <div style="margin-top:20px;background:#1e293b;border-radius:10px;padding:18px;">
          <div style="color:#cbd5e1;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px;">Leyenda de tendencias</div>
          <div style="display:flex;flex-wrap:wrap;gap:18px;font-size:12px;">
            <div style="display:flex;align-items:center;gap:8px;"><span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:#dc2626;"></span><strong style="color:#fca5a5;">ESCALANDO</strong> <span style="color:#94a3b8;">STF ≥ +20</span></div>
            <div style="display:flex;align-items:center;gap:8px;"><span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:#f97316;"></span><strong style="color:#fdba74;">SUBIDA</strong> <span style="color:#94a3b8;">STF +10 a +20</span></div>
            <div style="display:flex;align-items:center;gap:8px;"><span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:#94a3b8;"></span><strong style="color:#cbd5e1;">ESTABLE</strong> <span style="color:#94a3b8;">STF −10 a +10</span></div>
            <div style="display:flex;align-items:center;gap:8px;"><span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:#84cc16;"></span><strong style="color:#bef264;">DESCENSO</strong> <span style="color:#94a3b8;">STF −10 a −20</span></div>
            <div style="display:flex;align-items:center;gap:8px;"><span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:#22c55e;"></span><strong style="color:#86efac;">ATENUÁNDOSE</strong> <span style="color:#94a3b8;">STF &lt; −20</span></div>
          </div>
        </div>

        {formulas_html}
        """

    n_factores = data.get("n_factores", 0)
    n_escalando = len(top_mov.get("escalando", []))
    n_atenuandose = len(top_mov.get("atenuandose", []))
    fechas_str = ""
    if periodo.get("fechas"):
        fechas_str = f'{periodo["fechas"][0]} → {periodo["fechas"][-1]} · {n_corridas} corridas'

    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"/>
<title>THALOS · Matriz Retrospectiva 7 días</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background:#0f172a;
          color:#f8fafc; margin:0; padding:24px 32px; }}
  h1 {{ color:#60a5fa; margin:0 0 4px 0; font-size:22px; }}
  .subtitle {{ color:#94a3b8; margin-bottom:24px; font-size:13px; }}
  .stats {{ display:flex; gap:16px; margin-bottom:24px; }}
  .stat {{ background:#1e293b; padding:14px 20px; border-radius:8px; border-left:3px solid #60a5fa; }}
  .stat-label {{ font-size:10px; text-transform:uppercase; letter-spacing:1px; color:#64748b; }}
  .stat-value {{ font-size:24px; color:#f8fafc; font-weight:700; margin-top:2px; }}
  #tooltip {{ position:fixed; pointer-events:none; background:#0f172a;
              border:1px solid #334155; border-radius:8px; padding:12px 14px;
              font-size:12px; color:#f8fafc; box-shadow:0 4px 20px rgba(0,0,0,0.5);
              display:none; z-index:1000; min-width:240px; }}
  #tooltip .row {{ display:flex; justify-content:space-between; margin:3px 0; }}
  #tooltip .label {{ color:#64748b; font-size:10.5px; text-transform:uppercase; letter-spacing:0.4px;}}
  #tooltip .value {{ color:#f8fafc; font-weight:600; font-family:monospace; }}
  #tooltip .stf-badge {{ display:inline-block; padding:3px 8px; border-radius:10px;
                          font-size:10.5px; font-weight:700; margin-top:6px; }}
  .acciones {{ margin-top:24px; padding:14px; background:#1e293b; border-radius:8px;
                font-size:12px; color:#94a3b8; }}
  code {{ background:#0f172a; color:#a855f7; padding:2px 6px; border-radius:4px; }}
  a {{ color:#60a5fa; }}
</style>
</head><body>

<h1>🎯 Matriz Retrospectiva P×I · Tendencia {periodo.get("dias", 7)} días</h1>
<div class="subtitle">
  Posición actual de cada factor + vector de movimiento desde hace {periodo.get("dias", 7)} días.
  Útil para detectar qué riesgos están escalando o atenuándose.<br>
  {fechas_str}
</div>

<div class="stats">
  <div class="stat"><div class="stat-label">Factores</div><div class="stat-value">{n_factores}</div></div>
  <div class="stat" style="border-left-color:#ef4444;"><div class="stat-label">🔥 Escalando</div><div class="stat-value" style="color:#fca5a5;">{n_escalando}</div></div>
  <div class="stat" style="border-left-color:#22c55e;"><div class="stat-label">🌿 Atenuándose</div><div class="stat-value" style="color:#86efac;">{n_atenuandose}</div></div>
  <div class="stat"><div class="stat-label">Corridas</div><div class="stat-value">{n_corridas}</div></div>
</div>

{contenido}

<div class="acciones">
  <strong>Endpoints relacionados:</strong>
  · <code>GET /api/matriz/retrospectiva-7d?dias=N</code> · JSON crudo
  · <a href="/matriz-7d">/matriz-7d</a> matriz consolidada agregada
  · <a href="/diagnostico/scores-paralelos">/diagnostico/scores-paralelos</a> validación v1↔v2
</div>

<div id="tooltip"></div>

<script>
(function() {{
  const svg = document.getElementById('chart-svg');
  const tt = document.getElementById('tooltip');
  if (!svg) return;
  const groups = svg.querySelectorAll('.factor-group circle');
  groups.forEach(function(c) {{
    c.addEventListener('mouseenter', function(e) {{
      const d = c.dataset;
      tt.innerHTML = '<div style="font-weight:700;font-size:13px;color:#60a5fa;">' + d.nombre +
        '</div><div style="color:#64748b;font-size:10px;text-transform:uppercase;margin-bottom:8px;">' + d.categoria + '</div>' +
        '<div class="row"><span class="label">P (hoy / hace ' + ({periodo.get("dias", 7)}) + 'd)</span><span class="value">' + d.pActual + ' / ' + d.pHace + '</span></div>' +
        '<div class="row"><span class="label">ΔP</span><span class="value" style="color:' + (parseFloat(d.deltaP) > 0 ? '#ef4444' : '#22c55e') + ';">' + d.deltaP + '</span></div>' +
        '<div class="row"><span class="label">I (hoy / hace ' + ({periodo.get("dias", 7)}) + 'd)</span><span class="value">' + d.iActual + ' / ' + d.iHace + '</span></div>' +
        '<div class="row"><span class="label">ΔI</span><span class="value" style="color:' + (parseFloat(d.deltaI) > 0 ? '#ef4444' : '#22c55e') + ';">' + d.deltaI + '</span></div>' +
        '<hr style="border:none;border-top:1px solid #334155;margin:8px 0;">' +
        '<div class="row"><span class="label">Score (hoy / hace)</span><span class="value">' + d.scoreActual + ' / ' + d.scoreHace + '</span></div>' +
        '<div class="row"><span class="label">VT puntos/día</span><span class="value">' + d.vt + '</span></div>' +
        '<div class="row"><span class="label">CT consistencia</span><span class="value">' + d.ct + '</span></div>' +
        '<div class="row"><span class="label">MC magnitud</span><span class="value">' + d.mc + '</span></div>' +
        '<div class="row"><span class="label" style="font-size:11px;font-weight:700;color:#fbbf24;">STF score final</span><span class="value" style="color:#fbbf24;font-size:14px;">' + d.stf + '</span></div>' +
        '<div class="stf-badge" style="background:' + c.getAttribute('fill') + ';color:white;">' + d.tendencia + '</div>';
      tt.style.display = 'block';
      c.setAttribute('r', parseFloat(c.getAttribute('r')) + 2);
    }});
    c.addEventListener('mousemove', function(e) {{
      const x = e.clientX + 15;
      const y = e.clientY + 15;
      const ttRect = tt.getBoundingClientRect();
      const maxX = window.innerWidth - ttRect.width - 10;
      tt.style.left = Math.min(x, maxX) + 'px';
      tt.style.top = y + 'px';
    }});
    c.addEventListener('mouseleave', function() {{
      tt.style.display = 'none';
      c.setAttribute('r', parseFloat(c.getAttribute('r')) - 2);
    }});
  }});
}})();
</script>

</body></html>"""


@router.get("/api/edi/snapshot")
async def edi_snapshot():
    """Estado de Derecho Index (EDI) — snapshot actual.

    Devuelve el EDI calculado sobre ventana móvil de últimos 7 días:
      - Score 0-100 con etiqueta (SÓLIDO/ESTABLE/TENSIONADO/FRÁGIL/CRÍTICO)
      - Banda de confianza ±
      - 4 sub-componentes con sus drivers
      - Tendencia vs 7 días atrás
      - Top 5 drivers cruzados
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

    # Intelligence brief (insumo de convergencias e I&W)
    intel = None
    try:
        try:
            from .analyzers.intelligence_engine import generar_intelligence_brief
        except ImportError:
            from apurisk.analyzers.intelligence_engine import generar_intelligence_brief
        intel = generar_intelligence_brief(snap, archive=archive, dias_baseline=28)
    except Exception:
        intel = None

    try:
        try:
            from .analyzers.estado_derecho_index import calcular_edi
        except ImportError:
            from apurisk.analyzers.estado_derecho_index import calcular_edi
        edi = calcular_edi(snap, archive=archive, intelligence_brief=intel)
        return JSONResponse(
            content=edi,
            media_type="application/json; charset=utf-8",
        )
    except Exception as e:
        import traceback
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(e).__name__,
                "error_msg": str(e),
                "traceback": traceback.format_exc().splitlines()[-15:],
            }
        )


@router.get("/api/edi/serie")
async def edi_serie(dias: int = Query(14, ge=7, le=180)):
    """Serie temporal del EDI últimos N días.

    Default 14 días (lo que el histórico actual permite con confianza).
    Cuando el archive cruce 30 y 90 días, esos rangos se vuelven viables.
    """
    archive = None
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if db_path.exists():
        try:
            archive = ApuriskArchive(str(db_path))
        except Exception:
            pass
    if not archive:
        raise HTTPException(status_code=503, detail="Archive no disponible.")

    try:
        try:
            from .analyzers.estado_derecho_index import calcular_edi_serie
        except ImportError:
            from apurisk.analyzers.estado_derecho_index import calcular_edi_serie
        serie = calcular_edi_serie(archive, dias=dias)
        return JSONResponse(
            content=serie,
            media_type="application/json; charset=utf-8",
        )
    except Exception as e:
        import traceback
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(e).__name__,
                "error_msg": str(e),
                "traceback": traceback.format_exc().splitlines()[-15:],
            }
        )


@router.get("/api/diagnostico/historico-edi")
async def diagnostico_historico_edi():
    """Auditoría del histórico SQLite para evaluar factibilidad del
    Estado de Derecho Index (EDI).

    Reporta:
      - Rango temporal real (primer snapshot, último, días totales)
      - Densidad: snapshots/día observados vs esperados (cada 30 min = 48/día)
      - Gaps: días con menos de 4 snapshots (degradados)
      - Conteo de alertas por categoría/regla últimos 90 días
      - Conteo de factores P×I por id
      - VEREDICTO: qué series temporales son factibles
    """
    from datetime import datetime, timedelta, timezone
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if not db_path.exists():
        return {"error": "Archive SQLite no existe"}

    try:
        archive = ApuriskArchive(str(db_path))
        stats_base = archive.stats()

        with archive._conn() as c:
            # 1) Densidad por día
            rows_dias = c.execute("""
                SELECT DATE(generado) as fecha, COUNT(*) as n_snapshots,
                       MIN(generado) as primer, MAX(generado) as ultimo,
                       AVG(score_global) as score_promedio
                FROM snapshots
                GROUP BY DATE(generado)
                ORDER BY fecha ASC
            """).fetchall()
            dias_observados = [
                {
                    "fecha": r["fecha"],
                    "snapshots": r["n_snapshots"],
                    "score_promedio": round(r["score_promedio"], 1) if r["score_promedio"] else None,
                }
                for r in rows_dias
            ]

            # 2) Conteo alertas por regla últimos 90 días
            cutoff_90d = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            rows_reglas = c.execute("""
                SELECT regla, nivel, COUNT(*) as n
                FROM alertas
                WHERE timestamp >= ?
                GROUP BY regla, nivel
                ORDER BY n DESC
                LIMIT 50
            """, (cutoff_90d,)).fetchall()
            alertas_por_regla = [
                {"regla": r["regla"], "nivel": r["nivel"], "n": r["n"]}
                for r in rows_reglas
            ]

            # 3) Conteo factores únicos
            rows_factores = c.execute("""
                SELECT factor_id, COUNT(*) as n_observaciones,
                       AVG(score) as score_promedio,
                       MIN(score) as score_min, MAX(score) as score_max
                FROM factores
                GROUP BY factor_id
                ORDER BY n_observaciones DESC
            """).fetchall()
            factores_disponibles = [
                {
                    "factor_id": r["factor_id"],
                    "n_observaciones": r["n_observaciones"],
                    "score_avg": round(r["score_promedio"], 1) if r["score_promedio"] else None,
                    "rango": [round(r["score_min"], 1) if r["score_min"] else None,
                             round(r["score_max"], 1) if r["score_max"] else None],
                }
                for r in rows_factores
            ]

            # 4) Conteo alertas institucionales específicas (críticas para EDI)
            reglas_edi_independencia = [
                'CRISIS_TRIBUNAL_CONSTITUCIONAL',
                'CRISIS_PODER_JUDICIAL',
                'CRISIS_ORGANOS_CONTROL',
                'CRISIS_INSTITUCIONAL_JUDICIAL',
            ]
            placeholders = ",".join("?" * len(reglas_edi_independencia))
            rows_inst = c.execute(f"""
                SELECT COUNT(*) as n FROM alertas
                WHERE regla IN ({placeholders}) AND timestamp >= ?
            """, (*reglas_edi_independencia, cutoff_90d)).fetchall()
            alertas_independencia_judicial_90d = rows_inst[0]["n"]

        # Calcular métricas derivadas
        total_dias = len(dias_observados)
        dias_densidad_ok = sum(1 for d in dias_observados if d["snapshots"] >= 12)  # >= 12 snapshots/día = densidad mínima aceptable
        dias_degradados = sum(1 for d in dias_observados if d["snapshots"] < 4)

        # Primer y último día
        primer_dia = dias_observados[0]["fecha"] if dias_observados else None
        ultimo_dia = dias_observados[-1]["fecha"] if dias_observados else None

        # Días continuos sin gaps (>=4 snapshots)
        dias_continuos_desde_ultimo = 0
        for d in reversed(dias_observados):
            if d["snapshots"] >= 4:
                dias_continuos_desde_ultimo += 1
            else:
                break

        # ===== VEREDICTO =====
        veredicto = {}
        if dias_continuos_desde_ultimo >= 90:
            veredicto["serie_90d"] = "✅ Factible"
            veredicto["serie_30d"] = "✅ Factible"
            veredicto["nivel_confianza"] = "ALTO"
            veredicto["recomendacion"] = ("Implementar EDI con ambas series temporales como se propuso. "
                                          "Suficiente histórico para análisis estructural confiable.")
        elif dias_continuos_desde_ultimo >= 60:
            veredicto["serie_90d"] = "⚠️ Parcial (recortar a {} días)".format(dias_continuos_desde_ultimo)
            veredicto["serie_30d"] = "✅ Factible"
            veredicto["nivel_confianza"] = "MEDIO"
            veredicto["recomendacion"] = ("Implementar EDI con serie 30d completa y serie larga "
                                          "limitada a {} días disponibles. Mostrar 'acumulando histórico' "
                                          "hasta cruzar 90 días.").format(dias_continuos_desde_ultimo)
        elif dias_continuos_desde_ultimo >= 30:
            veredicto["serie_90d"] = "❌ No factible aún (acumular más)"
            veredicto["serie_30d"] = "✅ Factible"
            veredicto["nivel_confianza"] = "MEDIO-BAJO"
            veredicto["recomendacion"] = ("Implementar EDI con solo serie 30d. Postponer serie 90d "
                                          "hasta tener {} días más de histórico.").format(90 - dias_continuos_desde_ultimo)
        elif dias_continuos_desde_ultimo >= 14:
            veredicto["serie_90d"] = "❌ No factible"
            veredicto["serie_30d"] = "⚠️ Parcial"
            veredicto["nivel_confianza"] = "BAJO"
            veredicto["recomendacion"] = ("Solo implementar EDI espontáneo (cálculo instantáneo "
                                          "sin serie temporal). Acumular {} días más para serie 30d.").format(30 - dias_continuos_desde_ultimo)
        else:
            veredicto["serie_90d"] = "❌ No factible"
            veredicto["serie_30d"] = "❌ No factible"
            veredicto["nivel_confianza"] = "INSUFICIENTE"
            veredicto["recomendacion"] = ("Histórico demasiado corto. Implementar EDI espontáneo "
                                          "solamente, sin promesa de series temporales hasta tener "
                                          "más datos.")

        # Factores requeridos por el EDI presentes
        factores_ids_observados = {f["factor_id"] for f in factores_disponibles}
        factores_edi_criticos = [
            "crisis_tc", "crisis_pj_corte_suprema", "crisis_organos_control",
            "vacancia_presidencial", "censura_gabinete", "investigacion_corrupcion",
            "corrupcion_sistemica", "regulacion_sectorial",
        ]
        factores_disponibles_para_edi = [
            f for f in factores_edi_criticos if f in factores_ids_observados
        ]
        factores_faltantes_edi = [
            f for f in factores_edi_criticos if f not in factores_ids_observados
        ]

        return {
            "veredicto": veredicto,
            "rango_temporal": {
                "primer_dia": primer_dia,
                "ultimo_dia": ultimo_dia,
                "total_dias_observados": total_dias,
                "dias_continuos_desde_ultimo": dias_continuos_desde_ultimo,
                "dias_degradados": dias_degradados,
                "dias_densidad_ok": dias_densidad_ok,
            },
            "stats_base": stats_base,
            "factores_edi": {
                "disponibles_para_edi": factores_disponibles_para_edi,
                "faltantes_para_edi": factores_faltantes_edi,
                "completitud_pct": round(100 * len(factores_disponibles_para_edi) / len(factores_edi_criticos), 1),
            },
            "alertas_independencia_judicial_90d": alertas_independencia_judicial_90d,
            "dias_observados_sample": dias_observados[:15] + (
                ["..."] if total_dias > 30 else []
            ) + (dias_observados[-15:] if total_dias > 30 else []),
            "alertas_por_regla_top": alertas_por_regla[:20],
            "factores_disponibles_top": factores_disponibles[:20],
        }
    except Exception as e:
        import traceback
        return {
            "error_type": type(e).__name__,
            "error_msg": str(e),
            "traceback_tail": traceback.format_exc().splitlines()[-10:],
        }


@router.get("/api/diagnostico/crisis-tc")
async def diagnostico_crisis_tc():
    """Diagnóstico end-to-end del flujo CRISIS_INSTITUCIONAL_JUDICIAL.

    Verifica en orden:
      1. Que la regla esté cargada en el deploy (no es bug de push)
      2. Que el factor crisis_institucional exista en la matriz
      3. Cuántos artículos del snapshot mencionan TC/magistrado/etc
      4. Cuántas alertas hay en el archive SQLite con esa regla
      5. Edad del último snapshot
    """
    resultado = {}

    # ===== 1. Regla cargada =====
    try:
        try:
            from .analyzers.alerts import REGLAS
        except ImportError:
            from apurisk.analyzers.alerts import REGLAS
        reglas_ids = [r.get("id") for r in REGLAS]
        regla_crisis = next((r for r in REGLAS if r.get("id") == "CRISIS_INSTITUCIONAL_JUDICIAL"), None)
        resultado["regla_cargada"] = regla_crisis is not None
        resultado["total_reglas_cargadas"] = len(REGLAS)
        if regla_crisis:
            resultado["regla_n_patrones"] = len(regla_crisis.get("patrones", []))
            resultado["regla_n_negaciones"] = len(regla_crisis.get("patrones_negacion", []))
            resultado["regla_sample_patrones"] = regla_crisis.get("patrones", [])[:5]
    except Exception as e:
        resultado["regla_cargada_error"] = str(e)

    # ===== 2. Factor P×I cargado =====
    try:
        try:
            from .analyzers.risk_matrix import FACTORES
        except ImportError:
            from apurisk.analyzers.risk_matrix import FACTORES
        factor_ids = [f.get("id") for f in FACTORES]
        resultado["factor_cargado"] = "crisis_institucional" in factor_ids
        resultado["total_factores_cargados"] = len(FACTORES)
    except Exception as e:
        resultado["factor_cargado_error"] = str(e)

    # ===== 3. Snapshot actual + búsqueda en artículos =====
    snap_path = _ultimo_snapshot_path()
    if snap_path:
        try:
            with open(snap_path, encoding="utf-8") as f:
                snap = json.load(f)
            resultado["snapshot_generado"] = snap.get("generado")

            articulos = snap.get("articulos", []) or []
            resultado["snapshot_n_articulos"] = len(articulos)

            # Búsqueda de keywords del TC en artículos
            keywords_test = [
                "tribunal constitucional", "tc renuncia", "tc presidenta",
                "magistrado", "magistrada", "poder judicial", "corte suprema",
                "junta nacional de justicia", "jnj",
            ]
            articulos_encontrados = {}
            for kw in keywords_test:
                kw_low = kw.lower()
                matches = []
                for a in articulos:
                    title = (a.get("title", "") or "").lower()
                    summary = (a.get("summary", "") or "").lower()
                    if kw_low in title or kw_low in summary:
                        matches.append({
                            "title": a.get("title", "")[:120],
                            "source": a.get("source_name", ""),
                            "published": a.get("published", ""),
                            "url": a.get("url", "")[:120],
                        })
                articulos_encontrados[kw] = {
                    "count": len(matches),
                    "samples": matches[:3],
                }
            resultado["busqueda_articulos"] = articulos_encontrados

            # Alertas del snapshot
            alertas = snap.get("alertas", []) or []
            resultado["snapshot_n_alertas_total"] = len(alertas)
            alertas_crisis = [a for a in alertas if a.get("regla") == "CRISIS_INSTITUCIONAL_JUDICIAL"]
            resultado["alertas_crisis_en_snapshot"] = len(alertas_crisis)
            if alertas_crisis:
                resultado["sample_alerta_crisis"] = alertas_crisis[0]

            # Probar el matching MANUAL sobre un artículo que mencione TC
            if articulos_encontrados.get("tribunal constitucional", {}).get("count", 0) > 0:
                primer_match = articulos_encontrados["tribunal constitucional"]["samples"][0]
                resultado["test_matching_manual"] = {
                    "articulo": primer_match,
                    "explicacion": "Existe artículo con 'tribunal constitucional' pero no genera alerta.",
                }
        except Exception as e:
            resultado["snapshot_error"] = str(e)
    else:
        resultado["snapshot_error"] = "No hay snapshot disponible"

    # ===== 4. Buscar alertas en archive SQLite (últimas 7 días) =====
    db_path = OUTPUT_DIR / "apurisk_archive.db"
    if db_path.exists():
        try:
            archive = ApuriskArchive(str(db_path))
            with archive._conn() as c:
                rows = c.execute("""
                    SELECT COUNT(*) as n, MAX(timestamp) as ultima
                    FROM alertas
                    WHERE regla = 'CRISIS_INSTITUCIONAL_JUDICIAL'
                """).fetchone()
                resultado["archive_alertas_crisis_total"] = rows["n"]
                resultado["archive_ultima_alerta_crisis"] = rows["ultima"]
        except Exception as e:
            resultado["archive_error"] = str(e)

    return resultado
