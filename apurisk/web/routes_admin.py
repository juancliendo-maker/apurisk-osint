"""APURISK · web/routes_admin — Panel de control administrativo (solo lectura — Fase A).

Acceso: requiere sesión con rol 'admin' + token de origen admin (segundo factor).
Aislamiento: si ADMIN_HOST está definido, solo acepta requests a ese host.

Variables de entorno:
  ADMIN_HOST             → subdominio admin (ej: admin.apurisk.onrender.com)
  ADMIN_PRESHARED_TOKEN  → token de origen, presentado una vez vía ?admin_token=<valor>
                           y persistido como cookie HttpOnly _apurisk_admin_tk (30 días).

Rutas:
  GET /admin/          → Resumen del sistema
  GET /admin/fuentes   → Inventario de fuentes RSS
  GET /admin/factores  → Factores de riesgo activos
  GET /admin/ingestas  → Ingesta manual de URLs
  GET /admin/alertas   → Historial de alertas recientes
  GET /admin/logs      → Estado del scheduler y métricas
"""
from __future__ import annotations
import os
import secrets
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    from .core import (
        _COOKIE_SESION, SECRET_SESION, _auth_state,
        OUTPUT_DIR, _state as _scheduler_state,
    )
    from ..utils import auth
    from ..storage.admin_tables import inicializar_admin_tables
except ImportError:
    from apurisk.web.core import (
        _COOKIE_SESION, SECRET_SESION, _auth_state,
        OUTPUT_DIR, _state as _scheduler_state,
    )
    from apurisk.utils import auth
    from apurisk.storage.admin_tables import inicializar_admin_tables

router = APIRouter(prefix="/admin")

# ── Env vars de aislamiento ─────────────────────────────────────────────────
# ADMIN_HOST: si está definido, solo ese host puede acceder a /admin/*.
_ADMIN_HOST = os.getenv("ADMIN_HOST", "").strip().lower()

# ADMIN_PRESHARED_TOKEN: segundo factor de origen. El analista lo obtiene de
# los env vars de Render y lo presenta UNA VEZ vía ?admin_token=<valor>.
# El servidor planta una cookie HttpOnly de 30 días. Sin esa cookie, las
# rutas /admin/* son inaccesibles aunque la sesión sea válida.
_ADMIN_TOKEN       = os.getenv("ADMIN_PRESHARED_TOKEN", "").strip()
_ADMIN_TOKEN_COOKIE = "_apurisk_admin_tk"
_ADMIN_TOKEN_TTL    = 60 * 60 * 24 * 30  # 30 días en segundos

_TOKEN_ACTIVO = bool(_ADMIN_TOKEN)

if not _TOKEN_ACTIVO:
    print("[admin] ADMIN_PRESHARED_TOKEN no configurado → segundo factor desactivado. "
          "Define ADMIN_PRESHARED_TOKEN en Render para activar el control de origen.")


# ──────────────────────────────────────────────────────────────────────────────
# Auth & aislamiento — tres capas independientes
# ──────────────────────────────────────────────────────────────────────────────

def _verificar_host(request: Request) -> bool:
    """Capa 1: host header debe coincidir con ADMIN_HOST (si está configurado).
    Render fija el Host desde el SNI del TLS handshake — no manipulable por el cliente."""
    if not _ADMIN_HOST:
        return True
    host = request.headers.get("host", "").lower().split(":")[0]
    return host == _ADMIN_HOST


def _verificar_token_origen(request: Request) -> bool:
    """Capa 2: cookie de token de origen (_apurisk_admin_tk).
    Sin ADMIN_PRESHARED_TOKEN configurado, este check siempre pasa (degradación segura)."""
    if not _TOKEN_ACTIVO:
        return True
    cookie_val = request.cookies.get(_ADMIN_TOKEN_COOKIE, "")
    return bool(cookie_val) and secrets.compare_digest(cookie_val, _ADMIN_TOKEN)


def _get_admin_sesion(request: Request) -> dict | None:
    """Capa 3: sesión válida con rol 'admin'."""
    if not _auth_state.get("login_enforce"):
        return {"username": "admin", "rol": "admin"}
    token = request.cookies.get(_COOKIE_SESION, "")
    sesion = auth.verificar_token_sesion(token, SECRET_SESION)
    if not sesion or sesion.get("rol") != "admin":
        return None
    return sesion


def _admin_guard(request: Request):
    """Verifica las tres capas. Devuelve (sesion, response_or_None).

    Si el request trae ?admin_token=<valor> correcto, planta la cookie de origen
    y redirige a la misma ruta sin el query param (limpia el token del historial
    de navegador).
    """
    # Capa 1: host
    if not _verificar_host(request):
        return None, HTMLResponse(_html_403(
            "Acceso denegado desde este host. Usa el subdominio de administración."
        ), status_code=403)

    # Bootstrap del token de origen: si viene ?admin_token=<valor>, validar y plantar cookie.
    query_token = request.query_params.get("admin_token", "").strip()
    if query_token and _TOKEN_ACTIVO:
        if secrets.compare_digest(query_token, _ADMIN_TOKEN):
            # Token correcto: plantar cookie y redirigir sin el param (evita historial)
            from urllib.parse import urlencode
            params = {k: v for k, v in request.query_params.items() if k != "admin_token"}
            clean_url = str(request.url.path)
            if params:
                clean_url += "?" + urlencode(params)
            resp = RedirectResponse(clean_url, status_code=302)
            resp.set_cookie(
                _ADMIN_TOKEN_COOKIE, _ADMIN_TOKEN,
                httponly=True, secure=True, samesite="strict",
                max_age=_ADMIN_TOKEN_TTL,
            )
            return None, resp
        else:
            return None, HTMLResponse(_html_403(
                "Token de acceso admin incorrecto."
            ), status_code=403)

    # Capa 2: cookie de token de origen
    if not _verificar_token_origen(request):
        return None, HTMLResponse(_html_403_token(), status_code=403)

    # Capa 3: sesión admin
    sesion = _get_admin_sesion(request)
    if not sesion:
        from urllib.parse import quote
        return None, RedirectResponse(
            f"/login?next={quote(request.url.path, safe='/')}",
            status_code=302,
        )

    return sesion, None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers HTML — páginas de error y layout
# ──────────────────────────────────────────────────────────────────────────────

_ERR_CSS = ("body{background:#0b1220;color:#e2e8f0;font-family:system-ui;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;}"
            ".box{max-width:460px;text-align:center;padding:32px;}"
            "h1{color:#f59e0b;font-size:28px;margin-bottom:8px}"
            "p{color:#94a3b8;margin:8px 0;font-size:14px;line-height:1.6}"
            "code{background:#1f2a44;padding:2px 6px;border-radius:4px;font-size:13px}"
            "a{color:#38bdf8;text-decoration:none}")


def _html_403(msg: str = "Acceso denegado.") -> str:
    return (f'<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
            f'<title>403 Acceso denegado</title><style>{_ERR_CSS}</style></head>'
            f'<body><div class="box"><h1>⛔ 403</h1><p>{escape(msg)}</p>'
            f'<a href="/dashboard">← Volver al dashboard</a></div></body></html>')


def _html_403_token() -> str:
    host = _ADMIN_HOST or "admin.tudominio.com"
    return (f'<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
            f'<title>Acceso admin requerido</title><style>{_ERR_CSS}</style></head>'
            f'<body><div class="box">'
            f'<h1>🔐 Acceso de origen requerido</h1>'
            f'<p>Este panel requiere un token de acceso de origen que no está presente en tu sesión.</p>'
            f'<p>Para activarlo, accede una sola vez a:<br>'
            f'<code>https://{escape(host)}/admin/?admin_token=TU_TOKEN</code></p>'
            f'<p>El token lo encontrás en las variables de entorno de Render:<br>'
            f'<code>ADMIN_PRESHARED_TOKEN</code></p>'
            f'<p style="font-size:12px;color:#64748b">El token se guardará como cookie segura '
            f'(HttpOnly, 30 días). No volverás a necesitar ingresarlo.</p>'
            f'<a href="/dashboard">← Volver al dashboard</a>'
            f'</div></body></html>')


