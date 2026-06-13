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
import time
import socket
import secrets
import asyncio
import ipaddress
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

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

try:
    from .utils import auth
except ImportError:
    from apurisk.utils import auth


# Configuración
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "1800"))  # 30 min default
PORT = int(os.getenv("PORT", "8080"))
SERVER_VERSION = "1.0.0"

# --- Login por usuario/clave (Fase 1) ---
# El login se ACTIVA solo si APURISK_SECRET_KEY está definida Y existe al menos
# un usuario. Mientras tanto, el sitio se comporta como antes (sin romper nada).
SECRET_SESION = os.getenv("APURISK_SECRET_KEY", "").strip()
LOGIN_ACTIVO = bool(SECRET_SESION)
SESION_TTL = int(os.getenv("APURISK_SESION_TTL", str(60 * 60 * 24 * 7)))  # 7 días
_COOKIE_SESION = "apurisk_sesion"
# Holder mutable: el middleware lee si debe exigir login. Se fija en _startup
# (requiere que exista al menos un usuario para no dejar a nadie afuera).
_auth_state = {"login_enforce": False}

app = FastAPI(
    title="APURISK OSINT — Strategic Intelligence for Complex Decisions (Powered by THALOS)",
    description=(
        "Plataforma de monitoreo en tiempo real. Auto-refresh cada "
        f"{REFRESH_SECONDS//60} minutos. Genera reportes on-demand en PDF, DOCX y HTML."
    ),
    version=SERVER_VERSION,
)

# ----------------------------------------------------------------------
# Static files de /output CON filtro de seguridad.
#
# El directorio output/ mezcla productos públicos (PDF/DOCX/HTML y los
# snapshots JSON que el dashboard ofrece como descarga) con artefactos
# sensibles que NO deben ser descargables por nadie: la base SQLite
# completa (apurisk_archive.db + sus WAL/SHM) y el caché interno del brief
# ejecutivo. Antes se montaba todo el directorio sin filtro, de modo que
# cualquiera podía bajar /output/apurisk_archive.db. Esta subclase sirve los
# productos públicos y responde 404 a los archivos sensibles.
# ----------------------------------------------------------------------
class _OutputStaticFiles(StaticFiles):
    _EXT_BLOQUEADAS = (
        ".db", ".db-wal", ".db-shm", ".db-journal",
        ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm",
    )
    _NOMBRES_BLOQUEADOS = ("executive_brief_cache.json",)

    async def get_response(self, path, scope):
        nombre = path.rsplit("/", 1)[-1].lower()
        if nombre.endswith(self._EXT_BLOQUEADAS) or nombre in self._NOMBRES_BLOQUEADOS:
            # 404 (no 403) para no confirmar siquiera la existencia del archivo.
            from starlette.responses import PlainTextResponse
            return PlainTextResponse("Not Found", status_code=404)
        return await super().get_response(path, scope)


# Servir archivos estáticos del dashboard (HTML, PDFs, DOCX, snapshots JSON).
# La base SQLite y el caché interno quedan bloqueados (ver clase de arriba).
app.mount("/output", _OutputStaticFiles(directory=str(OUTPUT_DIR)), name="output")

# Servir assets de la marca (logo THALOS, favicons, etc.)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ======================================================================
# Capa de autenticación (defense-in-depth)
# ======================================================================
# Se ACTIVA solo si la variable de entorno APURISK_API_KEY está definida.
# Si NO está definida, el comportamiento es idéntico al histórico (todo
# abierto), para no romper despliegues existentes ni el primer arranque.
#
# Cuando está activa, exige credencial en:
#   - TODA operación que cambia estado (POST/PUT/PATCH/DELETE), p.ej.
#     limpiar archivos, regenerar briefs, generar reportes, análisis de caso.
#   - GETs costosos o de diagnóstico: /api/refresh y los *-test / debug-*.
#
# Las vistas de LECTURA (dashboard, /api/status, /api/snapshot, descargas
# de reportes, /healthz, /static, /output) quedan SIEMPRE públicas para no
# romper el visor en navegador.
#
# La credencial se acepta por (en este orden): cabecera 'X-API-Key', query
# '?api_key=' o cookie 'apurisk_auth'. Al cargar CUALQUIER página con
# ?api_key=<clave>, el servidor deja una cookie HttpOnly; así los botones del
# dashboard (que hacen fetch a endpoints POST) siguen funcionando sin tener
# que modificar el frontend. Clientes programáticos usan la cabecera.
_GET_PROTEGIDOS = frozenset({
    "/api/refresh",
    "/api/executive/llm-test",
    "/api/executive/sutran-test",
    "/api/executive/debug-snapshot",
})
_METODOS_ESCRITURA = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_COOKIE_AUTH = "apurisk_auth"

