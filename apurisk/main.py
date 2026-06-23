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
        CongresoCollector, TwitterCollector, ACLEDCollector,
        CrimenOrganizadoCollector,
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
        CongresoCollector, TwitterCollector, ACLEDCollector,
        CrimenOrganizadoCollector,
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
    # Garbage collection agresivo al inicio del ciclo para liberar memoria
    # del ciclo anterior (especialmente importante en plan Render Starter de 512MB)
    import gc
    gc.collect()
    print("\n[1/3] Recolección de datos OSINT…")
    medios_articulos = []
    feeds = config.get("medios_rss", [])
    if isinstance(feeds, dict):
        feeds = list(feeds.values())
    # Fase B: si config_fuentes (BD) tiene fuentes activas, usarlas (permite
    # activar/desactivar desde el panel admin). Fallback a config.yaml si vacío.
    if not demo:
        try:
            import os as _os
            from .storage.config_loader import cargar_feeds_efectivos
            _db = _os.environ.get("APURISK_DB_PATH",
                                  str(_os.path.join(_os.getenv("OUTPUT_DIR", "output"),
                                                    "apurisk_archive.db")))
            _feeds_bd = cargar_feeds_efectivos(_db)
            if _feeds_bd:
                print(f"  [config] {len(_feeds_bd)} fuentes activas desde config_fuentes (BD)")
                feeds = _feeds_bd
        except Exception as _e:
            print(f"  [config] feeds desde BD no disponibles → config.yaml: {_e}")
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

    # ACLED — eventos georreferenciados de violencia política y protestas.
    # Si ACLED_API_KEY y ACLED_EMAIL están como env vars, jala datos reales.
    # Si no, cae a demo georreferenciado (8 eventos con coords reales).
    acled_col = ACLEDCollector(config, demo=demo)
    acled_events = acled_col.collect()
    print(f"  · {acled_col.source_name}: {len(acled_events)} eventos georreferenciados")

    # Clasificación temática REAL-TIME: crimen organizado, narcotráfico,
    # minería ilegal, tala ilegal, contrabando, migración irregular,
    # extorsión/sicariato. Procesa los RSS reales y los etiqueta.
    co_col = CrimenOrganizadoCollector(config, demo=demo)
    if not demo:
        crimen_items = co_col.classify_from_media(medios_articulos)
        print(f"  · {co_col.source_name} (clasificación RSS real-time): {len(crimen_items)} items")
        # Desglose por tipología
        from collections import Counter
        tipologias = Counter(it.raw.get("tipologia") for it in crimen_items)
        for tip, n in tipologias.most_common():
            print(f"      · {tip}: {n}")
    else:
        crimen_items = []

    # Ingestas manuales del panel admin (Fase B Item 4)
    # Se cargan como artículos de medios y participan en el matching/análisis.
    ingestas_ids = []
    if not demo:
        try:
            import os as _os
            from .storage.config_loader import cargar_ingestas_pendientes, marcar_ingestas_procesadas
            from .collectors.base import Article
            _db_ing = _os.environ.get("APURISK_DB_PATH",
                                      str(_os.path.join(_os.getenv("OUTPUT_DIR", "output"),
                                                        "apurisk_archive.db")))
            pendientes = cargar_ingestas_pendientes(_db_ing)
            if pendientes:
                print(f"  · Ingestas manuales: {len(pendientes)} artículos pendientes")
                for ing in pendientes:
                    art_manual = Article(
                        source_id="manual",
                        source_name=ing.get("fuente") or "Ingesta manual",
                        category=ing.get("categoria") or "medios",
                        title=ing.get("titulo") or ing["url"],
                        summary=ing.get("resumen") or "",
                        url=ing["url"],
                        published=ing.get("published"),
                        criticidad="media",
                    )
                    medios_articulos.append(art_manual)
                    ingestas_ids.append(ing["id"])
                marcar_ingestas_procesadas(_db_ing, ingestas_ids)
        except Exception as _e:
            print(f"  [warn] ingestas manuales no cargadas: {_e}")

    # Universo "todos": medios + conflictos + GDELT + tweets + ACLED + crimen
    todos = medios_articulos + conflictos + gdelt + tweets + acled_events + crimen_items
    return {
        "todos": todos,
        "medios": medios_articulos,
        "conflictos": conflictos,
        "gdelt": gdelt,
        "proyectos": proyectos,
        "tweets": tweets,
        "acled_events": acled_events,
        "crimen_items": crimen_items,
    }


