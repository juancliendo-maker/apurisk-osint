"""THALOS · Análisis Político — Últimas 24 Horas (Fase 3-3c).

Línea de producto SEPARADA del OSINT: pone los HECHOS del día en CONTEXTO
(prosa ejecutiva), no métricas crudas. La narrativa la redacta Claude vía API
bajo un prompt maestro que codifica la doctrina del Coronel (grounding estricto:
solo hechos provistos; describe y contextualiza, NO juzga — sin proyecciones,
hipótesis ni recomendaciones).

Salvaguardas:
  0. La API key vive SOLO en env (ANTHROPIC_API_KEY). Sin key → estado 'error'
     con mensaje claro.
  1. Grounding en el prompt maestro (config-editable).
  2. Fallback: si la API falla tras reintentos, PDF de respaldo (titulares +
     métricas) con nota de degradación visible; estado 'completado'.
  3. Modo calibración: marca visible en portada y footer mientras
     AP24_MODO_CALIBRACION=1.
"""
from __future__ import annotations
import os
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

from . import thalos_base as T
from .reporte_a import _grid_temas, _score_global_bloque, _fmt, escape_txt

_SECCIONES = ["SÍNTESIS DEL DÍA", "DESARROLLOS PRINCIPALES",
              "CONEXIONES Y CONTEXTO", "NOTA DE MATERIAL"]


def _material_para_llm(articulos: list, globos: list, riesgo: dict,
                       actores: list, ahora_iso: str) -> str:
    """Arma el bloque de material (hechos + métricas) que se envía como user."""
    lineas = [f"FECHA/HORA (Lima): {ahora_iso}",
              "VENTANA: últimas 24 horas.", "",
              "=== MÉTRICAS DEL MOTOR (para contexto, no para copiar como cifras) ===",
              f"Score Nacional: {riesgo.get('global','—')} ({riesgo.get('nivel','—')})",
              "Semáforo por tema (gravedad / actividad / urgencia / cuadrante):"]
    for g in sorted(globos, key=lambda x: x.get("y", 0), reverse=True):
        lineas.append(
            f"  - {g['tema'].replace('_',' ')}: grav {g.get('y',0):.0f} · "
            f"act {g.get('x',0):.1f} · {g.get('urgencia','—')} · {g.get('cuadrante','—')}")
    if actores:
        lineas.append("Actores de mayor peso: " +
                      ", ".join(f"{a.get('nombre','?')} (peso {a.get('peso_calculado',0):.0f})"
                                for a in actores[:5]))
    lineas += ["", f"=== HECHOS DEL DÍA — {len(articulos)} titulares de fuentes abiertas ==="]
    for a in articulos:
        fecha = (a.get("capturado_en") or "")[:16].replace("T", " ")
        fuente = a.get("source_name") or "fuente"
        titulo = (a.get("title") or "").strip()
        resumen = (a.get("summary") or "").strip()
        item = f"- [{fuente}] {titulo}"
        if resumen:
            item += f" — {resumen[:220]}"
        lineas.append(item)
    lineas += ["", "Redacta el Análisis Político siguiendo EXACTAMENTE la estructura "
               "y las reglas del system. Solo estos hechos; cita la fuente entre "
               "paréntesis al mencionar un hecho concreto."]
    return "\n".join(lineas)


def _parsear_secciones(texto: str) -> list:
    """Divide la salida del LLM en (encabezado, cuerpo) por las secciones esperadas."""
    import re
    if not texto:
        return []
    # localizar posiciones de cada encabezado (tolerante a mayúsculas/guiones)
    marcas = []
    for sec in _SECCIONES:
        m = re.search(re.escape(sec), texto, re.IGNORECASE)
        if m:
            marcas.append((m.start(), sec, m.end()))
    marcas.sort()
    if not marcas:
        return [("ANÁLISIS", texto.strip())]
    out = []
    for i, (ini, sec, fin) in enumerate(marcas):
        cuerpo_ini = fin
        cuerpo_fin = marcas[i + 1][0] if i + 1 < len(marcas) else len(texto)
        cuerpo = texto[cuerpo_ini:cuerpo_fin].strip().lstrip("—-:").strip()
        out.append((sec, cuerpo))
    return out


def _render_narrativa(secciones: list, st: dict) -> list:
    S = []
    for enc, cuerpo in secciones:
        S.append(Paragraph(escape_txt(enc.title()), st["h2"]))
        S.append(T.linea_oro())
        # cada párrafo/línea del cuerpo
        for parte in [p for p in cuerpo.split("\n") if p.strip()]:
            S.append(Paragraph(escape_txt(parte.strip()), st["body"]))
        S.append(Spacer(1, 8))
    return S