if not os.environ.get("APURISK_API_KEY", "").strip():
    print("[seguridad] APURISK_API_KEY no está definida → endpoints de "
          "escritura/diagnóstico ABIERTOS. Definí APURISK_API_KEY en el "
          "entorno (Render → Environment) para activar la protección.")

# ======================================================================
# Utilidades de seguridad: respuestas JSON, anti-SSRF y rate limiting
# ======================================================================
def _json_error(status_code: int, detail: str) -> JSONResponse:
    """JSONResponse con charset utf-8 explícito (evita acentos rotos en pantalla)."""
    return JSONResponse(status_code=status_code, content={"detail": detail},
                        media_type="application/json; charset=utf-8")


# --- Anti-SSRF: validar y descargar URLs provistas por el usuario ---
_FETCH_TIMEOUT = 10
_FETCH_MAX_BYTES = 3_000_000        # 3 MB tope por descarga
_FETCH_MAX_REDIRECTS = 3


def _ip_es_interna(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # ante la duda, bloquear
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _url_es_segura(url: str):
    """(True, host) si es http/https y resuelve SOLO a IPs públicas; (False, motivo)
    si no. Bloquea SSRF hacia metadata cloud (169.254.169.254), localhost y redes
    internas."""
    try:
        p = urlparse(url)
    except Exception:
        return False, "URL ilegible"
    if p.scheme not in ("http", "https"):
        return False, "solo se permiten http/https"
    host = p.hostname
    if not host:
        return False, "URL sin host"
    puerto = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, puerto, proto=socket.IPPROTO_TCP)
    except Exception:
        return False, "no se pudo resolver el host"
    for info in infos:
        if _ip_es_interna(info[4][0]):
            return False, f"destino interno no permitido ({info[4][0]})"
    return True, host


def _fetch_url_segura(url: str):
    """Descarga una URL del usuario con protección anti-SSRF: valida CADA salto de
    redirección, limita tamaño y tiempo. Devuelve el texto, o None ante cualquier
    problema."""
    import requests
    actual = url
    for _ in range(_FETCH_MAX_REDIRECTS + 1):
        ok, motivo = _url_es_segura(actual)
        if not ok:
            print(f"[ssrf] URL rechazada: {actual!r} → {motivo}")
            return None
        try:
            r = requests.get(
                actual, timeout=_FETCH_TIMEOUT, allow_redirects=False, stream=True,
                headers={"User-Agent": "Mozilla/5.0 APURISK-OSINT/1.0"})
        except Exception:
            return None
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location")
            if not loc:
                return None
            actual = urljoin(actual, loc)  # el destino se valida en la próxima vuelta
            continue
        if r.status_code != 200:
            return None
        try:
            crudo = r.raw.read(_FETCH_MAX_BYTES + 1, decode_content=True)
        except Exception:
            return None
        if len(crudo) > _FETCH_MAX_BYTES:
            print(f"[ssrf] respuesta demasiado grande, descartada: {actual!r}")
            return None
        return crudo.decode(r.encoding or "utf-8", errors="replace")
    print(f"[ssrf] demasiadas redirecciones: {url!r}")
    return None


# --- Rate limiting en memoria (la app corre en una sola instancia en Render) ---
# Prefijo de ruta -> (máx. solicitudes, ventana en segundos). Solo endpoints caros.
_RL_REGLAS = {
    "/api/refresh": (5, 300),
    "/api/analisis-caso": (10, 600),
    "/api/riesgo-minera/generar": (10, 600),
    "/api/executive/brief/regenerar": (10, 600),
    "/api/executive/llm-test": (15, 300),
    "/api/diagnostico/scores-paralelos/calcular-hoy": (10, 600),
}
_rl_buckets: dict = {}  # "ip|prefijo" -> (conteo, inicio_ventana)


def _cliente_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "desconocido"


def _excede_rate_limit(request: Request) -> bool:
    ruta = request.url.path
    regla = None
    for pref, lim in _RL_REGLAS.items():
        if ruta == pref or ruta.startswith(pref):
            regla = (pref, lim[0], lim[1])
            break
    if not regla:
        return False
    pref, maximo, ventana = regla
    ahora = int(time.time())
    key = f"{_cliente_ip(request)}|{pref}"
    conteo, inicio = _rl_buckets.get(key, (0, ahora))
    if ahora - inicio >= ventana:
        conteo, inicio = 0, ahora
    conteo += 1
    _rl_buckets[key] = (conteo, inicio)
    if len(_rl_buckets) > 5000:  # prune ocasional de entradas vencidas
        tope = max(v[1] for v in _RL_REGLAS.values())
        for k, (_, t) in list(_rl_buckets.items()):
            if ahora - t >= tope:
                _rl_buckets.pop(k, None)
    return conteo > maximo