def analizar(data: dict, config: dict) -> dict:
    import gc
    gc.collect()  # liberar memoria de recolección antes de análisis pesado
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
    # Universo para detector de alertas: medios + GDELT + tweets + conflictos
    # + crimen_items (para que sicariato/narcotráfico/etc generen alertas)
    universo_alertas = (data["medios"] + data["gdelt"]
                          + data.get("tweets", [])
                          + data.get("crimen_items", []))
    alertas = detectar_alertas(universo_alertas, data["conflictos"], ventana_horas=72)
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

    # =================================================================
    # Camino B · Integración Score Engine v2 (validación paralela + flip)
    # =================================================================
    # Estrategia:
    #   1. Calcular v2 SIEMPRE (para trazabilidad y corrida paralela)
    #   2. Si flag activo es "v2", sobrescribir riesgo.global con score_v2
    #      (preserva v1 reconciliado como riesgo.riesgo_v1_legacy)
    #   3. Si flag es "v1" (default), no tocar el flujo visible
    #   4. Si v2 falla por cualquier razón → fallback automático a v1
    #   5. Persistir corrida en scores_paralelos para dashboard de validación
    #
    # Versión configurable en config.yaml score_engine.version: "v1" | "v2"
    # =================================================================
    version_engine = (config.get("score_engine") or {}).get("version", "v1")
    score_v2_completo = None

    try:
        try:
            from .analyzers.risk_score_v2 import calcular_score_nacional_v2
        except ImportError:
            from apurisk.analyzers.risk_score_v2 import calcular_score_nacional_v2

        # Snapshot mínimo en formato dict (v2 lo prefiere así)
        _articulos_dict = [a.to_dict() if hasattr(a, "to_dict") else a for a in art]
        _conflictos_dict = [c.to_dict() if hasattr(c, "to_dict") else c
                              for c in data["conflictos"]]
        snapshot_para_v2 = {
            "articulos": _articulos_dict,
            "temas": temas,
            "conflictos": _conflictos_dict,
        }

        # EDI actual (opcional — si falla, v2 lo ignora con peso_edi=0 en horizonte)
        edi_actual = None
        try:
            try:
                from .analyzers.estado_derecho_index import calcular_edi
            except ImportError:
                from apurisk.analyzers.estado_derecho_index import calcular_edi
            edi_data = calcular_edi(snapshot_para_v2, archive=None,
                                     intelligence_brief=None)
            if isinstance(edi_data, dict):
                edi_actual = edi_data.get("edi")
        except Exception:
            edi_actual = None

        score_v2_completo = calcular_score_nacional_v2(
            snapshot=snapshot_para_v2,
            archive=None,
            edi_actual=edi_actual,
            config=config,
        )
        print(f"  · Score v2: {score_v2_completo['score_nacional']} "
              f"({score_v2_completo['label']}) · "
              f"confidence={score_v2_completo['confidence']['score']} · "
              f"evento_crítico={score_v2_completo['evento_critico']['detectado']}")
    except Exception as e:
        print(f"  · ⚠ Score v2 falló: {type(e).__name__}: {e} — uso v1 (fallback)")
        score_v2_completo = None

    # Flip del motor si flag activo es "v2"
    if version_engine == "v2" and score_v2_completo is not None:
        # Preservar v1 reconciliado como referencia auditable
        riesgo["riesgo_v1_legacy"] = {
            "global": riesgo["global"],
            "nivel": riesgo["nivel"],
        }
        # Activar v2 como score oficial
        riesgo["global"] = score_v2_completo["score_nacional"]
        riesgo["nivel"] = score_v2_completo["label"]
        riesgo["motor"] = "v2"
        riesgo["confidence"] = score_v2_completo["confidence"]["score"]
        riesgo["evento_critico"] = score_v2_completo["evento_critico"]
        # Puente: exponer los 4 horizontes temporales al dashboard (tira bajo el Score).
        # Coyuntura 24h · Última semana 7d · Tendencia 4 sem (30d) · Estructural 3 meses (90d).
        _horiz = score_v2_completo.get("horizontes", {})
        riesgo["horizontes"] = {
            h: (_horiz.get(h, {}) or {}).get("score") for h in ("h24", "h7d", "h30d", "h90d")
        }
        print(f"  · 🟢 Motor activo: v2 (score {riesgo['global']} sobrescribió v1 legacy {riesgo['riesgo_v1_legacy']['global']})")
    else:
        riesgo["motor"] = "v1"
        if version_engine == "v2" and score_v2_completo is None:
            print(f"  · ⚠ flag='v2' pero v2 no disponible → fallback automático a v1")

    # Corrida paralela siempre persistida (alimenta dashboard /diagnostico)
    try:
        try:
            from .analyzers.risk_score_v2 import ejecutar_score_paralelo
            from .storage.archive import ApuriskArchive
        except ImportError:
            from apurisk.analyzers.risk_score_v2 import ejecutar_score_paralelo
            from apurisk.storage.archive import ApuriskArchive

        if score_v2_completo is not None:
            _db_path = Path(config.get("salida", {}).get("carpeta", "./output")) / "apurisk_archive.db"
            _archive = ApuriskArchive(str(_db_path))
            ejecutar_score_paralelo(
                snapshot=snapshot_para_v2,
                archive=_archive,
                edi_actual=edi_actual,
                config=config,
                score_v1=None,  # se recalcula puro desde el snapshot
                persistir=True,
            )
            print(f"  · ✓ Corrida paralela persistida en scores_paralelos")
    except Exception as e:
        print(f"  · ⚠ Corrida paralela falló: {type(e).__name__}: {e}")

    print(f"  · Score global ({riesgo.get('motor', 'v1')}): {riesgo['global']} ({riesgo['nivel']})")
    print(f"  · Sentimiento: {riesgo['sentimiento_promedio']}")
    print(f"  · Factores de riesgo: {len(matriz)}  → top: {matriz[0]['nombre']} ({matriz[0]['score']})")
    print(f"  · Alertas activas: {len(alertas)}  ({len([a for a in alertas if a['nivel']=='CRÍTICA'])} críticas)")
    print(f"  · Twitter: {twitter_stats['n']} tweets · {twitter_stats['engagement_total']:,} engagement · {len(twitter_stats['virales'])} virales")

    # ── Motor OSINT con semáforo multiplicativo (Fase C) ──────────────────
    osint_resultado = None
    try:
        import os as _os
        try:
            from .analyzers.motor_osint import analizar_osint
        except ImportError:
            from apurisk.analyzers.motor_osint import analizar_osint
        _db_osint = _os.environ.get(
            "APURISK_DB_PATH",
            str(_os.path.join(_os.getenv("OUTPUT_DIR", "output"), "apurisk_archive.db"))
        )
        osint_resultado = analizar_osint(
            articles=art,
            db_path=_db_osint,
            pais="PE",
            riesgo_nacional=float(riesgo.get("global", 0)),
            jaccard_dup=0.0,
            modo="AUTOMATICO",
            articulo_id=None,
            persistir=False,  # sin articulo_id no hay fila que actualizar
        )
        sem = osint_resultado.get("semaforo", {})
        print(f"  · Motor OSINT: semáforo={sem.get('nivel_interpretado', '?')} "
              f"score={sem.get('score', 0):.4f} "
              f"activador={sem.get('activador_disparado', False)}")
    except Exception as _e:
        print(f"  · ⚠ Motor OSINT falló: {type(_e).__name__}: {_e}")

    return {"entidades": entidades, "temas": temas, "riesgo": riesgo,
            "matriz": matriz, "alertas": alertas, "twitter_stats": twitter_stats,
            "score_v2_completo": score_v2_completo,
            "osint_motor": osint_resultado}


