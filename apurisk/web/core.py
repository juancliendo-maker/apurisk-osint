"""APURISK · web/core — Configuración y utilidades compartidas.

Constantes de entorno, estado de autenticación, helpers anti-SSRF, rate limiting,
acceso al archive SQLite, último snapshot y caché del Executive Brief.
No crea la app FastAPI ni registra rutas.
"""
from __future__ import annotations
import os
import json
import time
import socket
import secrets
import ipaddress
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

try:
    from ..storage import ApuriskArchive
except ImportError:
    from apurisk.storage import ApuriskArchive

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
SESION_TTL = int(os.getenv("APURISK_SESION_TTL", str(60 * 60 * 4)))  # 4h
_COOKIE_SESION = "apurisk_sesion"
# Holder mutable: el middleware lee si debe exigir login. Se fija en _startup
# (requiere que exista al menos un usuario para no dejar a nadie afuera).
_auth_state = {"login_enforce": False}



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


def _esc_html(s: str) -> str:
    from html import escape
    return escape(str(s))


def _get_archive():
    """Devuelve la instancia singleton de ApuriskArchive."""
    try:
        from .storage.archive import ApuriskArchive
    except ImportError:
        from apurisk.storage.archive import ApuriskArchive
    db_path = os.environ.get("APURISK_DB_PATH",
                              str(Path(OUTPUT_DIR) / "apurisk_archive.db"))
    return ApuriskArchive(db_path)


# ======================================================================
# Helpers
# ======================================================================
def _ultimo_snapshot_path() -> Optional[Path]:
    snaps = sorted(OUTPUT_DIR.glob("apurisk_snapshot_*.json"))
    return snaps[-1] if snaps else None