# Rutas siempre accesibles sin sesión (necesarias para poder loguearse o para
# que la plataforma viva): login/logout, health check, favicon y assets.
_RUTAS_PUBLICAS = frozenset({"/login", "/logout", "/healthz", "/favicon.ico"})


def _es_ruta_publica(ruta: str) -> bool:
    return ruta in _RUTAS_PUBLICAS or ruta.startswith("/static")


def _apikey_valida(request: Request) -> bool:
    """True si la request trae una APURISK_API_KEY válida (header, query o cookie)."""
    esperada = os.environ.get("APURISK_API_KEY", "").strip()
    if not esperada:
        return False
    provista = (
        request.headers.get("X-API-Key", "")
        or request.query_params.get("api_key", "")
        or request.cookies.get(_COOKIE_AUTH, "")
    ).strip()
    return bool(provista) and secrets.compare_digest(provista, esperada)


@app.middleware("http")
async def _guardia_acceso(request: Request, call_next):
    ruta = request.url.path
    metodo = request.method.upper()

    # Rate limiting de endpoints costosos (aplica con o sin login).
    if _excede_rate_limit(request):
        return _json_error(429, "Demasiadas solicitudes a este recurso. "
                                "Esperá un momento e intentá de nuevo.")

    # ============ MODO LOGIN: protege TODO el sitio ============
    if _auth_state["login_enforce"]:
        if _es_ruta_publica(ruta):
            return await call_next(request)

        sesion = auth.verificar_token_sesion(
            request.cookies.get(_COOKIE_SESION, ""), SECRET_SESION)
        autorizado = bool(sesion) or _apikey_valida(request)

        if autorizado:
            response = await call_next(request)
            # Bootstrap de la cookie api_key para clientes que la pasan por query.
            if _apikey_valida(request) and request.query_params.get("api_key"):
                response.set_cookie(
                    _COOKIE_AUTH, os.environ["APURISK_API_KEY"].strip(),
                    httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 24 * 7)
            return response

        # No autorizado: API → 401 JSON; navegación → redirige al login.
        if ruta.startswith("/api/") or ruta.startswith("/output") or metodo in _METODOS_ESCRITURA:
            return _json_error(401, "No autorizado. Iniciá sesión en /login.")
        from urllib.parse import quote
        return RedirectResponse(url=f"/login?next={quote(ruta, safe='/')}", status_code=302)

    # ============ MODO API-KEY (login desactivado): comportamiento anterior ============
    clave_esperada = os.environ.get("APURISK_API_KEY", "").strip()
    if not clave_esperada:
        return await call_next(request)

    valida = _apikey_valida(request)
    protegido = metodo in _METODOS_ESCRITURA or ruta in _GET_PROTEGIDOS
    if protegido and not valida:
        return _json_error(401,
            "No autorizado. Falta o es inválida la credencial. Provéela vía "
            "cabecera 'X-API-Key', o cargá el dashboard una vez con "
            "?api_key=<clave> para fijar la cookie de sesión.")

    response = await call_next(request)
    if valida and request.query_params.get("api_key"):
        response.set_cookie(
            _COOKIE_AUTH, clave_esperada,
            httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 24 * 7,
        )
    return response


# ======================================================================
# Login por usuario y clave
# ======================================================================
def _safe_next(n: str) -> str:
    """Solo permite redirecciones internas seguras; ante la duda → /dashboard."""
    n = (n or "/dashboard").strip()
    if not n.startswith("/") or n.startswith("//") or any(c in n for c in '"\'<>'):
        return "/dashboard"
    return n