def generar_analisis_politico_24h(db_path: str, snapshot: dict,
                                  construir_semaforo, conteos_bd: dict = None) -> dict:
    """Genera el PDF del Análisis Político 24h. Devuelve {pdf, estado, nota}.

    estado: 'completado' (con narrativa o con fallback) | 'error' (sin key / sin foto).
    """
    from ..storage.config_loader import (
        cargar_parametros_ap24, articulos_ultimas_24h, listar_actores,
    )
    from ..utils.timezone_pe import now_pe_iso
    from ..utils.llm_client import redactar_con_sistema

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return {"pdf": None, "estado": "error",
                "nota": "API key no configurada — agregar ANTHROPIC_API_KEY en Render"}

    osint = (snapshot or {}).get("osint_motor")
    if not osint:
        return {"pdf": None, "estado": "error", "nota": "Sin snapshot OSINT disponible"}

    par = cargar_parametros_ap24(db_path)
    calibracion = bool(par.get("modo_calibracion", 1))
    md = construir_semaforo(osint, db_path, dias=1)   # ventana 24h para el semáforo
    globos = md.get("globos_b", [])
    riesgo = (snapshot or {}).get("riesgo", {}) or {}
    articulos = articulos_ultimas_24h(db_path, par.get("top_n", 120))
    actores = listar_actores(db_path, pais="PE", solo_activos=True)
    ahora = now_pe_iso()

    material = _material_para_llm(articulos, globos, riesgo, actores, ahora)
    narrativa, err = redactar_con_sistema(
        par.get("prompt_maestro", ""), material,
        max_tokens=par.get("max_tokens", 3000),
        model=par.get("modelo", "claude-sonnet-4-6"), reintentos=2)

    degradado = narrativa is None
    nota_deg = None
    if degradado:
        nota_deg = f"Narrativa no disponible — fallo de API ({err}). Versión de respaldo."

    # ── Ensamblado del PDF ──
    st = T.estilos()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=T.MARGEN_LAT, rightMargin=T.MARGEN_LAT,
                            topMargin=T.MARGEN_SUP, bottomMargin=T.MARGEN_INF,
                            title="Análisis Político · Últimas 24 Horas")
    estado_txt = "CALIBRACIÓN" if calibracion else "OPERATIVO"
    doc._fecha_footer = ahora[:10] + (" · VERSIÓN DE CALIBRACIÓN" if calibracion else "")
    doc._header_meta = "ANÁLISIS POLÍTICO · 24h · THALOS"
    gen = (snapshot.get("generado") or ahora)[:16].replace("T", " ")
    from datetime import timedelta
    from ..utils.timezone_pe import now_pe
    desde = (now_pe() - timedelta(hours=24)).isoformat(timespec="minutes")[:16].replace("T", " ")
    doc._portada = {
        "titulo": "Análisis Político — Últimas 24 Horas",
        "subtitulo": "Los hechos del día en contexto",
        "tema_rango": (f"Ventana: {desde} → {ahora[:16].replace('T',' ')} (Lima)"
                       + ("  ·  VERSIÓN DE CALIBRACIÓN" if calibracion else "")),
        "metadata": [
            ("Tipo", "Análisis Político · 24h · global"),
            ("Generado", ahora[:16].replace("T", " ") + " (America/Lima)"),
            ("Estado", estado_txt + (" · RESPALDO" if degradado else "")),
            ("Clasificación", "USO INTERNO"),
        ],
    }
    S = [PageBreak()]

    if calibracion:
        S.append(T.recuadro_ejecutivo(
            "VERSIÓN DE CALIBRACIÓN",
            "Este análisis está en fase de calibración de la redacción automática, "
            "sujeta a revisión analítica. No sustituye el juicio del analista.", st))
        S.append(Spacer(1, 10))

    if not degradado:
        secciones = _parsear_secciones(narrativa)
        S += _render_narrativa(secciones, st)
    else:
        S.append(Paragraph("Narrativa no disponible", st["h2"]))
        S.append(T.linea_oro())
        S.append(Paragraph(escape_txt(nota_deg), st["body"]))
        S.append(Paragraph("Se presenta la foto de métricas y los titulares del día "
                            "como respaldo.", st["body"]))

    # Tablero de métricas (grid 8 temas + Score Nacional) — junto a la prosa
    S.append(Spacer(1, 6))
    S.append(Paragraph("Tablero de métricas (semáforo 24h)", st["h2"]))
    S.append(T.linea_oro())
    S += _score_global_bloque(riesgo, st)
    S.append(Spacer(1, 8))
    if globos:
        S.append(_grid_temas(globos, st))
        S.append(Spacer(1, 6))
        S.append(T._leyenda_riesgo())

    S.append(PageBreak())

    # ── Hechos destacados + integridad ──
    S.append(Paragraph("Hechos destacados", st["h1"]))
    S.append(T.linea_oro())
    if articulos:
        filas = [[(a.get("title") or "")[:90], (a.get("source_name") or "—")[:22],
                  (a.get("capturado_en") or "")[:16].replace("T", " ")]
                 for a in articulos[:15]]
        S.append(T.tabla_profesional(["Titular", "Fuente", "Hora (Lima)"], filas,
                                     [3.4 * inch, 1.4 * inch, 1.3 * inch]))
    else:
        S.append(Paragraph("Sin titulares en las últimas 24h.", st["body"]))
    S.append(Spacer(1, 12))
    cb = conteos_bd or {}
    partes = []
    if cb.get("total") is not None:
        partes.append(f"{cb['total']:,} artículos en BD")
    partes.append(f"{len(articulos)} titulares en la ventana 24h")
    deg_line = (f"<br/><b>Degradación:</b> {escape_txt(nota_deg)}" if degradado else "")
    S.append(T.recuadro_ejecutivo(
        "INTEGRIDAD DE DATOS",
        " · ".join(partes) + ".<br/>"
        f"Última actualización de datos (snapshot): {gen} (America/Lima).<br/><br/>"
        "Narrativa generada automáticamente a partir de fuentes abiertas (OSINT) y "
        "métricas del sistema, mediante modelo de lenguaje bajo doctrina THALOS. "
        f"Estado: {estado_txt}." + deg_line, st))

    doc.build(S, onFirstPage=T.dibujar_portada, onLaterPages=T.header_footer)
    return {"pdf": buf.getvalue(), "estado": "completado",
            "nota": nota_deg if degradado else (
                "calibración" if calibracion else None)}
