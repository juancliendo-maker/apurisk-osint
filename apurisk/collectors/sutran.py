"""SUTRAN Collector — Sistema Nacional de Alertas de Vías de Perú.

Fuente OFICIAL: SUTRAN (Superintendencia de Transporte Terrestre) / MTC.
Endpoint: gis.sutran.gob.pe/alerta_sutran/script_cgm/carga_xlsx.php?tipo=MAPA

Devuelve GeoJSON en tiempo real con todas las alertas vigentes en 3 categorías:
  - normal:       vía con alerta vigente pero tránsito normal
  - restringido:  tránsito parcialmente limitado
  - interrumpido: vía cerrada (más crítico operacionalmente)

Motivos categorizados:
  - HUMANO:         paros, bloqueos, manifestaciones
  - CLIMATOLOGICO:  lluvia, huaico, derrumbe natural, niebla
  - INFRAESTRUCTURA: obras, mantenimiento, pérdida de plataforma
  - ACCIDENTES:     choques, vehículos averiados

Cada evento trae:
  - latitud/longitud exactas (oficiales del MTC, no geocodificadas)
  - kilometraje preciso (KM + nombre carretera)
  - ubigeo (Dpto/Prov/Distrito)
  - fuente del reporte (PNP / Concesionaria / COE MTC / DESPRCAR)
  - código de vía estándar (PE-1N, PE-3S, etc.)

Es la fuente más autoritativa para continuidad logística minera en Perú —
los reportes son del Estado, en tiempo real, con datos operacionales crudos.
"""
from __future__ import annotations
import json
import logging
import requests
from typing import Optional

log = logging.getLogger("apurisk.sutran")

SUTRAN_URL = (
    "https://gis.sutran.gob.pe/alerta_sutran/script_cgm/carga_xlsx.php?tipo=MAPA"
)
TIMEOUT = 15  # segundos