def _html_login(error: str = "", next_url: str = "/dashboard") -> str:
    import html as _html
    next_url = _html.escape(_safe_next(next_url), quote=True)
    err = (f'<div class="err">{_html.escape(error)}</div>') if error else ""
    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>APURISK · Iniciar sesión</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0b1220; color:#e5e7eb;
         font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
  .card {{ width:min(92vw, 380px); background:#111a2e; border:1px solid #1f2a44;
          border-radius:16px; padding:32px 28px; box-shadow:0 12px 40px rgba(0,0,0,.5); }}
  .logo {{ height:42px; display:block; margin:0 auto 14px; }}
  h1 {{ font-size:20px; text-align:center; margin:0 0 4px; letter-spacing:.5px; }}
  .sub {{ text-align:center; color:#94a3b8; font-size:12px; margin:0 0 22px; }}
  label {{ display:block; font-size:12px; color:#94a3b8; margin:14px 0 6px; }}
  input {{ width:100%; padding:11px 12px; border-radius:9px; border:1px solid #28354f;
          background:#0b1220; color:#e5e7eb; font-size:14px; }}
  input:focus {{ outline:2px solid #38bdf8; border-color:transparent; }}
  button {{ width:100%; margin-top:22px; padding:12px; border:0; border-radius:9px;
           background:linear-gradient(90deg,#38bdf8,#6366f1); color:#fff;
           font-size:15px; font-weight:600; cursor:pointer; }}
  button:hover {{ filter:brightness(1.08); }}
  .err {{ background:#3b1320; border:1px solid #7f1d1d; color:#fecaca;
         padding:10px 12px; border-radius:9px; font-size:13px; margin-bottom:8px; }}
  .foot {{ text-align:center; color:#64748b; font-size:11px; margin-top:18px; }}
</style></head>
<body>
  <form class="card" method="post" action="/login" autocomplete="on">
    <img class="logo" src="/static/thalos-mark.svg" alt="THALOS"
         onerror="this.style.display='none'">
    <h1>APURISK OSINT</h1>
    <div class="sub">Strategic Intelligence · Acceso restringido</div>
    {err}
    <input type="hidden" name="next" value="{next_url}">
    <label for="u">Usuario</label>
    <input id="u" name="username" autofocus required autocomplete="username">
    <label for="p">Contraseña</label>
    <input id="p" name="password" type="password" required autocomplete="current-password">
    <button type="submit">Entrar</button>
    <div class="foot">Powered by THALOS</div>
  </form>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/dashboard"):
    if not _auth_state["login_enforce"]:
        return HTMLResponse(_html_login(
            error="El inicio de sesión no está configurado en este servidor."))
    if auth.verificar_token_sesion(request.cookies.get(_COOKIE_SESION, ""), SECRET_SESION):
        return RedirectResponse(_safe_next(next), status_code=302)
    return HTMLResponse(_html_login(next_url=next))


@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    next_url = _safe_next(form.get("next") or "/dashboard")

    if not _auth_state["login_enforce"]:
        return RedirectResponse("/dashboard", status_code=302)

    user = auth.verificar_credenciales(username, password)
    if not user:
        return HTMLResponse(
            _html_login(error="Usuario o contraseña incorrectos.", next_url=next_url),
            status_code=401)

    token = auth.crear_token_sesion(user["username"], user["rol"], SECRET_SESION, SESION_TTL)
    resp = RedirectResponse(next_url, status_code=302)
    resp.set_cookie(_COOKIE_SESION, token, httponly=True, secure=True,
                    samesite="lax", max_age=SESION_TTL)
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(_COOKIE_SESION)
    return resp


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
                "<h1>APURISK OSINT está iniciando…</h1>"
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
        "service": "APURISK OSINT — Strategic Intelligence for Complex Decisions",
        "powered_by": "THALOS",
        "future_product": "APURISK SIM-CRISIS",
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
    # archive se inyecta para que el EDI (Nivel 3) tenga acceso a histórico
    brief = sintetizar_executive_brief(snap, intel, archive=archive)

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


@app.get("/executive", response_class=HTMLResponse)
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


def _esc_html(s: str) -> str:
    from html import escape
    return escape(str(s))


# =====================================================================
# STRATEGIC DAILY BRIEF PDF — Producto C-level Capa 2 (Strategic Intelligence)
# =====================================================================
@app.get("/api/strategic/daily-brief/pdf")
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
@app.get("/api/strategic/last-24h/pdf")
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


# =====================================================================
# SCORE ENGINE v2 — Validación paralela (Sprint 1.8)
# =====================================================================
@app.get("/api/diagnostico/scores-paralelos")
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


@app.post("/api/diagnostico/scores-paralelos/calcular-hoy")
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


@app.post("/api/diagnostico/scores-paralelos/{fecha}/revision")
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


@app.get("/diagnostico/scores-paralelos", response_class=HTMLResponse)
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
@app.get("/api/matriz/consolidada-7d")
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


@app.get("/matriz-7d", response_class=HTMLResponse)
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
@app.get("/api/matriz/retrospectiva-7d")
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


@app.get("/matriz-retrospectiva-7d", response_class=HTMLResponse)
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


def _get_archive():
    """Devuelve la instancia singleton de ApuriskArchive."""
    try:
        from .storage.archive import ApuriskArchive
    except ImportError:
        from apurisk.storage.archive import ApuriskArchive
    db_path = os.environ.get("APURISK_DB_PATH",
                              str(Path(OUTPUT_DIR) / "apurisk_archive.db"))
    return ApuriskArchive(db_path)


@app.get("/api/edi/snapshot")
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


@app.get("/api/edi/serie")
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


@app.get("/api/diagnostico/historico-edi")
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


@app.get("/api/diagnostico/crisis-tc")
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


@app.get("/api/executive/debug-snapshot")
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


@app.get("/api/executive/sutran-test")
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


@app.get("/api/executive/llm-test")
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
        # Descarga con protección anti-SSRF (ver _fetch_url_segura).
        return _fetch_url_segura(url)

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
        # Descarga con protección anti-SSRF (ver _fetch_url_segura).
        return _fetch_url_segura(url)

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