def _limpiar_archivos_viejos(out_dir: Path, retencion_snapshots: int = 5,
                                retencion_dashboards: int = 3,
                                retencion_reportes_dias: int = 30) -> int:
    """Limpieza retentiva: mantiene solo lo esencial.

    - apurisk_snapshot_*.json: últimos N (default 5)
    - apurisk_dashboard_*.html: últimos N (default 3)
    - Reportes bajo demanda (24h, alertas, ejecutivo, diario, semanal):
      conserva solo los de los últimos N días (default 30)
    - PRESERVADOS siempre: dashboard.html, apurisk_archive.db, reportes_caso/

    Returns: número de archivos eliminados.
    """
    from datetime import datetime as _dt, timedelta as _td
    eliminados = 0

    # Snapshots JSON: mantener últimos N
    snapshots = sorted(out_dir.glob("apurisk_snapshot_*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    for f in snapshots[retencion_snapshots:]:
        try:
            f.unlink()
            eliminados += 1
        except Exception:
            pass

    # Dashboards HTML: mantener últimos N (sin contar dashboard.html canónico)
    dashboards = sorted(out_dir.glob("apurisk_dashboard_*.html"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    for f in dashboards[retencion_dashboards:]:
        try:
            f.unlink()
            eliminados += 1
        except Exception:
            pass

    # ELIMINACIÓN INMEDIATA de archivos legacy del scheduler antiguo
    # (mayo 2026): el scheduler ya NO genera estos archivos, así que
    # cualquier archivo legacy que aún exista es basura del periodo
    # antes del cambio. Lo eliminamos TODOS sin importar antigüedad.
    patrones_legacy = [
        "apurisk_reporte_24h_*.html", "apurisk_reporte_24h_*.docx",
        "apurisk_alertas_*.html", "apurisk_alertas_*.docx",
        "apurisk_ejecutivo_*.docx", "apurisk_ejecutivo_*.pdf",
        "apurisk_diario_*.pdf", "apurisk_semanal_*.pdf",
        "apurisk_ejecutivo_diario_*.docx", "apurisk_ejecutivo_diario_*.pdf",
        # reportes legacy con prefijo distinto
        "reporte_24h_*.html", "reporte_24h_*.docx",
        "reporte_alertas_*.html", "reporte_alertas_*.docx",
        "reporte_ejecutivo_*.docx", "reporte_ejecutivo_*.pdf",
        "reporte_diario_*.pdf", "reporte_semanal_*.pdf",
    ]
    for patron in patrones_legacy:
        for f in out_dir.glob(patron):
            try:
                f.unlink()
                eliminados += 1
            except Exception:
                pass

    # Reportes generados manualmente vía /api/reporte/{tipo}/{formato}
    # con más de N días: eliminar (estos sí tienen retención, no son legacy)
    limite = _dt.now().timestamp() - retencion_reportes_dias * 86400
    patrones_manuales = [
        "reporte_*_*.pdf", "reporte_*_*.docx", "reporte_*_*.html",
    ]
    for patron in patrones_manuales:
        for f in out_dir.glob(patron):
            try:
                if f.stat().st_mtime < limite:
                    f.unlink()
                    eliminados += 1
            except Exception:
                pass

    if eliminados > 0:
        print(f"  · LIMPIEZA: {eliminados} archivos antiguos eliminados del disco")
    return eliminados


def reportar(data: dict, an: dict, config: dict, modo: str, refresh_seconds: int = 1800) -> dict:
    """Generación MINIMALISTA por ciclo del scheduler.

    Solo genera lo estrictamente necesario para servir el dashboard:
      - apurisk_snapshot_{ts}.json (alimenta el archive SQLite y endpoints)
      - apurisk_dashboard_{ts}.html (el HTML que sirve /dashboard)
      - dashboard.html (copia canónica)
      - apurisk_archive.db (SQLite con histórico)

    Los reportes 24h, alertas, ejecutivo, diario, semanal NO se generan
    automáticamente — se crean solo cuando el usuario los pide vía
    endpoints `/api/reporte/{tipo}/{formato}` o botones del dashboard.

    Esto reduce el uso de disco de ~140 MB/día → ~30 MB/día y libera
    significativa memoria por ciclo.
    """
    import gc
    gc.collect()
    print("\n[3/3] Generando reportes (modo minimalista: solo dashboard + snapshot)…")
    out_dir = Path(__file__).resolve().parent.parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Limpieza retentiva ANTES de generar (libera espacio para nuevos archivos)
    _limpiar_archivos_viejos(out_dir)

    ts = now_pe().strftime("%Y%m%d_%H%M")
    paths = {}

    art_24 = [a for a in (data["medios"] + data["gdelt"]) if a.hours_ago() <= 24]
    conf_24 = [c for c in data["conflictos"] if c.hours_ago() <= 24]
    alertas_24 = [a for a in an["alertas"] if a.get("ventana_24h")]
    tweets = data.get("tweets", [])
    acled_events = data.get("acled_events", [])
    crimen_items = data.get("crimen_items", [])

    # ── Conteos de temas últimos 7 días (ventana deslizante) ─────────────────
    # Se calculan ANTES de archivar para que el snapshot los incluya.
    # Usa la BD de archive para leer artículos publicados/capturados en los
    # últimos 7 días exactos desde el momento actual (rolling, no corte fijo).
    _temas_7d_conteos: dict = {}
    try:
        from datetime import timedelta as _td7
        _db_7d = out_dir / "apurisk_archive.db"
        if _db_7d.exists():
            import sqlite3 as _sq7
            _cutoff_7d = (now_pe() - _td7(days=7)).isoformat(timespec="seconds")
            with _sq7.connect(str(_db_7d)) as _c7:
                _rows7 = _c7.execute(
                    "SELECT title, summary FROM articulos "
                    "WHERE capturado_en >= ? OR published >= ?",
                    (_cutoff_7d, _cutoff_7d),
                ).fetchall()
            if _rows7:
                try:
                    from .analyzers.topics import detectar_temas as _dt7
                except ImportError:
                    from apurisk.analyzers.topics import detectar_temas as _dt7

                class _FakeArt:
                    __slots__ = ("title", "summary")
                    def __init__(self, t, s):
                        self.title = t or ""
                        self.summary = s or ""

                _arts7 = [_FakeArt(r[0], r[1]) for r in _rows7]
                _temas_7d_conteos = _dt7(_arts7).get("conteos", {})
                _n7 = len(_arts7)
                print(f"  · Temas 7D: {_n7} artículos desde {_cutoff_7d[:16]} → conteos={_temas_7d_conteos}")
            else:
                print(f"  · Temas 7D: sin artículos en BD desde {_cutoff_7d[:16]} — usando ciclo actual")
        else:
            print("  · Temas 7D: BD no existe aún — usando ciclo actual")
    except Exception as _e7d:
        print(f"  · [warn] Temas 7D falló: {type(_e7d).__name__}: {_e7d}")

    # Inyectar conteos 7D en el resultado del motor OSINT antes de serializar
    _osint_motor = an.get("osint_motor")
    if _osint_motor and _temas_7d_conteos:
        _osint_motor = dict(_osint_motor)
        _osint_motor["temas_7d_conteos"] = _temas_7d_conteos

    # Snapshot JSON
    snapshot = {
        "generado": now_pe_iso(),
        "modo": modo,
        "n_articulos": len(data["todos"]),
        "n_articulos_24h": len(art_24),
        "n_conflictos": len(data["conflictos"]),
        "n_proyectos": len(data["proyectos"]),
        "n_tweets": len(tweets),
        "n_acled_events": len(acled_events),
        "n_crimen_items": len(crimen_items),
        "entidades": an["entidades"],
        "temas": an["temas"],
        "riesgo": an["riesgo"],
        "matriz_riesgo": an["matriz"],
        "alertas": an["alertas"],
        "twitter_stats": an["twitter_stats"],
        # Score v2 completo (Camino B) — 4 horizontes, confidence, evento crítico.
        # Disponible siempre, motor activo se indica en riesgo.motor ("v1" | "v2").
        "score_v2_completo": an.get("score_v2_completo"),
        # Motor OSINT con semáforo multiplicativo (Fase C).
        # Incluye temas_7d_conteos (ventana deslizante 7 días desde BD).
        "osint_motor": _osint_motor,
        "articulos": [a.to_dict() for a in data["todos"]],
        "conflictos": [c.to_dict() for c in data["conflictos"]],
        "proyectos": [p.to_dict() for p in data["proyectos"]],
        "tweets": [t.to_dict() for t in tweets],
        "acled_events": [e.to_dict() for e in acled_events],
        "crimen_items": [c.to_dict() for c in crimen_items],
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
        acled_events=acled_events,
        crimen_items=crimen_items,
    )
    paths["dashboard_html"] = str(dash_path)
    print(f"  · DASHBOARD: {dash_path.name}")

    # === SECCIONES 2-7 DESACTIVADAS (mayo 2026) ===
    # Los reportes 24h, alertas, ejecutivo, diario, semanal NO se generan
    # automáticamente para reducir uso de disco y memoria.
    # Se crean BAJO DEMANDA via endpoints /api/reporte/{tipo}/{formato}
    # cuando el usuario pulsa "Generar AHORA" en la pestaña Descargas.
    # Esto reduce uso de disco de ~140 MB/día a ~30 MB/día.

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