_CSS = """
:root {
  --bg-0:#0b1220; --bg-1:#111a2e; --bg-2:#162040; --bg-3:#1f2a44;
  --text:#e2e8f0; --muted:#94a3b8;
  --accent:#38bdf8; --accent-2:#818cf8;
  --admin:#f59e0b;          /* ámbar: distingue el admin del dashboard público */
  --admin-dk:#b45309;
  --alto:#f87171; --medio:#fbbf24; --bajo:#34d399; --critica:#ef4444;
  color-scheme: dark;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg-0); color: var(--text);
       font-family: system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
       font-size: 14px; line-height: 1.5; display: flex; min-height: 100vh; }

/* Sidebar */
.sidebar {
  width: 220px; min-height: 100vh; background: var(--bg-1);
  border-right: 1px solid var(--bg-3); display: flex; flex-direction: column;
  flex-shrink: 0; position: sticky; top: 0; height: 100vh; overflow-y: auto;
}
.sb-header {
  padding: 20px 18px 14px; border-bottom: 1px solid var(--bg-3);
}
.sb-logo { font-size: 13px; font-weight: 700; letter-spacing: .5px; color: var(--admin); }
.sb-sub  { font-size: 11px; color: var(--muted); margin-top: 2px; }
.sb-user { font-size: 11px; color: var(--accent); margin-top: 6px; }
nav { padding: 12px 0; flex: 1; }
nav a {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 18px; color: var(--muted); text-decoration: none;
  font-size: 13px; transition: background .15s, color .15s;
}
nav a:hover { background: var(--bg-2); color: var(--text); }
nav a.activo { background: var(--bg-2); color: var(--admin); font-weight: 600;
               border-right: 3px solid var(--admin); }
nav a .ico { font-size: 16px; width: 20px; text-align: center; }
.sb-footer { padding: 14px 18px; border-top: 1px solid var(--bg-3);
             font-size: 11px; color: var(--muted); }
.sb-footer a { color: var(--muted); text-decoration: none; }
.sb-footer a:hover { color: var(--text); }

/* Main content */
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.topbar {
  background: var(--bg-1); border-bottom: 1px solid var(--bg-3);
  padding: 12px 28px; display: flex; align-items: center; gap: 12px;
}
.topbar-title { font-size: 16px; font-weight: 600; }
.topbar-badge {
  background: var(--admin); color: #000; font-size: 10px; font-weight: 700;
  padding: 2px 8px; border-radius: 20px; letter-spacing: .5px;
}
.content { padding: 24px 28px; flex: 1; }

/* Cards */
.card {
  background: var(--bg-1); border: 1px solid var(--bg-3);
  border-radius: 12px; padding: 18px 22px; margin-bottom: 18px;
}
.card-title { font-size: 13px; font-weight: 600; color: var(--muted);
              text-transform: uppercase; letter-spacing: .8px; margin-bottom: 14px; }
.card-accent { border-left: 4px solid var(--admin); }

/* KPI row */
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 14px; margin-bottom: 18px; }
.kpi { background: var(--bg-1); border: 1px solid var(--bg-3);
       border-radius: 12px; padding: 16px 18px; }
.kpi .label { font-size: 11px; color: var(--muted); text-transform: uppercase;
              letter-spacing: .7px; margin-bottom: 6px; }
.kpi .val { font-size: 28px; font-weight: 700; color: var(--accent); }
.kpi .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }

/* Tabla */
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; padding: 8px 10px; color: var(--muted);
          border-bottom: 1px solid var(--bg-3); font-size: 11px;
          text-transform: uppercase; letter-spacing: .6px; }
.tbl td { padding: 9px 10px; border-bottom: 1px solid var(--bg-3); vertical-align: top; }
.tbl tr:last-child td { border-bottom: none; }
.tbl tr:hover td { background: var(--bg-2); }

/* Badges de nivel / estado */
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 20px;
  font-size: 11px; font-weight: 600; letter-spacing: .3px;
}
.badge-critica { background: #3b1212; color: var(--critica); }
.badge-alto    { background: #3b1212; color: var(--alto); }
.badge-medio   { background: #3b2600; color: var(--medio); }
.badge-bajo    { background: #0d2b1f; color: var(--bajo); }
.badge-ok      { background: #0d2b1f; color: var(--bajo); }
.badge-warn    { background: #3b2600; color: var(--medio); }
.badge-off     { background: var(--bg-3); color: var(--muted); }

/* Status dot */
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; }
.dot-ok   { background: var(--bajo); }
.dot-warn { background: var(--medio); }
.dot-err  { background: var(--alto); }

/* Alerts */
.alert-box {
  padding: 12px 16px; border-radius: 8px; margin-bottom: 12px;
  font-size: 13px; border-left: 4px solid;
}
.alert-info  { background: #0c2040; border-color: var(--accent); color: var(--accent); }
.alert-warn  { background: #2a1e00; border-color: var(--medio); color: var(--medio); }

/* Responsive */
@media (max-width: 768px) {
  .sidebar { display: none; }
  .kpi-row { grid-template-columns: repeat(2, 1fr); }
}
"""


def _badge_nivel(nivel: str) -> str:
    n = (nivel or "").upper()
    cls = {"CRÍTICA": "critica", "ALTA": "alto", "MEDIO": "medio",
           "BAJO": "bajo", "MEDIA": "medio"}.get(n, "off")
    return f'<span class="badge badge-{cls}">{escape(nivel or "—")}</span>'


def _nav_html(activo: str, username: str) -> str:
    links = [
        ("resumen",  "📊", "Resumen",       "/admin/"),
        ("fuentes",   "📡", "Fuentes RSS",    "/admin/fuentes"),
        ("factores",  "⚖️",  "Factores",       "/admin/factores"),
        ("ingestas",  "📥", "Ingesta manual", "/admin/ingestas"),
        ("semaforo",  "🚦", "Semáforo OSINT", "/admin/semaforo"),
        ("alertas",   "🚨", "Alertas",        "/admin/alertas"),
        ("logs",      "📋", "Logs sistema",   "/admin/logs"),
    ]
    items = ""
    for key, ico, label, href in links:
        cls = " activo" if key == activo else ""
        items += f'<a href="{href}" class="{cls.strip()}"><span class="ico">{ico}</span>{escape(label)}</a>\n'
    return f"""
<div class="sidebar">
  <div class="sb-header">
    <div class="sb-logo">APURISK ADMIN</div>
    <div class="sb-sub">Panel de control</div>
    <div class="sb-user">👤 {escape(username)}</div>
  </div>
  <nav>{items}</nav>
  <div class="sb-footer">
    <a href="/dashboard">← Dashboard público</a><br>
    <a href="/logout">Cerrar sesión</a>
  </div>
</div>"""


