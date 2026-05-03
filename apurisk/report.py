"""CLI on-demand para generar reportes en cualquier momento.

Uso:
    python -m apurisk.report --tipo ejecutivo --formato pdf
    python -m apurisk.report --tipo ejecutivo --formato docx --salida mi_reporte.docx
    python -m apurisk.report --tipo 24h --formato html
    python -m apurisk.report --tipo alertas --formato pdf --desde 2026-04-28
    python -m apurisk.report --tipo semanal --formato pdf
    python -m apurisk.report --buscar "Huancavelica" --desde 2026-04-25 --hasta 2026-05-01

Tipos de reporte:
    ejecutivo  : Reporte ejecutivo diario (≤3 páginas, foco tendencias)
    24h        : Síntesis ejecutiva últimas 24 horas
    alertas    : Feed de alertas críticas
    semanal    : Reporte aggregated últimos 7 días
    busqueda   : Búsqueda histórica en archivo SQLite

Genera el reporte usando los DATOS ACTUALES en tiempo real:
  - Si hay datos en archive SQLite, los usa
  - Si no, ejecuta el pipeline completo primero
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from .utils.timezone_pe import now_pe, now_pe_iso, fmt_pe_full
    from .storage import ApuriskArchive
    from .reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apurisk.utils.timezone_pe import now_pe, now_pe_iso, fmt_pe_full
    from apurisk.storage import ApuriskArchive
    from apurisk.reports import (
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
    )


def _ultimo_snapshot(out_dir: Path) -> dict | None:
    """Carga el snapshot JSON más reciente del directorio output/."""
    snaps = sorted(out_dir.glob("apurisk_snapshot_*.json"))
    if not snaps:
        return None
    with open(snaps[-1], encoding="utf-8") as f:
        return json.load(f)


def _ejecutar_pipeline_si_no_hay_datos(out_dir: Path) -> dict:
    """Si no hay snapshot reciente, corre el pipeline completo."""
    snap = _ultimo_snapshot(out_dir)
    if snap:
        # Verificar si es reciente (<2 horas)
        try:
            gen = datetime.fromisoformat(snap["generado"].replace("Z", "+00:00"))
            age = (now_pe() - gen).total_seconds() / 3600
            if age < 2:
                return snap
        except Exception:
            pass
    # No hay datos frescos → corre el pipeline
    print("[!] No hay snapshot reciente. Ejecutando pipeline completo…\n")
    from .main import run_once
    import argparse as _ap
    a = _ap.Namespace(live=True, demo=False, config=None, watch=0, once=True)
    run_once(a)
    return _ultimo_snapshot(out_dir)


def cmd_reporte(args):
    """Genera un reporte específico on-demand."""
    out_dir = Path(args.output_dir or "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Obtener datos actuales
    snap = _ejecutar_pipeline_si_no_hay_datos(out_dir)
    if not snap:
        print("[ERROR] No se pudo generar/leer snapshot.")
        return 1

    print(f"📊 Snapshot generado: {fmt_pe_full(snap.get('generado', ''))}")
    print(f"   Score: {snap.get('riesgo', {}).get('global')} · {snap.get('riesgo', {}).get('nivel')}")
    print(f"   Artículos: {snap.get('n_articulos')} · Alertas: {len(snap.get('alertas', []))}")
    print()

    ts = now_pe().strftime("%Y%m%d_%H%M")
    salida = args.salida
    tipo = args.tipo.lower()
    formato = args.formato.lower()

    # Determinar el path de salida
    if not salida:
        salida = out_dir / f"reporte_{tipo}_{ts}.{formato}"
    salida = Path(salida)

    # Despachar
    if tipo == "ejecutivo":
        if formato == "pdf":
            from .reports import generar_ejecutivo_pdf
            generar_ejecutivo_pdf(str(salida), snap, str(out_dir))
        elif formato == "docx":
            from .reports import generar_ejecutivo_docx
            generar_ejecutivo_docx(str(salida), snap, str(out_dir))
        else:
            print(f"[ERROR] formato '{formato}' no soportado para tipo 'ejecutivo' (use pdf|docx)")
            return 1
    elif tipo == "24h":
        if formato == "html":
            # 24h slice del snapshot
            arts = snap.get("articulos", [])
            confs = snap.get("conflictos", [])
            alertas_24 = [a for a in snap.get("alertas", []) if a.get("ventana_24h")]
            generar_reporte_24h_html(str(salida), arts, confs, alertas_24,
                                       snap.get("riesgo", {}), snap.get("matriz_riesgo", []), snap.get("modo", "live"))
        elif formato == "docx":
            arts = snap.get("articulos", [])
            confs = snap.get("conflictos", [])
            alertas_24 = [a for a in snap.get("alertas", []) if a.get("ventana_24h")]
            generar_reporte_24h_docx(str(salida), arts, confs, alertas_24,
                                       snap.get("riesgo", {}), snap.get("matriz_riesgo", []), snap.get("modo", "live"))
        else:
            print(f"[ERROR] formato '{formato}' no soportado para 24h (use html|docx)")
            return 1
    elif tipo == "alertas":
        alertas = snap.get("alertas", [])
        if formato == "html":
            generar_alertas_html(str(salida), alertas, snap.get("modo", "live"))
        elif formato == "docx":
            generar_alertas_docx(str(salida), alertas, snap.get("modo", "live"))
        else:
            print(f"[ERROR] formato '{formato}' no soportado para alertas (use html|docx)")
            return 1
    elif tipo == "diario":
        if formato == "pdf":
            generar_reporte_diario_pdf(str(salida), snap)
        else:
            print(f"[ERROR] formato '{formato}' no soportado para diario (use pdf)")
            return 1
    elif tipo == "semanal":
        if formato == "pdf":
            generar_reporte_semanal_pdf(str(salida), str(out_dir))
        else:
            print(f"[ERROR] formato '{formato}' no soportado para semanal (use pdf)")
            return 1
    else:
        print(f"[ERROR] tipo '{tipo}' no reconocido")
        print("        Use: ejecutivo | 24h | alertas | diario | semanal")
        return 1

    print(f"✓ Reporte generado: {salida}")
    return 0


def cmd_buscar(args):
    """Busca en el archivo SQLite histórico."""
    out_dir = Path(args.output_dir or "output")
    db_path = out_dir / "apurisk_archive.db"
    if not db_path.exists():
        print(f"[ERROR] Archivo SQLite no encontrado: {db_path}")
        print("        Ejecuta el pipeline al menos una vez con: python -m apurisk.main --once")
        return 1

    archive = ApuriskArchive(str(db_path))
    st = archive.stats()
    print(f"📚 ARCHIVO OSINT — {st['snapshots']} snapshots · {st['articulos']} artículos · {st['alertas']} alertas")
    print(f"   Período: {st['primer']} → {st['ultimo']}")
    print()

    if args.tipo == "alertas":
        rows = archive.search_alertas(
            keyword=args.keyword, nivel=args.nivel, regla=args.regla,
            desde=args.desde, hasta=args.hasta, limit=args.limit,
        )
        print(f"🚨 {len(rows)} alertas encontradas:\n")
        for r in rows:
            print(f"  [{r['nivel']:<8}] {r['titulo'][:70]:72s} {r['timestamp'][:16]} · {r['fuente']}")
            if r.get("url"):
                print(f"           🔗 {r['url'][:100]}")
    elif args.tipo == "persistentes":
        rows = archive.alertas_persistentes(dias=args.dias, min_dias=args.min_dias)
        print(f"♻️ {len(rows)} casos persistentes ({args.min_dias}+ días):\n")
        for r in rows:
            print(f"  [{r['nivel']:<8}] {r['titulo'][:65]:67s} {r['dias_activo']}d · {r['ocurrencias']} apariciones")
    elif args.tipo == "score":
        rows = archive.serie_temporal_score(dias=args.dias)
        print(f"📈 Serie temporal score (últimos {args.dias} días):\n")
        for r in rows:
            bar = "█" * int(r["score_global"] / 5) if r["score_global"] else ""
            print(f"  {r['generado'][:16]} · {r['score_global']:5.1f} {r['nivel']:<6} {bar}")
    else:
        rows = archive.search_articulos(
            keyword=args.keyword, region=args.region, source_id=args.fuente,
            desde=args.desde, hasta=args.hasta, limit=args.limit,
        )
        print(f"📰 {len(rows)} artículos encontrados:\n")
        for r in rows:
            print(f"  [{(r['source_id'] or '—')[:14]:14s}] {r['title'][:75]:77s} · {r['published'][:16]}")
            if r.get("url"):
                print(f"           🔗 {r['url'][:100]}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="APURISK 1.0 — Generador on-demand de reportes y búsqueda histórica",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python -m apurisk.report --tipo ejecutivo --formato pdf
  python -m apurisk.report --tipo 24h --formato docx
  python -m apurisk.report --tipo alertas --formato html
  python -m apurisk.report --tipo semanal --formato pdf

  python -m apurisk.report --buscar --keyword "Huancavelica" --desde 2026-04-25
  python -m apurisk.report --buscar --tipo alertas --nivel CRÍTICA
  python -m apurisk.report --buscar --tipo persistentes --dias 7 --min-dias 3
  python -m apurisk.report --buscar --tipo score --dias 30
""",
    )
    sp = parser.add_subparsers(dest="cmd", help="comandos")

    # Comando: generar reporte
    p1 = parser
    p1.add_argument("--tipo", help="ejecutivo | 24h | alertas | diario | semanal | persistentes | score")
    p1.add_argument("--formato", default="pdf", help="pdf | docx | html")
    p1.add_argument("--salida", help="ruta de salida específica (opcional)")
    p1.add_argument("--output-dir", default="output", help="directorio de output")

    # Comando: buscar
    p1.add_argument("--buscar", action="store_true", help="buscar en archivo histórico SQLite")
    p1.add_argument("--keyword", help="palabra clave para buscar")
    p1.add_argument("--nivel", help="filtrar alertas por nivel: CRÍTICA | ALTA | MEDIA")
    p1.add_argument("--regla", help="filtrar alertas por regla")
    p1.add_argument("--region", help="filtrar artículos por región")
    p1.add_argument("--fuente", help="filtrar artículos por source_id")
    p1.add_argument("--desde", help="fecha desde ISO YYYY-MM-DD")
    p1.add_argument("--hasta", help="fecha hasta ISO YYYY-MM-DD")
    p1.add_argument("--dias", type=int, default=7, help="días de ventana")
    p1.add_argument("--min-dias", type=int, default=2, help="mínimo de días activos para 'persistentes'")
    p1.add_argument("--limit", type=int, default=50, help="máximo resultados")

    args = parser.parse_args()

    if args.buscar:
        return cmd_buscar(args)
    elif args.tipo:
        return cmd_reporte(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
