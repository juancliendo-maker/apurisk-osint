"""APURISK · web/schedulers — Tareas de fondo y arranque del servidor."""
from __future__ import annotations
import json
import asyncio

from .core import (
    _state, REFRESH_SECONDS, OUTPUT_DIR, REPORTES_DIARIOS_DIR,
    _limpiar_reportes_diarios_viejos, _ultimo_snapshot_path,
    LOGIN_ACTIVO, _auth_state,
)

try:
    from ..utils.timezone_pe import now_pe, now_pe_iso
    from ..main import run_once as pipeline_run_once
    from ..reports import generar_ejecutivo_pdf
    from ..utils import auth
except ImportError:
    from apurisk.utils.timezone_pe import now_pe, now_pe_iso
    from apurisk.main import run_once as pipeline_run_once
    from apurisk.reports import generar_ejecutivo_pdf
    from apurisk.utils import auth



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


async def _startup():
    # --- Tablas de configuración admin (Fase A) ---
    db_path = None
    try:
        from ..storage.admin_tables import inicializar_admin_tables
        import os
        db_path = os.environ.get("APURISK_DB_PATH", str(OUTPUT_DIR / "apurisk_archive.db"))
        inicializar_admin_tables(db_path)
        print("[admin] Tablas de configuración inicializadas.")
    except Exception as e:
        print(f"[admin] inicialización de tablas admin falló (no crítico): {e}")

    # --- Seed de config_fuentes desde config.yaml (Fase B, item 1) ---
    # Solo puebla si la tabla está vacía. La calidad inicial se calcula con la
    # misma lógica que usa el pipeline (risk_matrix._calidad_fuente).
    if db_path:
        try:
            from ..storage.config_loader import seed_fuentes_si_vacio
            from ..analyzers.risk_matrix import _calidad_fuente
            import yaml
            from pathlib import Path as _P
            _cfg_path = _P(__file__).resolve().parent.parent / "config.yaml"
            with open(_cfg_path, encoding="utf-8") as _f:
                _feeds = (yaml.safe_load(_f) or {}).get("medios_rss", [])
            n = seed_fuentes_si_vacio(db_path, _feeds, calidad_fn=_calidad_fuente)
            if n > 0:
                print(f"[config] config_fuentes poblada con {n} fuentes desde config.yaml")
        except Exception as e:
            print(f"[config] seed de config_fuentes falló (no crítico): {e}")

    # --- Seed de config_factores desde FACTORES + PROB_BASE_FACTOR (Fase B, item 2) ---
    if db_path:
        try:
            from ..storage.config_loader import seed_factores_si_vacio
            from ..analyzers.risk_matrix import FACTORES, PROB_BASE_FACTOR
            n = seed_factores_si_vacio(db_path, FACTORES, PROB_BASE_FACTOR)
            if n > 0:
                print(f"[config] config_factores poblada con {n} factores")
        except Exception as e:
            print(f"[config] seed de config_factores falló (no crítico): {e}")

    # --- Autenticación: preparar tabla de usuarios y admin inicial ---
    try:
        auth.init_db()
        nuevo = auth.seed_admin_desde_env()
        if nuevo:
            print(f"[auth] usuario administrador inicial creado: '{nuevo}'")
        _auth_state["login_enforce"] = LOGIN_ACTIVO and auth.existe_algun_usuario()
        if _auth_state["login_enforce"]:
            print("[auth] Login por usuario/clave ACTIVO → todo el sitio requiere sesión.")
        elif LOGIN_ACTIVO:
            print("[auth] APURISK_SECRET_KEY presente pero NO hay usuarios → login NO "
                  "se exige. Definí APURISK_ADMIN_USER y APURISK_ADMIN_PASSWORD para "
                  "crear el primer usuario.")
        else:
            print("[auth] Login por usuario/clave DESACTIVADO (sin APURISK_SECRET_KEY).")
    except Exception as e:
        print(f"[auth] inicialización de login falló (login desactivado): {e}")
        _auth_state["login_enforce"] = False

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