def _page(titulo: str, contenido: str, nav_activo: str, username: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>APURISK Admin · {escape(titulo)}</title>
  <style>{_CSS}</style>
</head>
<body>
  {_nav_html(nav_activo, username)}
  <div class="main">
    <div class="topbar">
      <span class="topbar-title">{escape(titulo)}</span>
      <span class="topbar-badge">ADMIN · SOLO LECTURA</span>
    </div>
    <div class="content">
      {contenido}
    </div>
  </div>
</body>
</html>"""




# ──────────────────────────────────────────────────────────────────────────────
# Helpers de datos
# ──────────────────────────────────────────────────────────────────────────────

def _get_db_path() -> str:
    return os.environ.get("APURISK_DB_PATH", str(OUTPUT_DIR / "apurisk_archive.db"))


def _db_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_get_db_path())
    c.row_factory = sqlite3.Row
    return c


def _ultimo_snapshot() -> dict:
    snaps = sorted(OUTPUT_DIR.glob("apurisk_snapshot_*.json"))
    if not snaps:
        return {}
    try:
        with open(snaps[-1], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cargar_feeds_yaml() -> list[dict]:
    """Lee la lista de feeds desde config.yaml (fuente de verdad en Fase A)."""
    yaml_path = Path(__file__).resolve().parent.parent / "config.yaml"
    try:
        import yaml
        with open(yaml_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("medios_rss", [])
    except Exception:
        return []


def _ago(iso: str | None) -> str:
    """Convierte ISO timestamp a texto relativo ('hace 3h', 'hace 2d')."""
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        s = int(delta.total_seconds())
        if s < 60:
            return "hace <1 min"
        if s < 3600:
            return f"hace {s // 60} min"
        if s < 86400:
            return f"hace {s // 3600}h"
        return f"hace {s // 86400}d"
    except Exception:
        return iso[:16] if iso else "—"


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/   Resumen del sistema
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def admin_resumen(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    snap = _ultimo_snapshot()
    feeds = _cargar_feeds_yaml()
    n_feeds = len(feeds)

    # Métricas del scheduler
    scheduler_ok = _scheduler_state.get("scheduler_running", False)
    total_runs   = _scheduler_state.get("total_runs", 0)
    errores      = _scheduler_state.get("errors", 0)
    last_run     = _scheduler_state.get("last_run_iso")
    next_run     = _scheduler_state.get("next_run_iso")
    last_error   = _scheduler_state.get("last_error")

    # Score y artículos
    score   = snap.get("riesgo", {}).get("score", 0) if snap else 0
    nivel   = snap.get("riesgo", {}).get("nivel", "—") if snap else "—"
    n_arts  = snap.get("riesgo", {}).get("n_articulos", 0) if snap else 0
    n_24h   = snap.get("riesgo", {}).get("n_articulos_24h", 0) if snap else 0

    # Alertas recientes (últimas 24h) desde BD
    n_alertas_24h = 0
    n_alertas_crit = 0
    try:
        with _db_conn() as conn:
            cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            row = conn.execute(
                "SELECT COUNT(*) as c FROM alertas WHERE timestamp >= ?", (cutoff,)
            ).fetchone()
            n_alertas_24h = row["c"] if row else 0
            row2 = conn.execute(
                "SELECT COUNT(*) as c FROM alertas WHERE nivel='CRÍTICA' AND timestamp >= ?",
                (cutoff,)
            ).fetchone()
            n_alertas_crit = row2["c"] if row2 else 0
    except Exception:
        pass

    dot_cls = "dot-ok" if scheduler_ok else "dot-err"
    sch_txt = "En ejecución" if scheduler_ok else "Detenido"

    warn_html = ""
    if last_error:
        warn_html = f'<div class="alert-box alert-warn">⚠️ Último error del scheduler: {escape(str(last_error))}</div>'
    if errores == 0:
        info_html = '<div class="alert-box alert-info">✓ Sistema operando sin errores en este ciclo.</div>'
    else:
        info_html = ""

    contenido = f"""
{warn_html}{info_html}
<div class="kpi-row">
  <div class="kpi">
    <div class="label">Score global</div>
    <div class="val" style="color:var(--{'alto' if score >= 60 else 'medio' if score >= 35 else 'bajo'})">{score:.1f}</div>
    <div class="sub">{escape(nivel)} · {_ago(snap.get('generado') if snap else None)}</div>
  </div>
  <div class="kpi">
    <div class="label">Artículos 24h</div>
    <div class="val">{n_24h}</div>
    <div class="sub">de {n_arts} en total</div>
  </div>
  <div class="kpi">
    <div class="label">Fuentes activas</div>
    <div class="val">{n_feeds}</div>
    <div class="sub">feeds RSS configurados</div>
  </div>
  <div class="kpi">
    <div class="label">Alertas 24h</div>
    <div class="val" style="color:var(--{'alto' if n_alertas_crit > 0 else 'text'})">{n_alertas_24h}</div>
    <div class="sub">{n_alertas_crit} críticas</div>
  </div>
</div>

<div class="card card-accent">
  <div class="card-title">Estado del scheduler</div>
  <table class="tbl">
    <tr><th>Parámetro</th><th>Valor</th></tr>
    <tr><td>Estado</td>
        <td><span class="dot {dot_cls}"></span>{sch_txt}</td></tr>
    <tr><td>Ciclos ejecutados</td><td>{total_runs}</td></tr>
    <tr><td>Errores acumulados</td>
        <td style="color:{'var(--alto)' if errores > 0 else 'inherit'}">{errores}</td></tr>
    <tr><td>Último ciclo</td><td>{_ago(last_run)} ({escape(last_run or '—')})</td></tr>
    <tr><td>Próximo ciclo</td><td>{escape(next_run or '—')}</td></tr>
  </table>
</div>

<div class="card">
  <div class="card-title">Módulos de configuración (Fase A · solo lectura)</div>
  <table class="tbl">
    <tr><th>Módulo</th><th>Estado</th><th>Notas</th></tr>
    <tr>
      <td>Fuentes RSS</td>
      <td><span class="badge badge-warn">Hardcodeado</span></td>
      <td>config.yaml → migración a BD en Fase B</td>
    </tr>
    <tr>
      <td>Keywords / Factores P×I</td>
      <td><span class="badge badge-warn">Hardcodeado</span></td>
      <td>risk_matrix.py → migración a BD en Fase B</td>
    </tr>
    <tr>
      <td>Reglas de alertas</td>
      <td><span class="badge badge-warn">Hardcodeado</span></td>
      <td>alerts.py → migración a BD en Fase B</td>
    </tr>
    <tr>
      <td>Tablas config BD</td>
      <td><span class="badge badge-ok">Creadas</span></td>
      <td>config_fuentes, config_factores, config_keywords, config_alertas_reglas, config_paises, config_parametros</td>
    </tr>
    <tr>
      <td>Auditoría fuentes</td>
      <td><span class="badge badge-ok">Activa</span></td>
      <td>config_fuentes_log lista para registrar cambios en Fase B</td>
    </tr>
    <tr>
      <td>Perfiles de país</td>
      <td><span class="badge badge-ok">Preparados</span></td>
      <td>PE activo · CO/EC/BO/CL configurados (inactivos)</td>
    </tr>
  </table>
</div>
"""
    return HTMLResponse(_page("Resumen del sistema", contenido, "resumen", sesion["username"]))


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/fuentes   Inventario de fuentes RSS
# ──────────────────────────────────────────────────────────────────────────────

def _calidad_color(q: float) -> str:
    if q >= 1.20:
        return "var(--bajo)"
    if q >= 1.00:
        return "var(--accent)"
    return "var(--muted)"


@router.get("/fuentes", response_class=HTMLResponse)
async def admin_fuentes(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    from ..storage.config_loader import listar_fuentes

    fuentes = listar_fuentes(_get_db_path())

    # Banner de resultado de la última edición (via query param)
    msg = request.query_params.get("msg", "")
    err_msg = request.query_params.get("err", "")
    banner = ""
    if msg:
        banner = f'<div class="alert-box alert-info">✓ {escape(msg)}</div>'
    elif err_msg:
        banner = f'<div class="alert-box alert-warn">⚠️ {escape(err_msg)}</div>'

    _CAT_LABEL = {
        "medios": "Medios nacionales", "estado": "Estado peruano",
        "internacional": "Internacional", "regional": "Medios regionales",
        "especializado": "Especializado (minería/anticorrupción)",
        "social": "Redes sociales / Foros",
    }

    n_total   = len(fuentes)
    n_activas = sum(1 for f in fuentes if f.get("activo"))
    n_inact   = n_total - n_activas

    if not fuentes:
        contenido = """
<div class="alert-box alert-warn">
  Aún no hay fuentes en <code>config_fuentes</code>. Se poblarán automáticamente
  desde <code>config.yaml</code> en el próximo arranque del servidor.
</div>"""
        return HTMLResponse(_page("Fuentes RSS", contenido, "fuentes", sesion["username"]))

    # Agrupar por categoría
    grupos: dict[str, list] = {}
    for f in fuentes:
        grupos.setdefault(f.get("categoria") or "medios", []).append(f)

    filas_html = ""
    for cat, items in grupos.items():
        cat_label = _CAT_LABEL.get(cat, (cat or "otros").title())
        filas_html += (f'<tr><td colspan="6" style="background:var(--bg-2);color:var(--muted);'
                       f'font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;'
                       f'padding:8px 10px">{escape(cat_label)} ({len(items)})</td></tr>\n')
        for f in items:
            fid    = f["id"]
            fname  = escape(f.get("nombre") or "—")
            furl   = f.get("url_feed") or ""
            q      = float(f.get("calidad") or 1.0)
            q_col  = _calidad_color(q)
            activo = bool(f.get("activo"))
            via_gn = "news.google.com" in furl

            estado_badge = ('<span class="badge badge-ok">Activa</span>' if activo
                            else '<span class="badge badge-off">Inactiva</span>')
            tipo_badge = ('<span class="badge badge-warn">Google News</span>' if via_gn
                          else '<span class="badge badge-ok">Directo</span>')
            toggle_label = "Desactivar" if activo else "Activar"
            toggle_val = "0" if activo else "1"
            row_op = "" if activo else "opacity:.55;"

            pa     = float(f.get("peso_analista") or 1.0)
            pa_col = _calidad_color(pa)

            filas_html += f"""<tr style="{row_op}">
  <td>{fname}<br><span style="color:var(--muted);font-size:10px">{tipo_badge}</span></td>
  <td>{estado_badge}</td>
  <td>
    <form method="post" action="/admin/fuentes/calidad" style="display:flex;gap:4px;align-items:center">
      <input type="hidden" name="fuente_id" value="{fid}">
      <input type="number" name="calidad" value="{q:.2f}" step="0.05" min="0.1" max="2.0"
             style="width:64px;padding:3px 6px;border-radius:6px;border:1px solid var(--bg-3);
                    background:var(--bg-0);color:{q_col};font-weight:600;font-size:12px">
      <button type="submit" style="padding:3px 8px;border:0;border-radius:6px;
              background:var(--bg-3);color:var(--text);font-size:11px;cursor:pointer">✓</button>
    </form>
  </td>
  <td>
    <form method="post" action="/admin/fuentes/peso_analista" style="display:flex;gap:4px;align-items:center">
      <input type="hidden" name="fuente_id" value="{fid}">
      <input type="number" name="peso_analista" value="{pa:.2f}" step="0.05" min="0.1" max="2.0"
             style="width:64px;padding:3px 6px;border-radius:6px;border:1px solid var(--bg-3);
                    background:var(--bg-0);color:{pa_col};font-weight:600;font-size:12px">
      <button type="submit" style="padding:3px 8px;border:0;border-radius:6px;
              background:var(--bg-3);color:var(--text);font-size:11px;cursor:pointer">✓</button>
    </form>
  </td>
  <td>
    <form method="post" action="/admin/fuentes/toggle" style="margin:0">
      <input type="hidden" name="fuente_id" value="{fid}">
      <input type="hidden" name="activo" value="{toggle_val}">
      <button type="submit" style="padding:4px 10px;border:1px solid var(--bg-3);border-radius:6px;
              background:{'#2a1620' if activo else '#0d2b1f'};
              color:{'var(--alto)' if activo else 'var(--bajo)'};font-size:11px;cursor:pointer">
        {toggle_label}</button>
    </form>
  </td>
  <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
    <a href="{escape(furl)}" target="_blank" rel="noopener noreferrer"
       style="color:var(--muted);font-size:11px">{escape(furl[:60] + ('…' if len(furl) > 60 else ''))}</a>
  </td>
</tr>\n"""

    contenido = f"""
{banner}
<div class="alert-box alert-info">
  ℹ️ Edición en vivo: activar/desactivar una fuente la incluye o excluye del próximo
  ciclo del pipeline. Cambiar la calidad ajusta el multiplicador de score de esa fuente.
  Todos los cambios quedan registrados en el <a href="/admin/fuentes/log" style="color:var(--accent)">log de auditoría</a>.
</div>

<div class="kpi-row">
  <div class="kpi">
    <div class="label">Total fuentes</div>
    <div class="val">{n_total}</div>
    <div class="sub">en config_fuentes</div>
  </div>
  <div class="kpi">
    <div class="label">Activas</div>
    <div class="val" style="color:var(--bajo)">{n_activas}</div>
    <div class="sub">entran al pipeline</div>
  </div>
  <div class="kpi">
    <div class="label">Inactivas</div>
    <div class="val" style="color:var(--muted)">{n_inact}</div>
    <div class="sub">excluidas del ciclo</div>
  </div>
</div>

<div class="card">
  <div class="card-title">Fuentes — edición</div>
  <div style="font-size:11px;color:var(--muted);margin-bottom:12px">
    Calidad: multiplicador al score de artículos de esa fuente (rango 0.10–2.00; &gt;1 amplifica, &lt;1 atenúa).
  </div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Fuente</th><th>Estado</th>
        <th title="Multiplicador automático basado en historial de confiabilidad">Calidad auto</th>
        <th title="Multiplicador manual del analista — sobreescribe calidad en el pipeline">Peso analista</th>
        <th>Acción</th><th>URL</th>
      </tr></thead>
      <tbody>{filas_html}</tbody>
    </table>
  </div>
</div>
"""
    return HTMLResponse(_page("Fuentes RSS", contenido, "fuentes", sesion["username"]))


# ──────────────────────────────────────────────────────────────────────────────
# POST /admin/fuentes/toggle   Activar/desactivar fuente
# POST /admin/fuentes/calidad  Editar calidad de fuente
# GET  /admin/fuentes/log      Auditoría de cambios
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/fuentes/toggle")
async def admin_fuentes_toggle(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import actualizar_fuente, LockTimeoutError
    form = await request.form()
    try:
        fuente_id = int(form.get("fuente_id"))
        activo = form.get("activo", "1")
        r = actualizar_fuente(_get_db_path(), fuente_id, "activo", activo,
                              usuario=sesion["username"], motivo="toggle desde panel")
        estado = "activada" if r["valor_nuevo"] == 1 else "desactivada"
        return RedirectResponse(f"/admin/fuentes?msg=Fuente+{estado}", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            "/admin/fuentes?err=El+sistema+está+actualizando+datos+(ciclo+automático).+"
            "El+cambio+NO+se+guardó.+Reintenta+en+unos+segundos.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/fuentes?err={escape(str(e))}", status_code=303)


@router.post("/fuentes/calidad")
async def admin_fuentes_calidad(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import actualizar_fuente, LockTimeoutError
    form = await request.form()
    try:
        fuente_id = int(form.get("fuente_id"))
        calidad = form.get("calidad")
        r = actualizar_fuente(_get_db_path(), fuente_id, "calidad", calidad,
                              usuario=sesion["username"], motivo="edición de calidad")
        return RedirectResponse(
            f"/admin/fuentes?msg=Calidad+actualizada+a+{r['valor_nuevo']}", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            "/admin/fuentes?err=El+sistema+está+actualizando+datos+(ciclo+automático).+"
            "El+cambio+NO+se+guardó.+Reintenta+en+unos+segundos.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/fuentes?err={escape(str(e))}", status_code=303)


@router.post("/fuentes/peso_analista")
async def admin_fuentes_peso_analista(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import actualizar_fuente, LockTimeoutError
    form = await request.form()
    try:
        fuente_id = int(form.get("fuente_id"))
        peso = form.get("peso_analista")
        r = actualizar_fuente(_get_db_path(), fuente_id, "peso_analista", peso,
                              usuario=sesion["username"], motivo="edición de peso analista")
        return RedirectResponse(
            f"/admin/fuentes?msg=Peso+analista+actualizado+a+{r['valor_nuevo']}", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            "/admin/fuentes?err=El+sistema+está+actualizando+datos+(ciclo+automático).+"
            "El+cambio+NO+se+guardó.+Reintenta+en+unos+segundos.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/fuentes?err={escape(str(e))}", status_code=303)


@router.get("/fuentes/log", response_class=HTMLResponse)
async def admin_fuentes_log(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import listar_log_fuentes
    logs = listar_log_fuentes(_get_db_path(), limite=200)

    filas = ""
    for l in logs:
        ts = (l.get("cambiado_en") or "")[:19].replace("T", " ")
        campo = escape(l.get("campo") or "—")
        va = escape(str(l.get("valor_anterior") if l.get("valor_anterior") is not None else "—"))
        vn = escape(str(l.get("valor_nuevo") if l.get("valor_nuevo") is not None else "—"))
        filas += f"""<tr>
  <td style="color:var(--muted);font-size:12px;white-space:nowrap">{ts}</td>
  <td>{escape(l.get('fuente_nombre') or ('#' + str(l.get('fuente_id'))))}</td>
  <td><span class="badge badge-off">{campo}</span></td>
  <td style="color:var(--muted)">{va} → <b style="color:var(--text)">{vn}</b></td>
  <td style="color:var(--accent);font-size:12px">{escape(l.get('usuario') or '—')}</td>
  <td style="color:var(--muted);font-size:12px">{escape(l.get('motivo') or '—')}</td>
</tr>\n"""

    cuerpo = (filas if logs else
              '<tr><td colspan="6" style="color:var(--muted);padding:16px">'
              'Sin cambios registrados todavía.</td></tr>')

    contenido = f"""
<div class="alert-box alert-info">
  Registro de auditoría de cambios en fuentes (calidad, estado activo/inactivo).
  <a href="/admin/fuentes" style="color:var(--accent)">← Volver a fuentes</a>
</div>
<div class="card">
  <div class="card-title">Auditoría de cambios — config_fuentes_log ({len(logs)})</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Fecha</th><th>Fuente</th><th>Campo</th><th>Cambio</th><th>Usuario</th><th>Motivo</th>
      </tr></thead>
      <tbody>{cuerpo}</tbody>
    </table>
  </div>
</div>
"""
    return HTMLResponse(_page("Auditoría de fuentes", contenido, "fuentes", sesion["username"]))


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/factores   Editor de pesos de factores P×I (Fase B Item 2)
# POST /admin/factores/peso  Actualiza impacto_base o prob_base de un factor
# ──────────────────────────────────────────────────────────────────────────────

def _peso_color(val: int, campo: str) -> str:
    """Color para el badge de impacto_base o prob_base."""
    if campo == "impacto_base":
        if val >= 80:
            return "var(--alto)"
        if val >= 60:
            return "var(--warn, #e5a500)"
        return "var(--bajo)"
    else:  # prob_base
        if val >= 30:
            return "var(--alto)"
        if val >= 20:
            return "var(--warn, #e5a500)"
        return "var(--bajo)"


@router.get("/factores", response_class=HTMLResponse)
async def admin_factores(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    from ..storage.config_loader import listar_factores_config
    factores_cfg = listar_factores_config(_get_db_path())

    # Último snapshot para mostrar valores reales del pipeline (prob/impacto/score actual)
    factores_snap: dict[str, dict] = {}
    try:
        with _db_conn() as conn:
            rows = conn.execute("""
                SELECT f.factor_id, f.probabilidad, f.impacto, f.score,
                       f.nivel, f.tendencia, f.menciones_24h
                FROM factores f
                JOIN snapshots s ON f.snapshot_id = s.id
                WHERE s.id = (SELECT MAX(id) FROM snapshots)
            """).fetchall()
            factores_snap = {r["factor_id"]: dict(r) for r in rows}
    except Exception:
        pass

    msg = request.query_params.get("msg", "")
    msg_tipo = request.query_params.get("tipo", "info")
    msg_html = ""
    if msg:
        cls = {"ok": "alert-ok", "err": "alert-err", "warn": "alert-warn"}.get(msg_tipo, "alert-info")
        msg_html = f'<div class="alert-box {cls}">{escape(msg)}</div>'

    sin_datos = not factores_cfg
    aviso = '<div class="alert-box alert-warn">config_factores vacía. Reinicia el servidor para poblarla automáticamente.</div>' if sin_datos else ""

    filas = ""
    for f in factores_cfg:
        fid   = f.get("factor_id") or ""
        fname = escape(f.get("nombre") or "—")
        fcat  = escape(f.get("categoria") or "—")
        imp   = int(f.get("impacto_base", 60))
        prob  = int(f.get("prob_base", 25))
        snap  = factores_snap.get(fid) or {}
        prob_real  = snap.get("probabilidad", "—")
        imp_real   = snap.get("impacto", "—")
        score_real = snap.get("score")
        nivel_real = snap.get("nivel", "—")
        tend  = snap.get("tendencia") or "—"
        tend_ico = {"subiendo": "▲", "bajando": "▼", "estable": "▬"}.get(tend, "—")
        tend_col = {"subiendo": "var(--alto)", "bajando": "var(--bajo)", "estable": "var(--muted)"}.get(tend, "var(--muted)")
        score_txt = f"{score_real:.1f}" if isinstance(score_real, (int, float)) else "—"

        filas += f"""<tr>
  <td>
    <span style="color:var(--muted);font-size:10px">{escape(fid)}</span><br>
    <b>{fname}</b><br>
    <span style="color:var(--muted);font-size:11px">{fcat}</span>
  </td>
  <td style="white-space:nowrap">
    <span style="color:{_peso_color(imp,'impacto_base')};font-weight:700;font-size:15px">{imp}</span>
    <form method="post" action="/admin/factores/peso" style="display:inline-flex;gap:4px;margin-left:6px;vertical-align:middle">
      <input type="hidden" name="factor_id" value="{escape(fid)}">
      <input type="hidden" name="campo" value="impacto_base">
      <input type="number" name="valor" value="{imp}" min="1" max="100"
             style="width:58px;padding:2px 4px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:4px;font-size:12px">
      <button type="submit" style="padding:2px 8px;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px">✓</button>
    </form>
  </td>
  <td style="white-space:nowrap">
    <span style="color:{_peso_color(prob,'prob_base')};font-weight:700;font-size:15px">{prob}%</span>
    <form method="post" action="/admin/factores/peso" style="display:inline-flex;gap:4px;margin-left:6px;vertical-align:middle">
      <input type="hidden" name="factor_id" value="{escape(fid)}">
      <input type="hidden" name="campo" value="prob_base">
      <input type="number" name="valor" value="{prob}" min="1" max="95"
             style="width:54px;padding:2px 4px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:4px;font-size:12px">
      <button type="submit" style="padding:2px 8px;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px">✓</button>
    </form>
  </td>
  <td style="font-size:13px;color:var(--muted)">{prob_real}% → {imp_real}</td>
  <td style="font-size:15px;font-weight:700;color:var(--accent)">{score_txt}</td>
  <td>{_badge_nivel(nivel_real)}</td>
  <td style="color:{tend_col}">{tend_ico}</td>
  <td><a href="/admin/factores/{escape(fid)}/keywords" style="font-size:12px;color:var(--accent);text-decoration:none">✏️ keywords</a></td>
</tr>\n"""

    contenido = f"""
{msg_html}
<div class="alert-box alert-info">
  <b>Editor de pesos base P×I.</b> Los cambios toman efecto en el próximo ciclo del pipeline (hasta 30 min).
  impacto_base: 1–100 · prob_base: 1–95%.
  Los valores <b>Prob. real</b> e <b>Impacto real</b> son los calculados por el pipeline en el último ciclo.
</div>
{aviso}
<div class="card">
  <div class="card-title">Factores P×I — pesos base configurables ({len(factores_cfg)} factores)</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Factor</th>
        <th>Impacto base</th>
        <th>Prob. base</th>
        <th>Prob. real → Imp. real</th>
        <th>Score actual</th>
        <th>Nivel</th>
        <th>Tend.</th>
        <th></th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
</div>
"""
    return HTMLResponse(_page("Factores de riesgo", contenido, "factores", sesion["username"]))


@router.post("/factores/peso", response_class=HTMLResponse)
async def admin_factores_peso(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    form = await request.form()
    factor_id = (form.get("factor_id") or "").strip()
    campo     = (form.get("campo") or "").strip()
    valor_str = (form.get("valor") or "").strip()

    from ..storage.config_loader import actualizar_factor_peso, LockTimeoutError
    from fastapi.responses import RedirectResponse as RR
    try:
        r = actualizar_factor_peso(
            _get_db_path(), factor_id, campo, valor_str,
            usuario=sesion["username"],
        )
        msg = f"✓ {campo.replace('_', ' ')} de '{factor_id}' actualizado: {r['valor_anterior']} → {r['valor_nuevo']}. Activo en próximo ciclo."
        return RR(f"/admin/factores?msg={escape(msg)}&tipo=ok", status_code=303)
    except LockTimeoutError:
        msg = "El sistema está actualizando datos (ciclo automático en curso). El cambio NO se guardó. Reintenta en unos segundos."
        return RR(f"/admin/factores?msg={escape(msg)}&tipo=err", status_code=303)
    except ValueError as e:
        return RR(f"/admin/factores?msg={escape(str(e))}&tipo=err", status_code=303)
    except Exception as e:
        return RR(f"/admin/factores?msg=Error+inesperado:+{escape(str(e))}&tipo=err", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# GET  /admin/factores/{factor_id}/keywords   Editor de keywords de un factor
# POST /admin/factores/{factor_id}/keywords/agregar
# POST /admin/factores/{factor_id}/keywords/desactivar
# ──────────────────────────────────────────────────────────────────────────────

def _tipo_label(tipo: str) -> str:
    return {"fuerte": "💪 Fuerte", "contexto": "📌 Contexto", "negacion": "🚫 Negación"}.get(tipo, tipo)

def _tipo_color(tipo: str) -> str:
    return {"fuerte": "var(--accent)", "contexto": "var(--text)", "negacion": "var(--muted)"}.get(tipo, "var(--text)")


@router.get("/factores/{factor_id}/keywords", response_class=HTMLResponse)
async def admin_keywords(request: Request, factor_id: str):
    sesion, err = _admin_guard(request)
    if err:
        return err

    from ..storage.config_loader import listar_keywords_factor
    # Buscar nombre del factor
    from ..analyzers.risk_matrix import FACTORES as _FACTORES
    factor_meta = next((f for f in _FACTORES if f["id"] == factor_id), None)
    if factor_meta is None:
        return HTMLResponse(_html_404(request), status_code=404)

    keywords = listar_keywords_factor(_get_db_path(), factor_id)

    msg = request.query_params.get("msg", "")
    msg_tipo = request.query_params.get("tipo", "info")
    msg_html = ""
    if msg:
        cls = {"ok": "alert-ok", "err": "alert-err", "warn": "alert-warn"}.get(msg_tipo, "alert-info")
        msg_html = f'<div class="alert-box {cls}">{escape(msg)}</div>'

    # Agrupar por tipo para mostrar en secciones
    por_tipo: dict[str, list] = {"fuerte": [], "contexto": [], "negacion": []}
    for kw in keywords:
        t = kw.get("tipo", "")
        if t in por_tipo:
            por_tipo[t].append(kw)

    secciones = ""
    for tipo in ("fuerte", "contexto", "negacion"):
        filas_kw = ""
        for kw in por_tipo[tipo]:
            activo = kw.get("activo", 1)
            estilo_kw = "color:var(--muted);text-decoration:line-through" if not activo else f"color:{_tipo_color(tipo)}"
            estado_txt = "inactiva" if not activo else ""
            boton_desact = ""
            if activo:
                boton_desact = f"""<form method="post" action="/admin/factores/{escape(factor_id)}/keywords/desactivar" style="display:inline">
  <input type="hidden" name="kw_id" value="{kw['id']}">
  <button type="submit" title="Desactivar" style="background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;padding:0 4px">×</button>
</form>"""
            filas_kw += f"""<tr>
  <td style="{estilo_kw};font-size:13px">{escape(kw.get('keyword',''))}</td>
  <td style="color:var(--muted);font-size:11px">{estado_txt}</td>
  <td>{boton_desact}</td>
</tr>\n"""

        secciones += f"""<div class="card" style="margin-bottom:16px">
  <div class="card-title">{_tipo_label(tipo)} ({len([k for k in por_tipo[tipo] if k.get('activo',1)])} activas)</div>
  <div style="font-size:11px;color:var(--muted);margin-bottom:8px">
    {"Frases específicas multipalabra. Un match activa el factor." if tipo=="fuerte" else
     "Palabras de respaldo. Se necesitan ≥2 coincidencias simultáneas." if tipo=="contexto" else
     "Si aparece cualquiera de estas, se descarta la nota del factor."}
  </div>
  <div style="overflow-x:auto">
    <table class="tbl" style="margin-bottom:8px">
      <tbody>{filas_kw or '<tr><td colspan="3" style="color:var(--muted)">— sin keywords —</td></tr>'}</tbody>
    </table>
  </div>
  <form method="post" action="/admin/factores/{escape(factor_id)}/keywords/agregar"
        style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <input type="hidden" name="tipo" value="{tipo}">
    <input type="text" name="keyword" placeholder="Nueva keyword de tipo {tipo}..."
           style="flex:1;min-width:220px;padding:6px 10px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:13px">
    <button type="submit" style="padding:6px 14px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px">+ Agregar</button>
  </form>
</div>\n"""

    nombre_factor = escape(factor_meta.get("nombre", factor_id))
    cat_factor    = escape(factor_meta.get("categoria", ""))

    contenido = f"""
{msg_html}
<div style="margin-bottom:16px">
  <a href="/admin/factores" style="color:var(--muted);font-size:13px;text-decoration:none">← Volver a factores</a>
</div>
<div class="card" style="margin-bottom:16px">
  <div class="card-title">{nombre_factor}</div>
  <div style="color:var(--muted);font-size:12px">{cat_factor} · <code style="font-size:11px">{escape(factor_id)}</code></div>
</div>
<div class="alert-box alert-info">
  Los cambios de keywords toman efecto en el <b>próximo ciclo del pipeline</b> (hasta 30 min).
  Desactivar una keyword la oculta del matching sin borrarla (trazabilidad).
</div>
{secciones}
"""
    return HTMLResponse(_page(f"Keywords · {nombre_factor}", contenido, "factores", sesion["username"]))


@router.post("/factores/{factor_id}/keywords/agregar", response_class=HTMLResponse)
async def admin_keywords_agregar(request: Request, factor_id: str):
    sesion, err = _admin_guard(request)
    if err:
        return err

    form = await request.form()
    tipo    = (form.get("tipo") or "").strip()
    keyword = (form.get("keyword") or "").strip()

    from ..storage.config_loader import agregar_keyword, LockTimeoutError
    from fastapi.responses import RedirectResponse as RR
    base = f"/admin/factores/{factor_id}/keywords"
    try:
        r = agregar_keyword(_get_db_path(), factor_id, tipo, keyword, usuario=sesion["username"])
        accion = r.get("accion", "ok")
        msg = f"✓ Keyword '{r['keyword']}' {accion} en tipo '{tipo}'. Activa en próximo ciclo."
        return RR(f"{base}?msg={escape(msg)}&tipo=ok", status_code=303)
    except LockTimeoutError:
        msg = "El sistema está actualizando datos. El cambio NO se guardó. Reintenta en unos segundos."
        return RR(f"{base}?msg={escape(msg)}&tipo=err", status_code=303)
    except ValueError as e:
        return RR(f"{base}?msg={escape(str(e))}&tipo=err", status_code=303)
    except Exception as e:
        return RR(f"{base}?msg=Error+inesperado:+{escape(str(e))}&tipo=err", status_code=303)


@router.post("/factores/{factor_id}/keywords/desactivar", response_class=HTMLResponse)
async def admin_keywords_desactivar(request: Request, factor_id: str):
    sesion, err = _admin_guard(request)
    if err:
        return err

    form = await request.form()
    try:
        kw_id = int(form.get("kw_id") or 0)
    except ValueError:
        kw_id = 0

    from ..storage.config_loader import desactivar_keyword, LockTimeoutError
    from fastapi.responses import RedirectResponse as RR
    base = f"/admin/factores/{factor_id}/keywords"
    try:
        r = desactivar_keyword(_get_db_path(), kw_id, usuario=sesion["username"])
        msg = f"✓ Keyword '{r['keyword']}' ({r['tipo']}) desactivada. Activo en próximo ciclo."
        return RR(f"{base}?msg={escape(msg)}&tipo=ok", status_code=303)
    except LockTimeoutError:
        msg = "El sistema está actualizando datos. El cambio NO se guardó. Reintenta en unos segundos."
        return RR(f"{base}?msg={escape(msg)}&tipo=err", status_code=303)
    except ValueError as e:
        return RR(f"{base}?msg={escape(str(e))}&tipo=err", status_code=303)
    except Exception as e:
        return RR(f"{base}?msg=Error+inesperado:+{escape(str(e))}&tipo=err", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# GET  /admin/ingestas           Formulario + historial de ingesta manual de URLs
# POST /admin/ingestas/fetch     Fetch de metadatos de una URL (AJAX-like via form)
# POST /admin/ingestas/guardar   Guarda la ingesta en BD
# ──────────────────────────────────────────────────────────────────────────────

_TRIGGER_B2 = 10  # ingestas/día que sugieren migrar a Postgres


def _trigger_html(n_hoy: int, n_semana: int) -> str:
    pct = min(100, int(n_hoy / _TRIGGER_B2 * 100))
    if n_hoy >= _TRIGGER_B2:
        color = "var(--alto)"
        aviso = (f"<b>⚠️ Trigger B2 alcanzado ({n_hoy}/{_TRIGGER_B2} hoy · {n_semana} esta semana).</b> "
                 "Considera migrar a PostgreSQL para soporte de ingesta a alta frecuencia.")
    elif n_hoy >= _TRIGGER_B2 * 0.7:
        color = "var(--warn, #e5a500)"
        aviso = f"Acercándote al trigger B2 — <b>{n_hoy}/{_TRIGGER_B2} hoy</b> · {n_semana} esta semana."
    else:
        color = "var(--bajo)"
        aviso = f"Ingestas: <b>{n_hoy}/{_TRIGGER_B2} hoy</b> · {n_semana} esta semana (umbral de migración B2)."
    return f"""<div class="alert-box" style="border-left:4px solid {color};padding:10px 14px;margin-bottom:14px">
  {aviso}
  <div style="margin-top:6px;background:var(--bg2);border-radius:4px;height:8px;width:100%">
    <div style="width:{pct}%;background:{color};height:8px;border-radius:4px;transition:width 0.3s"></div>
  </div>
</div>"""


@router.get("/ingestas", response_class=HTMLResponse)
async def admin_ingestas(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    from ..storage.config_loader import listar_ingestas, contar_ingestas
    ingestas = listar_ingestas(_get_db_path(), limite=30)
    _conteo = contar_ingestas(_get_db_path())
    n_hoy    = _conteo["hoy"]
    n_semana = _conteo["semana"]

    msg = request.query_params.get("msg", "")
    msg_tipo = request.query_params.get("tipo", "info")
    # Prefill del formulario tras fetch
    pf_url     = request.query_params.get("url", "")
    pf_titulo  = request.query_params.get("titulo", "")
    pf_resumen = request.query_params.get("resumen", "")
    pf_fuente  = request.query_params.get("fuente", "")
    pf_error   = request.query_params.get("fetch_error", "")

    msg_html = ""
    if msg:
        cls = {"ok": "alert-ok", "err": "alert-err", "warn": "alert-warn"}.get(msg_tipo, "alert-info")
        msg_html = f'<div class="alert-box {cls}">{escape(msg)}</div>'

    fetch_error_html = (f'<div class="alert-box alert-warn">No se pudo extraer metadatos automáticamente: '
                        f'{escape(pf_error)}. Rellena título y resumen manualmente.</div>'
                        if pf_error else "")

    filas = ""
    for ing in ingestas:
        url_ing  = escape(ing.get("url") or "")
        titulo   = escape((ing.get("titulo") or "")[:60] or ing.get("url","")[:60])
        fuente   = escape(ing.get("fuente") or "—")
        cat      = escape(ing.get("categoria") or "—")
        proc     = ing.get("procesada", 0)
        estado   = ('<span style="color:var(--bajo)">✓ procesada</span>'
                    if proc else '<span style="color:var(--warn,#e5a500)">⏳ pendiente</span>')
        ts_raw = ing.get("ingresada_en") or ""
        try:
            from datetime import timedelta as _td
            _ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if _ts.tzinfo is None:
                _ts = _ts.replace(tzinfo=timezone.utc)
            ts_str = (_ts + _td(hours=-5)).strftime("%Y-%m-%d %H:%M") + " PET"
        except Exception:
            ts_str = ts_raw[:16]
        by = escape(ing.get("ingresada_por") or "—")
        filas += f"""<tr>
  <td style="font-size:12px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
    <a href="{url_ing}" target="_blank" rel="noopener noreferrer"
       style="color:var(--accent);text-decoration:none" title="{url_ing}">{titulo}</a>
  </td>
  <td style="color:var(--muted);font-size:12px">{fuente}</td>
  <td style="color:var(--muted);font-size:12px">{cat}</td>
  <td>{estado}</td>
  <td style="color:var(--muted);font-size:11px;white-space:nowrap">{ts_str}</td>
  <td style="color:var(--muted);font-size:11px">{by}</td>
</tr>\n"""

    contenido = f"""
{msg_html}
{_trigger_html(n_hoy, n_semana)}

<div class="card" style="margin-bottom:20px">
  <div class="card-title">Agregar URL para análisis</div>
  <div style="font-size:12px;color:var(--muted);margin-bottom:12px">
    Paso 1: pega la URL y haz clic en <b>Extraer metadatos</b> para pre-rellenar título y resumen.
    Paso 2: revisa/edita y haz clic en <b>Guardar en cola</b>.
    El artículo se incluirá en el <b>próximo ciclo del pipeline</b> (hasta 30 min).
  </div>

  <form method="post" action="/admin/ingestas/fetch" style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
    <input type="text" name="url" value="{escape(pf_url)}" placeholder="https://www.elperu.pe/..."
           style="flex:1;min-width:300px;padding:8px 12px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:13px">
    <button type="submit"
            style="padding:8px 16px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;cursor:pointer;font-size:13px">
      🔍 Extraer metadatos
    </button>
  </form>

  {fetch_error_html}

  <form method="post" action="/admin/ingestas/guardar">
    <div style="display:grid;gap:10px">
      <div>
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">URL *</label>
        <input type="url" name="url" value="{escape(pf_url)}" required
               style="width:100%;box-sizing:border-box;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:13px">
      </div>
      <div>
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Título</label>
        <input type="text" name="titulo" value="{escape(pf_titulo)}"
               style="width:100%;box-sizing:border-box;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:13px">
      </div>
      <div>
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Resumen / extracto</label>
        <textarea name="resumen" rows="3"
                  style="width:100%;box-sizing:border-box;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:13px;resize:vertical">{escape(pf_resumen)}</textarea>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <div style="flex:1;min-width:160px">
          <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Fuente (dominio)</label>
          <input type="text" name="fuente" value="{escape(pf_fuente)}"
                 style="width:100%;box-sizing:border-box;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:13px">
        </div>
        <div style="flex:1;min-width:160px">
          <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Categoría</label>
          <select name="categoria"
                  style="width:100%;box-sizing:border-box;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:13px">
            <option value="medios">medios</option>
            <option value="estado">estado</option>
            <option value="internacional">internacional</option>
            <option value="redes">redes</option>
          </select>
        </div>
      </div>
      <div>
        <button type="submit"
                style="padding:9px 22px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:14px;font-weight:600">
          📥 Guardar en cola de análisis
        </button>
      </div>
    </div>
  </form>
</div>

<div class="card">
  <div class="card-title">Historial de ingestas ({len(ingestas)} recientes)</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Artículo</th><th>Fuente</th><th>Cat.</th>
        <th>Estado</th><th>Ingresada</th><th>Por</th>
      </tr></thead>
      <tbody>{filas or '<tr><td colspan="6" style="color:var(--muted)">Sin ingestas aún.</td></tr>'}</tbody>
    </table>
  </div>
</div>
"""
    return HTMLResponse(_page("Ingesta manual", contenido, "ingestas", sesion["username"]))


@router.post("/ingestas/fetch", response_class=HTMLResponse)
async def admin_ingestas_fetch(request: Request):
    """Fetch de metadatos de la URL y redirige a /admin/ingestas con prefill."""
    sesion, err = _admin_guard(request)
    if err:
        return err

    form = await request.form()
    url = (form.get("url") or "").strip()
    from fastapi.responses import RedirectResponse as RR
    from urllib.parse import urlencode

    if not url:
        return RR("/admin/ingestas?msg=URL+vacía&tipo=err", status_code=303)

    from ..utils.url_fetcher import fetch_articulo
    meta = fetch_articulo(url)
    params = {"url": url, "titulo": meta.get("titulo",""),
              "resumen": meta.get("resumen",""), "fuente": meta.get("fuente","")}
    if meta.get("error"):
        params["fetch_error"] = meta["error"]
    return RR("/admin/ingestas?" + urlencode(params), status_code=303)


@router.post("/ingestas/guardar", response_class=HTMLResponse)
async def admin_ingestas_guardar(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    form = await request.form()
    url       = (form.get("url") or "").strip()
    titulo    = (form.get("titulo") or "").strip()
    resumen   = (form.get("resumen") or "").strip()
    fuente    = (form.get("fuente") or "").strip()
    categoria = (form.get("categoria") or "medios").strip()

    from ..storage.config_loader import guardar_ingesta_manual, LockTimeoutError
    from fastapi.responses import RedirectResponse as RR
    try:
        from ..utils.timezone_pe import now_pe_iso
    except ImportError:
        from apurisk.utils.timezone_pe import now_pe_iso

    try:
        guardar_ingesta_manual(
            _get_db_path(), url, titulo, resumen, fuente, categoria,
            published=now_pe_iso(), usuario=sesion["username"],
        )
        msg = f"✓ URL guardada en cola. Se procesará en el próximo ciclo del pipeline (hasta 30 min)."
        return RR(f"/admin/ingestas?msg={escape(msg)}&tipo=ok", status_code=303)
    except LockTimeoutError:
        msg = "El sistema está actualizando datos. La ingesta NO se guardó. Reintenta en unos segundos."
        return RR(f"/admin/ingestas?msg={escape(msg)}&tipo=err", status_code=303)
    except ValueError as e:
        return RR(f"/admin/ingestas?msg={escape(str(e))}&tipo=err", status_code=303)
    except Exception as e:
        return RR(f"/admin/ingestas?msg=Error+inesperado:+{escape(str(e))}&tipo=err", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/alertas   Historial de alertas recientes
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/alertas", response_class=HTMLResponse)
async def admin_alertas(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    alertas: list[dict] = []
    conteo: dict[str, int] = {}
    try:
        with _db_conn() as conn:
            cutoff = (datetime.utcnow() - timedelta(hours=72)).isoformat()
            rows = conn.execute("""
                SELECT nivel, categoria, regla, titulo, fuente, url, region, timestamp
                FROM alertas
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 200
            """, (cutoff,)).fetchall()
            alertas = [dict(r) for r in rows]

            # Conteo por nivel
            for row in conn.execute(
                "SELECT nivel, COUNT(*) as c FROM alertas WHERE timestamp >= ? GROUP BY nivel",
                (cutoff,)
            ).fetchall():
                conteo[row["nivel"]] = row["c"]
    except Exception:
        pass

    filas = ""
    for a in alertas:
        url   = a.get("url") or ""
        titulo = escape(a.get("titulo") or "—")
        if url:
            titulo = f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer" style="color:var(--text)">{titulo}</a>'
        _ts_raw = a.get("timestamp") or ""
        try:
            from datetime import timedelta as _td
            _ts = datetime.fromisoformat(_ts_raw.replace("Z", "+00:00"))
            if _ts.tzinfo is None:
                _ts = _ts.replace(tzinfo=timezone.utc)
            _ts_pet = _ts + _td(hours=-5)
            ts_str = _ts_pet.strftime("%Y-%m-%d %H:%M") + " PET"
        except Exception:
            ts_str = _ts_raw[:16].replace("T", " ")
        filas += f"""<tr>
  <td>{_badge_nivel(a.get('nivel') or '—')}</td>
  <td style="color:var(--muted);font-size:12px">{escape(a.get('categoria') or '—')}</td>
  <td style="font-size:12px;color:var(--muted)">{escape(a.get('regla') or '—')}</td>
  <td>{titulo}</td>
  <td style="color:var(--muted);font-size:12px">{escape(a.get('fuente') or '—')}</td>
  <td style="color:var(--muted);font-size:12px;white-space:nowrap">{ts_str}</td>
</tr>\n"""

    n_crit = conteo.get("CRÍTICA", 0)
    n_alta = conteo.get("ALTA", 0)
    n_med  = conteo.get("MEDIA", 0)

    sin_datos = not alertas
    aviso = '<div class="alert-box alert-warn">Sin alertas en las últimas 72h en BD.</div>' if sin_datos else ""

    contenido = f"""
<div class="kpi-row">
  <div class="kpi">
    <div class="label">Alertas 72h</div>
    <div class="val">{len(alertas)}</div>
    <div class="sub">últimas 72 horas</div>
  </div>
  <div class="kpi">
    <div class="label">Críticas</div>
    <div class="val" style="color:var(--critica)">{n_crit}</div>
    <div class="sub">nivel CRÍTICA</div>
  </div>
  <div class="kpi">
    <div class="label">Altas</div>
    <div class="val" style="color:var(--alto)">{n_alta}</div>
    <div class="sub">nivel ALTA</div>
  </div>
  <div class="kpi">
    <div class="label">Medias</div>
    <div class="val" style="color:var(--medio)">{n_med}</div>
    <div class="sub">nivel MEDIA</div>
  </div>
</div>
{aviso}
<div class="card">
  <div class="card-title">Alertas recientes (últimas 72h)</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Nivel</th><th>Categoría</th><th>Regla</th>
        <th>Título</th><th>Fuente</th><th>Timestamp</th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
</div>
"""
    return HTMLResponse(_page("Historial de alertas", contenido, "alertas", sesion["username"]))


# ──────────────────────────────────────────────────────────────────────────────
# GET /admin/logs   Estado del scheduler y métricas técnicas
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/logs", response_class=HTMLResponse)
async def admin_logs(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    scheduler_ok = _scheduler_state.get("scheduler_running", False)
    total_runs   = _scheduler_state.get("total_runs", 0)
    errores      = _scheduler_state.get("errors", 0)
    last_run     = _scheduler_state.get("last_run_iso")
    next_run     = _scheduler_state.get("next_run_iso")
    last_error   = _scheduler_state.get("last_error")

    dot_cls = "dot-ok" if scheduler_ok else "dot-err"
    sch_txt = "En ejecución" if scheduler_ok else "Detenido"

    # Serie temporal de scores (últimas 10 entradas)
    serie: list[dict] = []
    try:
        with _db_conn() as conn:
            rows = conn.execute("""
                SELECT generado, score_global, nivel, n_articulos, n_articulos_24h
                FROM snapshots
                ORDER BY id DESC LIMIT 10
            """).fetchall()
            serie = [dict(r) for r in rows]
    except Exception:
        pass

    filas_serie = ""
    for s in serie:
        ts  = (s.get("generado") or "")[:16].replace("T", " ")
        sc  = s.get("score_global") or 0
        niv = s.get("nivel") or "—"
        na  = s.get("n_articulos") or 0
        n24 = s.get("n_articulos_24h") or 0
        filas_serie += f"""<tr>
  <td style="color:var(--muted);font-size:12px">{ts}</td>
  <td style="font-weight:700;color:var(--accent)">{sc:.1f}</td>
  <td>{_badge_nivel(niv)}</td>
  <td>{na}</td>
  <td>{n24}</td>
</tr>\n"""

    # Parámetros del motor desde BD
    parametros: list[dict] = []
    try:
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT clave, valor, tipo, descripcion FROM config_parametros ORDER BY clave"
            ).fetchall()
            parametros = [dict(r) for r in rows]
    except Exception:
        pass

    filas_params = ""
    for p in parametros:
        filas_params += f"""<tr>
  <td style="font-family:monospace;color:var(--accent)">{escape(p['clave'])}</td>
  <td style="font-weight:600">{escape(p['valor'])}</td>
  <td style="color:var(--muted)">{escape(p['tipo'])}</td>
  <td style="color:var(--muted)">{escape(p.get('descripcion') or '—')}</td>
</tr>\n"""

    err_html = ""
    if last_error:
        err_html = f"""
<div class="card" style="border-color:var(--alto)">
  <div class="card-title" style="color:var(--alto)">⚠️ Último error registrado</div>
  <pre style="color:var(--medio);font-size:12px;white-space:pre-wrap;word-break:break-all">{escape(str(last_error))}</pre>
</div>"""

    sin_serie = not serie
    sin_params = not parametros

    _serie_html = (
        '<div style="color:var(--muted);font-size:13px">Sin snapshots en BD aún.</div>'
        if sin_serie else
        '<div style="overflow-x:auto"><table class="tbl">'
        '<thead><tr><th>Timestamp</th><th>Score</th><th>Nivel</th>'
        '<th>Artículos total</th><th>Artículos 24h</th></tr></thead>'
        f'<tbody>{filas_serie}</tbody></table></div>'
    )
    _params_html = (
        '<div style="color:var(--muted);font-size:13px">Sin parámetros en BD aún.</div>'
        if sin_params else
        '<div style="overflow-x:auto"><table class="tbl">'
        '<thead><tr><th>Clave</th><th>Valor</th><th>Tipo</th><th>Descripción</th></tr></thead>'
        f'<tbody>{filas_params}</tbody></table></div>'
    )
    _err_col = "var(--alto)" if errores > 0 else "var(--bajo)"
    _admin_host_label = escape(_ADMIN_HOST or "(no configurado — acceso solo por rol)")

    contenido = f"""
<div class="card card-accent">
  <div class="card-title">Estado del scheduler</div>
  <table class="tbl">
    <tr><th>Parámetro</th><th>Valor</th></tr>
    <tr><td>Estado</td>
        <td><span class="dot {dot_cls}"></span><b>{sch_txt}</b></td></tr>
    <tr><td>Ciclos ejecutados (esta sesión)</td><td>{total_runs}</td></tr>
    <tr><td>Errores acumulados</td>
        <td style="color:{_err_col}">{errores}</td></tr>
    <tr><td>Último ciclo completado</td><td>{_ago(last_run)}<br>
        <span style="color:var(--muted);font-size:11px">{escape(last_run or '—')}</span></td></tr>
    <tr><td>Próximo ciclo</td>
        <td><span style="font-size:11px">{escape(next_run or '—')}</span></td></tr>
    <tr><td>Host admin configurado (ADMIN_HOST)</td>
        <td><code style="color:var(--accent)">{_admin_host_label}</code></td></tr>
    <tr><td>Segundo factor de origen (ADMIN_PRESHARED_TOKEN)</td>
        <td>{'<span class="badge badge-ok">Activo</span>' if _TOKEN_ACTIVO else '<span class="badge badge-warn">No configurado</span>'}</td></tr>
  </table>
</div>

{err_html}

<div class="card">
  <div class="card-title">Serie temporal de scores (últimos 10 ciclos)</div>
  {_serie_html}
</div>

<div class="card">
  <div class="card-title">Parámetros del motor de scoring</div>
  {_params_html}
</div>
"""
    return HTMLResponse(_page("Logs del sistema", contenido, "logs", sesion["username"]))


# ──────────────────────────────────────────────────────────────────────────────
# Semáforo OSINT — resultado del motor multiplicativo (Fase C)
# ──────────────────────────────────────────────────────────────────────────────

def _color_nivel(nivel: str) -> str:
    return {
        "ROJO": "#7B0000", "ROJO PROBABLE": "#C0392B",
        "NARANJA ALTO": "#E06000", "AMARILLO": "#F5A623",
        "VERDE": "#2D9E56",
    }.get(nivel or "", "#888")


def _origen_badge(origen: str) -> str:
    cls = {"real": "ok", "proxy": "warn", "estimado": "alto"}.get(origen, "off")
    return f'<span class="badge badge-{cls}">{escape(origen)}</span>'


@router.get("/semaforo")
async def admin_semaforo(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    db_path = str(Path(OUTPUT_DIR) / "apurisk_archive.db")

    # Leer último resultado OSINT desde snapshot JSON más reciente
    osint = None
    out_dir = Path(OUTPUT_DIR)
    snaps = sorted(out_dir.glob("apurisk_snapshot_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    for s in snaps[:1]:
        try:
            data = json.loads(s.read_text(encoding="utf-8"))
            osint = data.get("osint_motor")
            break
        except Exception:
            pass

    if not osint:
        contenido = """
<div class="card card-accent">
  <div class="card-title">🚦 Semáforo OSINT</div>
  <p style="color:var(--muted)">Sin resultado disponible aún.
  El motor OSINT corre automáticamente en cada ciclo del scheduler.
  Espera el próximo ciclo o ejecuta una corrida manual.</p>
</div>"""
        return HTMLResponse(_page("Semáforo OSINT", contenido, "semaforo", sesion["username"]))

    sem = osint.get("semaforo", {})
    nivel = sem.get("nivel_interpretado") or sem.get("nivel") or "—"
    score = sem.get("score", 0)
    color = _color_nivel(nivel)
    vol = osint.get("volumen", {})
    factores = osint.get("factores_semaforo", {})
    puntos = osint.get("puntos", [])
    cobertura = osint.get("cobertura_factores", "?")
    sin_dato = osint.get("factores_sin_dato", [])
    procesado = osint.get("procesado_en", "—")

    # ── Tabla de factores ──
    filas_fact = ""
    for fk, fd in factores.items():
        barra_w = int(fd.get("valor", 0) * 100)
        filas_fact += (
            f"<tr><td><b>{escape(fk)}</b><br>"
            f"<span style='font-size:11px;color:var(--muted)'>{escape(fd.get('nombre',''))}</span></td>"
            f"<td>{fd.get('valor', 0):.3f}</td>"
            f"<td>{fd.get('peso', 1.0):.1f}</td>"
            f"<td>{_origen_badge(fd.get('origen','?'))}</td>"
            f"<td><div style='background:#ddd;border-radius:3px;height:10px;width:100px'>"
            f"<div style='background:{color};width:{barra_w}px;height:10px;border-radius:3px'></div>"
            f"</div></td></tr>"
        )

    # ── Activadores ──
    act = sem.get("activador_disparado", False)
    act_tipo = sem.get("activador_tipo") or "—"
    act_html = (
        f'<span class="badge badge-alto">DISPARADO ({escape(act_tipo)})</span>'
        if act else '<span class="badge badge-ok">ninguno</span>'
    )

    # ── Puntos del reporte ──
    puntos_html = ""
    for p in puntos:
        capa = p.get("capa", "señal")
        capa_cls = "badge-warn" if capa == "interpretativa" else "badge-ok"
        advertencia = (
            f'<div style="color:var(--alto);font-size:11px;margin-top:4px">'
            f'⚠ {escape(p.get("advertencia",""))}</div>'
            if p.get("advertencia") else ""
        )
        resultado = p.get("resultado")
        if isinstance(resultado, list):
            res_html = "<ul style='margin:4px 0 0 16px'>" + "".join(
                f"<li>{escape(str(r))}</li>" for r in resultado
            ) + "</ul>"
        elif isinstance(resultado, dict):
            res_html = f"<pre style='font-size:11px;overflow:auto'>{escape(json.dumps(resultado, ensure_ascii=False, indent=2))}</pre>"
        else:
            res_html = f"<b>{escape(str(resultado))}</b>"

        puntos_html += f"""
<div style="border-left:3px solid {color if capa=='interpretativa' else '#4A90D9'};
     padding:8px 12px;margin-bottom:8px;background:var(--card-bg)">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
    <span style="color:var(--muted);font-size:12px">#{p.get('punto','?')}</span>
    <b>{escape(p.get('titulo',''))}</b>
    <span class="badge {capa_cls}">{escape(capa)}</span>
  </div>
  {res_html}
  {advertencia}
</div>"""

    contenido = f"""
<div class="card card-accent">
  <div class="card-title">🚦 Semáforo OSINT — Motor Multiplicativo</div>
  <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">
    <div style="background:{color};color:#fff;border-radius:12px;
         padding:18px 32px;font-size:28px;font-weight:bold;text-align:center;min-width:160px">
      {escape(nivel)}<br>
      <span style="font-size:14px;opacity:.85">score: {score:.4f}</span>
    </div>
    <div>
      <table class="tbl" style="min-width:260px">
        <tr><th>Volumen</th><td><b>{escape(vol.get('clase','?'))}</b>
            — {vol.get('n_bruto',0)} artículos · {vol.get('n_fuentes',0)} fuentes</td></tr>
        <tr><th>Activadores</th><td>{act_html}</td></tr>
        <tr><th>Cobertura</th><td>{escape(cobertura)}</td></tr>
        <tr><th>Factores sin dato</th>
            <td style="font-size:11px;color:var(--muted)">{escape(', '.join(sin_dato)) or '—'}</td></tr>
        <tr><th>Fórmula</th><td><code>∏(factor_i ^ peso_i)</code></td></tr>
        <tr><th>Procesado</th><td style="font-size:11px">{escape(procesado)}</td></tr>
      </table>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-title">Factores del semáforo</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr><th>Factor</th><th>Valor</th><th>Peso (exp)</th>
             <th>Origen</th><th>Visual</th></tr></thead>
      <tbody>{filas_fact}</tbody>
    </table>
  </div>
  <p style="color:var(--muted);font-size:11px;margin-top:8px">
    Con peso=1.0: score ≡ VC × PA × CE × IA × V (multiplicación pura).
    <b>estimado</b> = heurística no verificada · <b>proxy</b> = señal indirecta · <b>real</b> = dato computado.
  </p>
</div>

<div class="card">
  <div class="card-title">Reporte de 10 puntos</div>
  {puntos_html}
</div>"""

    return HTMLResponse(_page("Semáforo OSINT", contenido, "semaforo", sesion["username"]))