# User-Agent realista (algunos servidores estatales bloquean python-requests/urllib)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_sutran_alertas(timeout: int = TIMEOUT) -> list[dict]:
    """Descarga y normaliza las alertas vigentes del MTC/SUTRAN.

    Returns:
        Lista de eventos normalizados (puede estar vacía si SUTRAN está caído).
        Cada evento es un dict con keys:
          titulo, descripcion, lat, lon, region, provincia, distrito,
          kilometraje, via_codigo, via_nombre, estado, motivo, fuente,
          fecha_evento, fecha_actualizacion, categoria_sutran,
          _tipo_hotspot_hint, origen, url, vehiculos_pasajeros_detenidos,
          vehiculos_mercancias_detenidos.
    """
    try:
        resp = requests.get(
            SUTRAN_URL,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
                "Referer": "https://gis.sutran.gob.pe/alerta_sutran/",
            },
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning("SUTRAN fetch fallo: %s: %s", type(e).__name__, e)
        return []

    # SUTRAN responde con Content-Type 'text/html; charset=UTF-8' pero el
    # body es JSON con BOM. Forzamos decode UTF-8 (sin esto requests
    # adivina Latin-1 y produce mojibake: â€" en lugar de —, Â· en
    # lugar de ·, etc.).
    try:
        resp.encoding = "utf-8"
        text = resp.content.decode("utf-8", errors="replace")
        text = text.lstrip("﻿").lstrip("﻿").strip()
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
        log.warning("SUTRAN parse JSON fallo: %s", e)
        return []

    eventos = []
    for categoria in ("normal", "restringido", "interrumpido"):
        for f in data.get(categoria, []):
            try:
                geom = f.get("geometry", {}) or {}
                coords = geom.get("coordinates", []) or []
                if len(coords) < 2:
                    continue
                # GeoJSON usa [lon, lat]
                lon = float(coords[0])
                lat = float(coords[1])

                props = f.get("properties", {}) or {}

                # ubigeo "PUNO/MELGAR/AYAVIRI"
                ubigeo = str(props.get("ubigeo", "") or "")
                partes = [p.strip() for p in ubigeo.split("/")]
                region = partes[0] if len(partes) > 0 else ""
                provincia = partes[1] if len(partes) > 1 else ""
                distrito = partes[2] if len(partes) > 2 else ""

                estado = str(props.get("estado", "") or "")
                motivo = str(props.get("motivo", "") or "")
                evento_desc = str(props.get("evento", "") or "")
                via_codigo = str(props.get("codigo_via", "") or "")
                via_nombre = str(props.get("nombre_carretera", "") or "")
                km = str(props.get("afectacion", "") or "")
                fuente_reporte = str(props.get("fuente", "") or "")
                fecha_evento = str(props.get("fecha_evento", "") or "")
                fecha_actualizacion = str(props.get("fecha_actualizacion", "") or "")

                # Título descriptivo legible para el dashboard
                titulo_parts = [estado, "—", evento_desc]
                if region:
                    titulo_parts.append(f"({region}")
                    if distrito:
                        titulo_parts[-1] += f"/{distrito}"
                    if via_codigo:
                        titulo_parts[-1] += f", {via_codigo} {km})"
                    else:
                        titulo_parts[-1] += ")"
                titulo = " ".join(titulo_parts)

                # Clasificar tipo de hotspot
                tipo_hotspot = _clasificar_tipo_hotspot(estado, motivo, evento_desc)

                eventos.append({
                    "titulo": titulo,
                    "descripcion": evento_desc,
                    "lat": lat,
                    "lon": lon,
                    "region": region,
                    "provincia": provincia,
                    "distrito": distrito,
                    "kilometraje": km,
                    "via_codigo": via_codigo,
                    "via_nombre": via_nombre,
                    "estado": estado,
                    "motivo": motivo,
                    "fuente": f"SUTRAN/MTC · {fuente_reporte}" if fuente_reporte else "SUTRAN/MTC",
                    "fecha_evento": fecha_evento,
                    "fecha_actualizacion": fecha_actualizacion,
                    "categoria_sutran": categoria,
                    "_tipo_hotspot_hint": tipo_hotspot,
                    "origen": "sutran",
                    "url": "https://gis.sutran.gob.pe/alerta_sutran/",
                    "vehiculos_pasajeros_detenidos": _safe_int(
                        props.get("cant_vehiculos_detenidos_pasajeros", 0)
                    ),
                    "vehiculos_mercancias_detenidos": _safe_int(
                        props.get("cant_vehiculos_detenidos_mercancias", 0)
                    ),
                })
            except Exception as e:
                log.warning("SUTRAN parse evento error: %s", e)
                continue

    log.info(
        "SUTRAN: %d alertas vigentes (normal=%d restringido=%d interrumpido=%d)",
        len(eventos),
        len(data.get("normal", [])),
        len(data.get("restringido", [])),
        len(data.get("interrumpido", [])),
    )
    return eventos


def _clasificar_tipo_hotspot(estado: str, motivo: str, evento: str) -> str:
    """Mapea estado+motivo SUTRAN → tipo de hotspot del Executive Home."""
    motivo_up = (motivo or "").upper()
    estado_up = (estado or "").upper()
    evento_low = (evento or "").lower()

    # Accidentes graves → violencia (impacto humano)
    if motivo_up == "ACCIDENTES":
        return "violencia"

    # Paros y bloqueos sociales (motivo HUMANO + tránsito interrumpido)
    if motivo_up == "HUMANO":
        return "corredor_logistico"

    # Climatológico (lluvia, huaico, derrumbe natural)
    if motivo_up == "CLIMATOLOGICO":
        return "corredor_logistico"

    # Infraestructura (obras, mantenimiento, pérdida plataforma)
    if motivo_up == "INFRAESTRUCTURA":
        return "corredor_logistico"

    # Default: corredor logístico
    return "corredor_logistico"


def _safe_int(v) -> int:
    """Convierte a int de forma defensiva."""
    try:
        return int(v) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0
