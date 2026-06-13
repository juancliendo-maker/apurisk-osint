"""APURISK · web/routes_reports — Reportes diarios, on-demand, búsqueda y limpieza."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .core import (
    OUTPUT_DIR, REPORTES_DIARIOS_DIR, _ultimo_snapshot_path, ApuriskArchive,
)

try:
    from ..utils.timezone_pe import now_pe
    from ..reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
    )
except ImportError:
    from apurisk.utils.timezone_pe import now_pe
    from apurisk.reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
    )

router = APIRouter()



@router.get("/api/reportes-diarios")
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


@router.get("/api/reportes-diarios/{filename}")
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


@router.post("/api/limpiar-archive-contaminado")
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


@router.post("/api/limpiar-archivos")
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


@router.get("/api/buscar")
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


@router.get("/api/reporte/{tipo}/{formato}")
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


@router.get("/api/reportes")
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


@router.get("/api/reportes/{reporte_id}/pdf")
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
