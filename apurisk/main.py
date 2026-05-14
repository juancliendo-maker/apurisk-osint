"""Orquestador principal de APURISK 1.0.

Uso:
    python -m apurisk.main           # modo demo (default)
    python -m apurisk.main --live    # intenta conectar a fuentes reales
    python -m apurisk.main --watch N # ejecuta cada N segundos (loop, ideal "tiempo real")
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from .utils.timezone_pe import now_pe, now_pe_iso
    from .storage import ApuriskArchive
    from .collectors import (
        RSSMediaCollector, DefensoriaCollector, GDELTCollector,
        CongresoCollector, TwitterCollector,
    )
    from .analyzers import (
        analizar_sentimiento, extraer_entidades, detectar_temas,
        calcular_riesgo_global, calcular_matriz, detectar_alertas,
        analizar_twitter,
    )
    from .reports import (
        generar_dashboard_html, generar_reporte_docx,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apurisk.utils.timezone_pe import now_pe, now_pe_iso
    from apurisk.storage import ApuriskArchive
    from apurisk.collectors import (
        RSSMediaCollector, DefensoriaCollector, GDELTCollector,
        CongresoCollector, TwitterCollector,
    )
    from apurisk.analyzers import (
        analizar_sentimiento, extraer_entidades, detectar_temas,
        calcular_riesgo_global, calcular_matriz, detectar_alertas,
        analizar_twitter,
    )
    from apurisk.reports import (
        generar_dashboard_html, generar_reporte_docx,
        generar_reporte_24h_html, generar_reporte_24h_docx,
        generar_alertas_html, generar_alertas_docx,
        generar_reporte_diario_pdf, generar_reporte_semanal_pdf,
        generar_ejecutivo_docx, generar_ejecutivo_pdf,
    )


def cargar_config(path: str = None) -> dict:
    cfg_path = Path(path or Path(__file__).resolve().parent / "config.yaml")
    if not cfg_path.exists():
        cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        return _yaml_min(text)


def _yaml_min(text: str) -> dict:
    """Parser YAML minimalista (suficiente para config.yaml)."""
    root = {}
    stack = [(0, root)]
    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while stack and indent < stack[-1][0]:
            stack.pop()
        cur = stack[-1][1]
        s = line.strip()
        if s.startswith("- "):
            item_text = s[2:].strip()
            if isinstance(cur, list):
                if ":" in item_text:
                    new = {}
                    k, v = item_text.split(":", 1)
                    v = v.strip().strip('"').strip("'")
                    new[k.strip()] = _coerce(v) if v else None
                    cur.append(new)
                    stack.append((indent + 2, new))
                else:
                    cur.append(_coerce(item_text))
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip()
            if v == "":
                new_obj = {}
                cur[k] = new_obj
                stack.append((indent + 2, new_obj))
            else:
                cur[k] = _coerce(v.strip('"').strip("'"))
    return root


def _coerce(v):
    if v is None or v == "":
        return v
    if isinstance(v, str):
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return v
    return v


def recolectar(config: dict, demo: bool = True) -> dict:
    print("\n[1/3] Recolección de datos OSINT…")
    medios_articulos = []
    feeds = config.get("medios_rss", [])
    if isinstance(feeds, dict):
        feeds = list(feeds.values())
    for feed in feeds or []:
        c = RSSMediaCollector(feed, config, demo=demo)
        items = c.collect()
        print(f"  · {c.source_name}: {len(items)} ítems")
        medios_articulos.extend(items)

    # GDELT primero porque también aporta material para clasificación
    gd = GDELTCollector(config, demo=demo)
    gdelt = gd.collect()
    print(f"  · {gd.source_name}: {len(gdelt)} eventos")

    # Conflictos sociales — clasificación REAL-TIME desde RSS de medios + GDELT.
    # Si no hay material clasificable, cae a demo como último recurso.
    dc = DefensoriaCollector(config, demo=demo)
    if not demo:
        conflictos = dc.classify_from_media(medios_articulos + gdelt)
        print(f"  · {dc.source_name} (clasificación RSS real-time): {len(conflictos)} conflictos")
        if not conflictos:
            print(f"  [info] Sin conflictos detectados en RSS reciente → fallback demo")
            conflictos = dc._demo_articles()
    else:
        conflictos = dc.collect()
        print(f"  · {dc.source_name}: {len(conflictos)} conflictos")

    # Actividad legislativa — clasificación REAL-TIME desde RSS de medios.
    # Filtra por keywords legislativos (proyecto de ley, moción, interpelación,
    # comisión, dictamen, etc.) y mantiene solo items dentro de la ventana
    # temporal configurada (default 7 días).
    cc = CongresoCollector(config, demo=demo)
    if not demo:
        proyectos = cc.classify_from_media(medios_articulos)
        print(f"  · {cc.source_name} (clasificación RSS real-time): {len(proyectos)} items")
        if not proyectos:
            print(f"  [info] Sin actividad legislativa en RSS reciente → fallback demo")
            proyectos = cc._demo_articles()
    else:
        proyectos = cc.collect()
        print(f"  · {cc.source_name}: {len(proyectos)} proyectos")

    tw = TwitterCollector(config, demo=demo)
    tweets = tw.collect()
    print(f"  · {tw.source_name}: {len(tweets)} tweets")

    todos = medios_articulos + conflictos + gdelt + tweets
    return {
        "todos": todos,
        "medios": medios_articulos,
        "conflictos": conflictos,
        "gdelt": gdelt,
        "proyectos": proyectos,
        "tweets": tweets,
    }


def analizar(data: dict, config: dict) -> dict:
    print("\n[2/3] Análisis…")
    art = data["todos"]
    entidades = extraer_entidades(art)
    temas = detectar_temas(art)
    pesos = config.get("indicadores_riesgo", {
        "estabilidad_gobierno": 0.25, "conflictos_sociales": 0.20,
        "riesgo_regulatorio": 0.15, "polarizacion": 0.15,
        "corrupcion": 0.15, "seguridad": 0.10,
    })
    riesgo = calcular_riesgo_global(art, temas, data["conflictos"], pesos)
    matriz = calcular_matriz(data["medios"] + data["gdelt"] + data.get("tweets", []), data["conflictos"])
    alertas = detectar_alertas(data["medios"] + data["gdelt"] + data.get("tweets", []), data["conflictos"], ventana_horas=72)
    twitter_stats = analizar_twitter(data.get("tweets", []))

    # Reconciliar score global con la matriz de factores y alertas (más fiel al estado real)
    if matriz:
        top5 = matriz[:5]
        matrix_avg = sum(f["score"] for f in top5) / len(top5)
        # ajuste por alertas críticas
        crit = sum(1 for a in alertas if a["nivel"] == "CRÍTICA")
        bonus = min(15, crit * 2.0)
        riesgo["global"] = round(riesgo["global"] * 0.25 + matrix_avg * 0.75 + bonus, 1)
        riesgo["global"] = min(100.0, riesgo["global"])
        if riesgo["global"] >= 70:
            riesgo["nivel"] = "ALTO"
        elif riesgo["global"] >= 45:
            riesgo["nivel"] = "MEDIO"
        else:
            riesgo["nivel"] = "BAJO"
        riesgo["matriz_avg_top5"] = round(matrix_avg, 1)
        riesgo["alertas_criticas_bonus"] = round(bonus, 1)

    print(f"  · Score global: {riesgo['global']} ({riesgo['nivel']})")
    print(f"  · Sentimiento: {riesgo['sentimiento_promedio']}")
    print(f"  · Factores de riesgo: {len(matriz)}  → top: {matriz[0]['nombre']} ({matriz[0]['score']})")
    print(f"  · Alertas activas: {len(alertas)}  ({len([a for a in alertas if a['nivel']=='CRÍTICA'])} críticas)")
    print(f"  · Twitter: {twitter_stats['n']} tweets · {twitter_stats['engagement_total']:,} engagement · {len(twitter_stats['virales'])} virales")
    return {"entidades": entidades, "temas": temas, "riesgo": riesgo,
            "matriz": matriz, "alertas": alertas, "twitter_stats": twitter_stats}


def reportar(data: dict, an: dict, config: dict, modo: str, refresh_seconds: int = 1800) -> dict:
    print("\n[3/3] Generando reportes…")
    out_dir = Path(__file__).resolve().parent.parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = now_pe().strftime("%Y%m%d_%H%M")
    paths = {}

    art_24 = [a for a in (data["medios"] + data["gdelt"]) if a.hours_ago() <= 24]
    conf_24 = [c for c in data["conflictos"] if c.hours_ago() <= 24]
    alertas_24 = [a for a in an["alertas"] if a.get("ventana_24h")]
    tweets = data.get("tweets", [])

    # Snapshot JSON
    snapshot = {
        "generado": now_pe_iso(),
        "modo": modo,
        "n_articulos": len(data["todos"]),
        "n_articulos_24h": len(art_24),
        "n_conflictos": len(data["conflictos"]),
        "n_proyectos": len(data["proyectos"]),
        "n_tweets": len(tweets),
        "entidades": an["entidades"],
        "temas": an["temas"],
        "riesgo": an["riesgo"],
        "matriz_riesgo": an["matriz"],
        "alertas": an["alertas"],
        "twitter_stats": an["twitter_stats"],
        "articulos": [a.to_dict() for a in data["todos"]],
        "conflictos": [c.to_dict() for c in data["conflictos"]],
        "proyectos": [p.to_dict() for p in data["proyectos"]],
        "tweets": [t.to_dict() for t in tweets],
    }
    json_path = out_dir / f"apurisk_snapshot_{ts}.json"
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["snapshot_json"] = str(json_path)
    print(f"  · JSON: {json_path.name}")

    # 1) Dashboard principal
    dash_path = out_dir / f"apurisk_dashboard_{ts}.html"
    generar_dashboard_html(
        str(dash_path),
        data["medios"] + data["gdelt"], data["conflictos"], data["proyectos"],
        an["entidades"], an["temas"], an["riesgo"],
        matriz=an["matriz"], alertas=an["alertas"],
        tweets=tweets, twitter_stats=an["twitter_stats"],
        modo=modo, ventana=24,
        refresh_seconds=refresh_seconds,
        output_dir=str(out_dir),
    )
    paths["dashboard_html"] = str(dash_path)
    print(f"  · DASHBOARD: {dash_path.name}")

    # 2) Reporte 24h (HTML + DOCX)
    r24_html = out_dir / f"apurisk_reporte_24h_{ts}.html"
    generar_reporte_24h_html(str(r24_html), art_24, conf_24, alertas_24, an["riesgo"], an["matriz"], modo)
    paths["reporte_24h_html"] = str(r24_html)
    print(f"  · REPORTE 24h HTML: {r24_html.name}")

    r24_docx = out_dir / f"apurisk_reporte_24h_{ts}.docx"
    generar_reporte_24h_docx(str(r24_docx), art_24, conf_24, alertas_24, an["riesgo"], an["matriz"], modo)
    paths["reporte_24h_docx"] = str(r24_docx)
    print(f"  · REPORTE 24h DOCX: {r24_docx.name}")

    # 3) Alertas inmediatas (HTML + DOCX)
    alert_html = out_dir / f"apurisk_alertas_{ts}.html"
    generar_alertas_html(str(alert_html), an["alertas"], modo)
    paths["alertas_html"] = str(alert_html)
    print(f"  · ALERTAS HTML: {alert_html.name}")

    alert_docx = out_dir / f"apurisk_alertas_{ts}.docx"
    generar_alertas_docx(str(alert_docx), an["alertas"], modo)
    paths["alertas_docx"] = str(alert_docx)
    print(f"  · ALERTAS DOCX: {alert_docx.name}")

    # 4) Reporte ejecutivo completo (DOCX clásico)
    docx_path = out_dir / f"apurisk_ejecutivo_{ts}.docx"
    generar_reporte_docx(
        str(docx_path),
        data["medios"] + data["gdelt"], data["conflictos"], data["proyectos"],
        an["entidades"], an["temas"], an["riesgo"], modo=modo, ventana=24,
    )
    paths["ejecutivo_docx"] = str(docx_path)
    print(f"  · EJECUTIVO DOCX: {docx_path.name}")

    # 5) Reporte diario PDF
    try:
        pdf_diario = out_dir / f"apurisk_diario_{ts}.pdf"
        # construye snapshot dict equivalente al JSON para el PDF
        snap_for_pdf = {
            "generado": now_pe_iso(),
            "modo": modo,
            "n_articulos": len(data["todos"]),
            "n_articulos_24h": len(art_24),
            "n_conflictos": len(data["conflictos"]),
            "n_proyectos": len(data["proyectos"]),
            "n_tweets": len(tweets),
            "riesgo": an["riesgo"],
            "matriz_riesgo": an["matriz"],
            "alertas": an["alertas"],
            "articulos": [a.to_dict() for a in data["todos"]],
            "conflictos": [c.to_dict() for c in data["conflictos"]],
        }
        generar_reporte_diario_pdf(str(pdf_diario), snap_for_pdf)
        paths["diario_pdf"] = str(pdf_diario)
        print(f"  · DIARIO PDF: {pdf_diario.name}")
    except Exception as e:
        print(f"  [warn] PDF diario falló: {e}")

    # 6) Reporte semanal PDF
    try:
        pdf_semanal = out_dir / f"apurisk_semanal_{ts}.pdf"
        generar_reporte_semanal_pdf(str(pdf_semanal), str(out_dir))
        paths["semanal_pdf"] = str(pdf_semanal)
        print(f"  · SEMANAL PDF: {pdf_semanal.name}")
    except Exception as e:
        print(f"  [warn] PDF semanal falló: {e}")

    # 7) REPORTE EJECUTIVO DIARIO (visualmente atractivo, ≤3 páginas, foco tendencias)
    try:
        ejec_docx = out_dir / f"apurisk_ejecutivo_diario_{ts}.docx"
        generar_ejecutivo_docx(str(ejec_docx), snap_for_pdf, str(out_dir))
        paths["ejecutivo_diario_docx"] = str(ejec_docx)
        print(f"  · EJECUTIVO DIARIO DOCX: {ejec_docx.name}")
    except Exception as e:
        print(f"  [warn] Ejecutivo diario DOCX falló: {e}")
    try:
        ejec_pdf = out_dir / f"apurisk_ejecutivo_diario_{ts}.pdf"
        generar_ejecutivo_pdf(str(ejec_pdf), snap_for_pdf, str(out_dir))
        paths["ejecutivo_diario_pdf"] = str(ejec_pdf)
        print(f"  · EJECUTIVO DIARIO PDF: {ejec_pdf.name}")
    except Exception as e:
        print(f"  [warn] Ejecutivo diario PDF falló: {e}")

    # 4.5) Archivar snapshot en SQLite (base de datos OSINT histórica)
    try:
        archive = ApuriskArchive(str(out_dir / "apurisk_archive.db"))
        archive.archivar_snapshot(snapshot)
        st = archive.stats()
        print(f"  · ARCHIVO SQLite: {st['snapshots']} snapshots · {st['articulos']} artículos · {st['alertas']} alertas")
        paths["archive_db"] = str(out_dir / "apurisk_archive.db")
    except Exception as e:
        print(f"  [warn] Archivado SQLite falló: {e}")

    # 5) Copia del dashboard como nombre canónico (siempre el más reciente).
    # Probamos varios nombres por si el filesystem tiene un symlink legacy bloqueado.
    contenido_html = Path(dash_path).read_text(encoding="utf-8")
    for nombre in ("dashboard.html", "dashboard_latest.html"):
        target = out_dir / nombre
        try:
            if target.is_symlink():
                # intenta eliminar symlink legacy; si falla, simplemente lo saltamos
                try:
                    target.unlink()
                except Exception:
                    continue
            target.write_text(contenido_html, encoding="utf-8")
            paths.setdefault("latest", str(target))
        except Exception as e:
            print(f"  [warn] {nombre} no se pudo actualizar: {e}")

    return paths


def run_once(args):
    cfg = cargar_config(args.config)
    modo = "live" if args.live else "demo"
    data = recolectar(cfg, demo=not args.live)
    an = analizar(data, cfg)
    refresh = args.watch if args.watch and args.watch > 0 else 1800
    paths = reportar(data, an, cfg, modo, refresh_seconds=refresh)
    return paths


def main():
    parser = argparse.ArgumentParser(description="APURISK 1.0 — OSINT Riesgos Políticos Perú")
    parser.add_argument(
        "--demo", action="store_true",
        help="Usar SOLO datos demo (sin intentar fuentes reales). Default: live."
    )
    parser.add_argument(
        "--live", action="store_true", default=True,
        help="Conectar a fuentes reales (default activado). Cae a fallback demo si no hay red."
    )
    parser.add_argument("--config", help="Ruta a config.yaml")
    parser.add_argument(
        "--watch", type=int, default=1800,
        help="Ejecutar en loop cada N segundos (default: 1800 = 30 min). 0 = corrida única.",
    )
    parser.add_argument("--once", action="store_true", help="Forzar corrida única (ignora --watch)")
    args = parser.parse_args()
    # --demo gana sobre --live
    if args.demo:
        args.live = False
    if args.once:
        args.watch = 0

    modo_str = "LIVE (con fallback demo por fuente si no hay red)" if args.live else "DEMO (solo datos sintéticos)"
    print("┌─ APURISK 1.0 — Plataforma OSINT de Riesgos Políticos del Perú")
    print(f"│  Modo: {modo_str}")
    print(f"│  Inicio: {now_pe_iso()} (Lima/PET)")
    print("└─")

    if args.watch > 0:
        print(f"\n[WATCH] Ejecutando ciclo cada {args.watch}s. Ctrl+C para detener.\n")
        try:
            while True:
                paths = run_once(args)
                print(f"\n✔ Ciclo completado. Próximo en {args.watch}s.\n")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\n[WATCH] Detenido por usuario.")
            return
    else:
        paths = run_once(args)
        print("\n✔ Listo. Reportes generados en ./output/")
        for k, v in paths.items():
            print(f"  {k:20s} → {v}")


if __name__ == "__main__":
    main()
