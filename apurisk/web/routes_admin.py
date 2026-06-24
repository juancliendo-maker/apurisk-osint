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
        ("calibrar",  "🎛️",  "Calibración",    "/admin/semaforo/calibrar"),
        ("actores",   "🎭", "Actores",        "/admin/actores"),
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
# Semáforo OSINT — matrices P×I comparables (Fase C)
# ──────────────────────────────────────────────────────────────────────────────

# Impacto político base por tema (0-100, fijo por categoría)
_IMPACTO_TEMA: dict[str, int] = {
    "estabilidad_gobierno":  92,
    "corrupcion":            78,
    "conflictos_sociales":   72,
    "seguridad":             70,
    "polarizacion":          65,
    "riesgo_regulatorio":    62,
    "economico_inversion":   60,
    "electoral":             55,
}

# Colores por nivel (fondo oscuro, igual que dashboard)
_COLOR_NIVEL_BURB: dict[str, str] = {
    "CRÍTICO": "#ef4444",
    "ALTO":    "#f97316",
    "MEDIO":   "#f59e0b",
    "BAJO":    "#22c55e",
}


def _color_nivel(nivel: str) -> str:
    return {
        "ROJO": "#7B0000", "ROJO PROBABLE": "#C0392B",
        "NARANJA ALTO": "#E06000", "AMARILLO": "#F5A623",
        "VERDE": "#2D9E56",
    }.get(nivel or "", "#888")


def _nivel_score(score: float) -> str:
    if score >= 70:
        return "CRÍTICO"
    if score >= 55:
        return "ALTO"
    if score >= 35:
        return "MEDIO"
    return "BAJO"


def _origen_badge(origen: str) -> str:
    cls = {"real": "ok", "proxy": "warn", "estimado": "alto"}.get(origen, "off")
    return f'<span class="badge badge-{cls}">{escape(origen)}</span>'


def _construir_datos_semaforo(osint: dict, db_path: str = None) -> dict:
    """Construye los datos de ambas matrices y sus Score Globales desde el snapshot OSINT.

    Matriz A (volumen):    X = frecuencia relativa, Y = impacto político base.
    Matriz B (estructural): X = actividad actual del tema (mismo cálculo que A),
                            Y = gravedad estructural = max(piso, impacto_base).
    Los ejes de B son independientes: X cambia semana a semana, Y es estructural.

    db_path: BD para cargar pisos estructurales y parámetros editables. Si es None
             o falla, pisos=0 (Y=impacto_base) y se usan defaults documentados.

    Devuelve {globos_a, globos_b, score_global_a, score_global_b,
              nivel_a, nivel_b, formula_score_b, umbral_x, umbral_y,
              cuadrantes_b, factores_globales}.
    """
    import math

    # ── Parámetros editables y pisos estructurales (BD, con defaults) ─────────
    pisos = {}
    params = {
        "umbral_x": 25.0, "umbral_y": 65.0,
        "coef_actividad": 8.0, "coef_simultaneidad": 3.5, "bonus_max": 15.0,
        "x_max_viz": 0.0,  # 0 = dinámico (al máximo real + margen)
    }
    pa_por_tema: dict = {}
    if db_path:
        try:
            from ..storage.config_loader import (
                cargar_pisos_estructurales, cargar_parametros_semaforo,
                cargar_pa_por_tema,
            )
            pisos = cargar_pisos_estructurales(db_path, "PE")
            params = cargar_parametros_semaforo(db_path)
            pa_por_tema = cargar_pa_por_tema(db_path, "PE")
        except Exception as e:
            print(f"[semaforo] no se pudieron cargar pisos/params/pa: {e}")

    umbral_x = params["umbral_x"]
    umbral_y = params["umbral_y"]

    # ── Extraer datos base del snapshot ──────────────────────────────────────
    puntos = {p["punto"]: p for p in osint.get("puntos", [])}
    factores_sem = osint.get("factores_semaforo", {})

    # Conteos por tema — preferimos la ventana deslizante 7D si está disponible.
    # temas_7d_conteos se calcula en _guardar() de main.py con rolling 7D desde BD.
    # Fallback al punto 2 del ciclo actual (comportamiento anterior) si no existe.
    p2 = puntos.get(2, {})
    conteos_ciclo = p2.get("detalle", {})           # {tema: conteo} — solo ciclo actual
    conteos_7d = osint.get("temas_7d_conteos", {})  # {tema: conteo} — ventana 7D rolling
    detalle_temas = conteos_7d if conteos_7d else conteos_ciclo
    usando_7d = bool(conteos_7d)
    total_menciones = max(1, sum(detalle_temas.values()))
    entidad_top_por_tema = p2.get("entidad_top_por_tema", {})  # {tema: {entidad, menciones}}

    # Emparejamiento actor visible → actor determinante (por alias y nombre)
    actores_db_cache: list = []
    if db_path:
        try:
            from ..storage.config_loader import listar_actores, emparejar_entidad_con_actor
            actores_db_cache = listar_actores(db_path, pais="PE", solo_activos=True)
        except Exception as e:
            print(f"[semaforo] no se pudieron cargar actores para emparejamiento: {e}")

    # Valores globales de los 5 factores del semáforo (para tabla informativa)
    fval = {k: float(v.get("valor", 0)) for k, v in factores_sem.items()}
    forigen = {k: v.get("origen", "proxy") for k, v in factores_sem.items()}
    fpeso = {k: float(v.get("peso", 1.0)) for k, v in factores_sem.items()}

    # Actividad del tema = % del volumen total de la ventana activa (7D o ciclo).
    # Con ventana 7D: mismo criterio que la P×I del dashboard — comparable entre matrices.
    def _actividad(menciones: int) -> float:
        return round((menciones / total_menciones) * 100, 1)

    # ── Velocidad por tema: %actividad(0-7d) − %actividad(7-14d) ──────────────
    # Mide si el tema gana cuota de cobertura mediática. El color del globo (urgencia)
    # se deriva de esto. Si no hay ventana previa, velocidad = 0 (todo "latente").
    conteos_prev = osint.get("temas_prev7d_conteos", {})  # {tema: conteo} ventana 7-14d
    total_prev = max(1, sum(conteos_prev.values()))
    hay_velocidad = bool(conteos_prev)

    def _velocidad(tema: str) -> float:
        act_reciente = (detalle_temas.get(tema, 0) / total_menciones) * 100
        act_previa = (conteos_prev.get(tema, 0) / total_prev) * 100
        return round(act_reciente - act_previa, 1)

    # Umbrales de urgencia (editables desde calibración)
    vel_urgente = params.get("vel_urgente", 30.0)
    vel_prioritario = params.get("vel_prioritario", 10.0)

    # Colores de urgencia (no de gravedad). Alineados con la Plantilla Madre.
    _COLOR_URGENTE     = "#dc2626"  # rojo brillante — grave + escalando fuerte
    _COLOR_PRIORITARIO = "#f59e0b"  # ámbar — grave + movimiento moderado
    _COLOR_IMPORTANTE  = "#94a3b8"  # gris — grave pero quieto (latente)
    _COLOR_NO_GRAVE    = "#475569"  # slate apagado — no grave (sin importar velocidad)

    def _clasificar_urgencia(y: float, velocidad: float) -> tuple[str, str]:
        """Devuelve (clase_urgencia, color_hex) desde gravedad + velocidad."""
        if y < umbral_y:
            return "no_grave", _COLOR_NO_GRAVE
        if velocidad >= vel_urgente:
            return "URGENTE", _COLOR_URGENTE
        if velocidad >= vel_prioritario:
            return "PRIORITARIO", _COLOR_PRIORITARIO
        return "IMPORTANTE", _COLOR_IMPORTANTE

    # ── MATRIZ A — por volumen ────────────────────────────────────────────────
    globos_a = []
    for tema, impacto_base in _IMPACTO_TEMA.items():
        menciones = detalle_temas.get(tema, 0)
        if menciones == 0:
            continue
        x = _actividad(menciones)
        y = impacto_base
        score_ab = round(math.sqrt(x * y), 1)
        nivel = _nivel_score(score_ab)
        globos_a.append({
            "tema": tema,
            "x": x,
            "y": y,
            # Radio reducido 50% respecto al diseño anterior (menciones×4 → ×2)
            "r": max(4, min(18, menciones * 2)),
            "score": score_ab,
            "nivel": nivel,
            "menciones": menciones,
            "pct_volumen": round(menciones / total_menciones * 100, 1),
            "color": _COLOR_NIVEL_BURB[nivel],
        })

    # Score Global A = promedio ponderado por proporción de volumen
    score_global_a = 0.0
    if globos_a:
        score_global_a = round(
            sum(g["score"] * g["pct_volumen"] for g in globos_a) / 100.0, 1
        )
    nivel_a = _nivel_score(score_global_a)

    # ── MATRIZ B — actividad (X) vs gravedad estructural (Y) ──────────────────
    # X = actividad actual del tema (volumen relativo, cambia semana a semana).
    # Y = gravedad estructural = max(piso_estructural, impacto_base). Piso lo fija
    #     el analista; con piso=0, Y = impacto_base.
    # Ejes independientes → ningún cuadrante queda vacío por construcción.
    # Cuadrantes (umbrales editables umbral_x / umbral_y):
    #   Y≥umbral_y, X<umbral_x  → GRAVE PERO SILENCIOSO (prioridad de inteligencia)
    #   Y≥umbral_y, X≥umbral_x  → GRAVE Y ACTIVO
    #   Y<umbral_y, X≥umbral_x  → RUIDOSO PERO MENOR
    #   Y<umbral_y, X<umbral_x  → TRANQUILO
    globos_b = []
    for tema, impacto_base in _IMPACTO_TEMA.items():
        menciones = detalle_temas.get(tema, 0)
        piso = pisos.get(tema, 0.0)
        x = _actividad(menciones)               # actividad: puede ser 0 (silencioso)

        # Y = gravedad estructural. Si hay actores reales, PA_tema reemplaza impacto_base.
        pa_info = pa_por_tema.get(tema)
        if pa_info:
            y_base = pa_info["pa"]
            origen_pa = "real"
        else:
            y_base = float(impacto_base)
            origen_pa = "estimado"
        y = round(max(piso, y_base), 1)

        grave = y >= umbral_y
        activo = x >= umbral_x
        if grave and not activo:
            cuadrante = "GRAVE PERO SILENCIOSO"
        elif grave and activo:
            cuadrante = "GRAVE Y ACTIVO"
        elif (not grave) and activo:
            cuadrante = "RUIDOSO PERO MENOR"
        else:
            cuadrante = "TRANQUILO"

        # Actor visible vs determinante
        ent_info = entidad_top_por_tema.get(tema)
        actor_visible_nombre = ent_info["entidad"] if ent_info else None
        actor_visible_menciones = ent_info["menciones"] if ent_info else 0
        actor_visible_match = None
        if actor_visible_nombre and actores_db_cache:
            actor_visible_match = emparejar_entidad_con_actor(
                actor_visible_nombre, actores_db_cache
            )
        actor_determinante_nombre = pa_info["actor_principal"] if pa_info else None
        # Brecha: visible ≠ determinante (comparamos nombre de actor emparejado vs determinante)
        nombre_match = (actor_visible_match or {}).get("nombre") if actor_visible_match else None
        hay_brecha = (
            actor_visible_nombre is not None
            and actor_determinante_nombre is not None
            and nombre_match != actor_determinante_nombre
        )

        # Velocidad 7d y clasificación de URGENCIA (define el color, no la gravedad)
        velocidad = _velocidad(tema)
        urgencia, color_urgencia = _clasificar_urgencia(y, velocidad)

        # nivel de gravedad se conserva para tablas/leyendas que aún lo usan
        nivel = _nivel_score(y)
        globos_b.append({
            "tema": tema,
            "x": x,
            "y": y,
            # Radio reducido (anti-encimado): máx 14 en vez de 20
            "r": max(4, min(14, round(menciones * 1.5))),
            "score": y,
            "nivel": nivel,
            "menciones": menciones,
            "piso": round(piso, 1),
            "impacto_base": impacto_base,
            "cuadrante": cuadrante,
            # color ahora codifica URGENCIA (velocidad), no gravedad
            "color": color_urgencia,
            "urgencia": urgencia,
            "velocidad": velocidad,
            "origen_pa": origen_pa,
            "pa_info": pa_info,
            "actor_visible": actor_visible_nombre,
            "actor_visible_menciones": actor_visible_menciones,
            "actor_visible_en_base": actor_visible_match is not None,
            "actor_determinante": actor_determinante_nombre,
            "hay_brecha": hay_brecha,
        })

    # ── Score Global B — "temperatura del momento" (urgencia, no gravedad) ────
    # La gravedad de fondo ya se ve en la POSICIÓN de los globos (eje Y). El Score
    # mide el momentum: sube con temas escalando, baja cuando todo está grave pero
    # quieto. Nunca se pega en 100 salvo escalada extrema simultánea.
    #
    #   G_base  = PISO_GRAVEDAD · (n_graves / n_total)      ← piso "todo grave pero quieto"
    #   U_score = max(0, vel_max_grave) + coef_sim · max(0, n_escalando − 1)
    #   U_norm  = min(1, U_score / URGENCIA_REF)            ← urgencia normalizada 0–1
    #   Score_B = G_base + (100 − G_base) · U_norm          ← el headroom lo llena la urgencia
    coef_sim = params["coef_simultaneidad"]
    piso_gravedad = params.get("piso_gravedad", 65.0)
    urgencia_ref = max(1.0, params.get("urgencia_ref", 50.0))

    score_global_b = 0.0
    formula_score_b = (
        f"G_base + (100−G_base)·U_norm · "
        f"G_base={piso_gravedad:g}·(n_graves/n_total) · "
        f"U_norm=min(1, [vel_max_grave + {coef_sim:g}·(n_escalando−1)] / {urgencia_ref:g})"
    )
    if globos_b:
        n_total = len(globos_b)
        graves = [g for g in globos_b if g["y"] >= umbral_y]
        n_graves = len(graves)
        frac_graves = n_graves / max(1, n_total)
        g_base = piso_gravedad * frac_graves

        # Velocidad máxima entre los temas graves (frente más caliente)
        vel_max_grave = max([max(0.0, g["velocidad"]) for g in graves], default=0.0)
        # Temas graves escalando (velocidad ≥ umbral prioritario)
        n_escalando = sum(1 for g in graves if g["velocidad"] >= vel_prioritario)

        u_score = vel_max_grave + coef_sim * max(0, n_escalando - 1)
        u_norm = min(1.0, u_score / urgencia_ref)
        score_global_b = round(min(100.0, g_base + (100.0 - g_base) * u_norm), 1)
    nivel_b = _nivel_score(score_global_b)

    # ── Escala visible del eje X (no toca los datos, solo el "zoom") ──────────
    # x_max_viz=0 → dinámico: ceil(X_max_real/10)×10 + 10, acotado a [30, 100].
    # Acerca la vista cuando ningún tema supera ~40% del volumen, sin inflar X.
    # Cualquier valor >0 fija la escala (útil para comparar semanas).
    import math as _math
    x_real_max = max([g["x"] for g in (globos_a + globos_b)] or [0])
    if params["x_max_viz"] and params["x_max_viz"] > 0:
        x_max_viz = float(params["x_max_viz"])
    else:
        x_max_viz = min(100, max(30, _math.ceil(x_real_max / 10) * 10 + 10))

    # Etiquetas de cuadrante para el dibujo de la matriz B (esquinas)
    cuadrantes_b = {
        "ti": "GRAVE PERO SILENCIOSO",   # top-left
        "td": "GRAVE Y ACTIVO",          # top-right
        "bi": "TRANQUILO",               # bottom-left
        "bd": "RUIDOSO PERO MENOR",      # bottom-right
    }

    return {
        "globos_a": globos_a,
        "globos_b": globos_b,
        "score_global_a": score_global_a,
        "score_global_b": score_global_b,
        "nivel_a": nivel_a,
        "nivel_b": nivel_b,
        "formula_score_b": formula_score_b,
        "umbral_x": umbral_x,
        "umbral_y": umbral_y,
        "x_max_viz": x_max_viz,
        "cuadrantes_b": cuadrantes_b,
        "factores_globales": {k: {"valor": fval.get(k, 0), "origen": forigen.get(k, "proxy"),
                                   "peso": fpeso.get(k, 1.0)} for k in ["VC","PA","CE","IA","V"]},
        "pa_por_tema": pa_por_tema,
        "usando_7d": usando_7d,
        "total_menciones_7d": total_menciones if usando_7d else 0,
        "hay_velocidad": hay_velocidad,
        "vel_urgente": vel_urgente,
        "vel_prioritario": vel_prioritario,
    }


def _matriz_bubble_html(canvas_id: str, globos: list, titulo_x: str,
                         titulo_y: str, altura: int = 340,
                         umbral_x: float = 50, umbral_y: float = 50,
                         etiquetas: dict = None,
                         eje_x_corto: str = "X", eje_y_corto: str = "Y",
                         x_max: float = 100) -> str:
    """Genera el bloque <canvas> + <script> de una matriz de burbujas con Chart.js.

    globos: lista de dicts con x, y, r, score, nivel, tema, menciones, color.
            Matriz B añade: piso, impacto_base, cuadrante (tooltip extendido).
    umbral_x / umbral_y: posición de las líneas divisorias de cuadrante (0-100).
    etiquetas: {ti, td, bi, bd} con los textos de cada esquina. Si None, usa P×I.
    eje_x_corto / eje_y_corto: prefijos de la primera línea del tooltip.
    x_max: tope visible del eje X (los datos no cambian; solo el "zoom" del eje).
    """
    if etiquetas is None:
        etiquetas = {
            "ti": "Alto Imp · Baja Prob",
            "td": "Alto Imp · Alta Prob (CRÍTICO)",
            "bi": "Bajo Imp · Baja Prob",
            "bd": "Bajo Imp · Alta Prob",
        }

    datasets = []
    for g in globos:
        # Construir líneas del tooltip
        if "cuadrante" in g:
            # Matriz B: actividad vs gravedad estructural
            origen_pa = g.get("origen_pa", "estimado")
            pa_info = g.get("pa_info")

            # Líneas de actor visible vs determinante
            actor_vis = g.get("actor_visible")
            actor_vis_n = g.get("actor_visible_menciones", 0)
            actor_vis_base = g.get("actor_visible_en_base", False)
            actor_det = g.get("actor_determinante")
            hay_brecha = g.get("hay_brecha", False)

            if actor_vis:
                sin_base_tag = "" if actor_vis_base else " [sin actor en base]"
                linea_visible = f"ACTOR VISIBLE: {actor_vis} ({actor_vis_n} mencs){sin_base_tag}"
            else:
                linea_visible = "ACTOR VISIBLE: sin datos de entidades"

            if actor_det:
                peso_det = pa_info["peso_mayor"] if pa_info else "?"
                linea_det = f"ACTOR DETERMINANTE: {actor_det} (peso {peso_det:g})"
            else:
                linea_det = "ACTOR DETERMINANTE: sin actores vinculados"

            brecha_tag = " | ⚠ percepción ≠ poder real" if hay_brecha else ""

            if pa_info:
                pa_str = f"PA={pa_info['pa']:g} (bonus +{pa_info['bonus']:g})"
            else:
                pa_str = f"PA={g.get('impacto_base', 0):g} (estimado)"

            # Línea de urgencia/velocidad
            _vel = g.get("velocidad", 0)
            _urg = g.get("urgencia", "—")
            _vel_signo = f"+{_vel:g}" if _vel > 0 else f"{_vel:g}"
            linea_urg = f"URGENCIA: {_urg} · velocidad 7d {_vel_signo} pts"

            tooltip_extra = (
                f"{linea_urg} | "
                f"{linea_visible} | {linea_det}{brecha_tag} | "
                f"Cuadrante: {g['cuadrante']} | "
                f"PA [{origen_pa}]: {pa_str} | "
                f"Menciones: {g.get('menciones', 0)} | "
                f"Piso: {g.get('piso', 0)}"
            )
        else:
            tooltip_extra = f"Menciones: {g.get('menciones', 0)}"
            if "pct_volumen" in g:
                tooltip_extra += f" ({g['pct_volumen']}% vol)"

        nombre_display = g["tema"].replace("_", " ").title()
        # Matriz B (tiene 'cuadrante'): más transparente y borde fino para que
        # globos superpuestos se vean ambos. Matriz A conserva su estilo.
        es_matriz_b = "cuadrante" in g
        alpha_hex = "B3" if es_matriz_b else "CC"   # B3≈0.70 · CC≈0.80
        borde_px = 1.5 if es_matriz_b else 2
        datasets.append({
            "label": nombre_display,
            "data": [{"x": g["x"], "y": g["y"], "r": g["r"]}],
            "backgroundColor": g["color"] + alpha_hex,
            "borderColor": g["color"],
            "borderWidth": borde_px,
            "_tooltip_extra": tooltip_extra,
            "_score": g["score"],
            "_nivel": g["nivel"],
        })

    datasets_json = json.dumps(datasets, ensure_ascii=False)
    etiquetas_json = json.dumps(etiquetas, ensure_ascii=False)

    return f"""
<div style="position:relative;height:{altura}px;">
  <canvas id="{canvas_id}"></canvas>
</div>
<script>
(function() {{
  var raw = {datasets_json};
  var etq = {etiquetas_json};
  var umbralX = {umbral_x}, umbralY = {umbral_y};
  var drawQ = {{
    id: 'quadrants_{canvas_id}',
    afterDraw: function(chart) {{
      var ctx = chart.ctx;
      var ca = chart.chartArea;
      var x = chart.scales.x, y = chart.scales.y;
      ctx.save();
      ctx.strokeStyle = '#334155'; ctx.setLineDash([4,4]); ctx.lineWidth = 1;
      var xm = x.getPixelForValue(umbralX), ym = y.getPixelForValue(umbralY);
      ctx.beginPath(); ctx.moveTo(xm, ca.top); ctx.lineTo(xm, ca.bottom); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(ca.left, ym); ctx.lineTo(ca.right, ym); ctx.stroke();
      ctx.restore();
      // Draw quadrant labels — each anchored inside its quadrant,
      // with a semi-transparent pill background for legibility.
      // Lines/margins keep them away from the divider and from each other.
      // [text, hAlign:'left'|'right', vPos:'top'|'bottom']
      var qlabels = [
        [etq.ti, 'left',  'top'],
        [etq.td, 'right', 'top'],
        [etq.bi, 'left',  'bottom'],
        [etq.bd, 'right', 'bottom'],
      ];
      var PAD = 5, MARG = 8;
      ctx.font = 'bold 10px sans-serif';
      qlabels.forEach(function(l) {{
        var text = l[0], ha = l[1], va = l[2];
        var isLeft = (ha == 'left');
        var isTop  = (va == 'top');
        var tx = isLeft ? (ca.left + MARG) : (ca.right - MARG);
        var ty = isTop  ? (ca.top + 16)    : (ca.bottom - 8);
        ctx.textAlign = ha;
        ctx.textBaseline = isTop ? 'top' : 'bottom';
        var tw = ctx.measureText(text).width;
        var bx = isLeft ? (tx - PAD) : (tx - tw - PAD);
        var by = isTop  ? (ty - PAD) : (ty - 12);
        ctx.fillStyle = 'rgba(11,18,32,0.75)';
        ctx.fillRect(bx, by, tw + PAD * 2, 14 + PAD);
        ctx.fillStyle = '#94a3b8';
        ctx.fillText(text, tx, ty);
      }});
      ctx.textBaseline = 'alphabetic';
    }}
  }};
  if (window.Chart) {{
    window.Chart.register(drawQ);
    var ctx = document.getElementById('{canvas_id}');
    if (ctx) new window.Chart(ctx.getContext('2d'), {{
      type: 'bubble',
      data: {{
        datasets: raw.map(function(d) {{
          return {{
            label: d.label,
            data: d.data,
            backgroundColor: d.backgroundColor,
            borderColor: d.borderColor,
            borderWidth: d.borderWidth,
            _extra: d._tooltip_extra,
            _score: d._score,
            _nivel: d._nivel,
          }};
        }})
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              title: function(items) {{
                return items[0].dataset.label + ' — ' + items[0].dataset._nivel +
                       ' (score ' + items[0].dataset._score + ')';
              }},
              label: function(item) {{
                return [
                  '{eje_x_corto}: ' + item.raw.x + '  ·  {eje_y_corto}: ' + item.raw.y,
                  item.dataset._extra
                ];
              }}
            }}
          }}
        }},
        scales: {{
          x: {{ min: 0, max: {x_max},
               title: {{ display: true, text: '{titulo_x}', color: '#94a3b8',
                         font: {{ size: 10, weight: '600' }} }},
               grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
          y: {{ min: 0, max: 100,
               title: {{ display: true, text: '{titulo_y}', color: '#94a3b8',
                         font: {{ size: 10, weight: '600' }} }},
               grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
        }}
      }}
    }});
  }}
}})();
</script>"""


def _score_chip(score: float, nivel: str) -> str:
    """Badge grande con color de nivel para el Score Global."""
    colores = {
        "CRÍTICO": ("#3b1212", "#ef4444"),
        "ALTO":    ("#3b1e00", "#f97316"),
        "MEDIO":   ("#3b2600", "#fbbf24"),
        "BAJO":    ("#0d2b1f", "#22c55e"),
    }
    bg, fg = colores.get(nivel, ("#1f2a44", "#94a3b8"))
    return (f'<div style="background:{bg};color:{fg};border:1px solid {fg}44;'
            f'border-radius:10px;padding:10px 20px;text-align:center;min-width:130px">'
            f'<div style="font-size:32px;font-weight:700">{score}</div>'
            f'<div style="font-size:11px;font-weight:600;letter-spacing:.5px">{escape(nivel)}</div>'
            f'</div>')


@router.get("/semaforo")
async def admin_semaforo(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

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
  El motor OSINT corre en cada ciclo del scheduler.
  Espera el próximo ciclo o ejecuta una corrida manual.</p>
</div>"""
        return HTMLResponse(_page("Semáforo OSINT", contenido, "semaforo",
                                  sesion["username"]))

    # ── Construir datos para ambas matrices ──────────────────────────────────
    db_path = str(Path(OUTPUT_DIR) / "apurisk_archive.db")
    md = _construir_datos_semaforo(osint, db_path)
    globos_a     = md["globos_a"]
    globos_b     = md["globos_b"]
    score_a      = md["score_global_a"]
    score_b      = md["score_global_b"]
    nivel_a      = md["nivel_a"]
    nivel_b      = md["nivel_b"]
    formula_b    = md["formula_score_b"]
    fact_glob    = md["factores_globales"]
    umbral_x     = md["umbral_x"]
    umbral_y     = md["umbral_y"]
    x_max_viz    = md["x_max_viz"]
    cuadrantes_b      = md["cuadrantes_b"]
    pa_por_tema       = md["pa_por_tema"]
    usando_7d         = md["usando_7d"]
    total_menc_7d     = md["total_menciones_7d"]
    hay_velocidad     = md["hay_velocidad"]
    vel_urgente_p     = md["vel_urgente"]
    vel_prioritario_p = md["vel_prioritario"]

    # Leyenda de URGENCIA para la Matriz B (color = velocidad, no gravedad)
    _leyenda_urgencia = [
        ("#dc2626", f"Urgente (grave, vel ≥ {vel_urgente_p:g})"),
        ("#f59e0b", f"Prioritario (grave, vel ≥ {vel_prioritario_p:g})"),
        ("#94a3b8", "Importante (grave, sin movimiento)"),
        ("#475569", "No grave"),
    ]
    leyenda_urgencia_html = " ".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:12px">'
        f'<span style="width:12px;height:12px;border-radius:50%;background:{c};display:inline-block"></span>'
        f'<span style="font-size:11px;color:#94a3b8">{n}</span></span>'
        for c, n in _leyenda_urgencia
    )

    # ── Metadatos generales ──────────────────────────────────────────────────
    sem = osint.get("semaforo", {})
    vol = osint.get("volumen", {})
    puntos_lista = osint.get("puntos", [])
    puntos = {p["punto"]: p for p in puntos_lista}
    procesado = osint.get("procesado_en", "—")[:19].replace("T", " ")
    sin_dato  = osint.get("factores_sin_dato", [])

    act_disparado = sem.get("activador_disparado", False)
    act_tipo      = sem.get("activador_tipo") or "—"
    act_html = (
        f'<span class="badge badge-alto">⚡ DISPARADO — tipo {escape(act_tipo)}</span>'
        if act_disparado else '<span class="badge badge-ok">ninguno</span>'
    )

    # ── Matrices HTML ────────────────────────────────────────────────────────
    matriz_a_html = _matriz_bubble_html(
        "matrizVolumenChart", globos_a,
        "FRECUENCIA (% del volumen) →", "IMPACTO POLÍTICO →",
        eje_x_corto="Frecuencia", eje_y_corto="Impacto",
        x_max=x_max_viz,
    )
    matriz_b_html = _matriz_bubble_html(
        "matrizEstructuralChart", globos_b,
        "ACTIVIDAD ACTUAL (% del volumen) →", "GRAVEDAD ESTRUCTURAL →",
        umbral_x=umbral_x, umbral_y=umbral_y,
        etiquetas={
            "ti": cuadrantes_b["ti"],   # GRAVE PERO SILENCIOSO
            "td": cuadrantes_b["td"],   # GRAVE Y ACTIVO
            "bi": cuadrantes_b["bi"],   # TRANQUILO
            "bd": cuadrantes_b["bd"],   # RUIDOSO PERO MENOR
        },
        eje_x_corto="Actividad", eje_y_corto="Gravedad",
        x_max=x_max_viz,
    )

    # ── Leyenda de colores de burbujas ───────────────────────────────────────
    leyenda_html = " ".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:12px">'
        f'<span style="width:12px;height:12px;border-radius:50%;background:{c};display:inline-block"></span>'
        f'<span style="font-size:11px;color:#94a3b8">{n}</span></span>'
        for n, c in _COLOR_NIVEL_BURB.items()
    )

    # ── Tabla de factores globales del semáforo ──────────────────────────────
    filas_fact = ""
    for fk, fd in fact_glob.items():
        bw = int(fd["valor"] * 100)
        filas_fact += (
            f"<tr><td><b>{escape(fk)}</b></td>"
            f"<td>{fd['valor']:.3f}</td>"
            f"<td>{fd['peso']:.1f}</td>"
            f"<td>{_origen_badge(fd['origen'])}</td>"
            f"<td><div style='background:#1e293b;border-radius:3px;height:8px;width:100px'>"
            f"<div style='background:#38bdf8;width:{bw}px;height:8px;border-radius:3px'>"
            f"</div></div></td></tr>"
        )

    # ── Activadores detectados ───────────────────────────────────────────────
    p5 = puntos.get(5, {})
    acts_det = p5.get("detalle", {}).get("activadores_detectados", [])
    filas_act = ""
    for a in acts_det:
        tipo_cls = "badge-alto" if a.get("tipo") == "absoluto" else "badge-warn"
        solape = a.get("solapamiento", 0)
        filas_act += (
            f"<tr><td>{escape(a.get('descripcion',''))}</td>"
            f"<td><span class='badge {tipo_cls}'>{escape(a.get('tipo',''))}</span></td>"
            f"<td>{int(solape*100)}%</td></tr>"
        )
    if not filas_act:
        filas_act = "<tr><td colspan='3' style='color:var(--muted)'>Sin activadores detectados</td></tr>"

    # ── Puntos del reporte (detalle legible) ─────────────────────────────────
    def _render_punto(p: dict) -> str:
        capa = p.get("capa", "señal")
        es_interp = capa == "interpretativa"
        borde = "#f59e0b" if es_interp else "#38bdf8"
        resultado = p.get("resultado")
        adv = p.get("advertencia", "")

        if isinstance(resultado, list):
            items = "".join(f"<li style='margin:3px 0'>{escape(str(r))}</li>" for r in resultado)
            res_html = f"<ul style='margin:6px 0 0 18px'>{items}</ul>"
        elif isinstance(resultado, dict):
            # Renderizar dict como tabla de 2 columnas, no como JSON crudo
            filas = "".join(
                f"<tr><td style='color:var(--muted);padding:3px 8px;white-space:nowrap'>"
                f"{escape(str(k))}</td>"
                f"<td style='padding:3px 8px'>{escape(str(v))}</td></tr>"
                for k, v in resultado.items()
            )
            res_html = f"<table style='font-size:12px;border-collapse:collapse'>{filas}</table>"
        else:
            badge_cls = "badge-alto" if es_interp else "badge-ok"
            res_html = f"<span class='badge {badge_cls}' style='font-size:13px;padding:4px 12px'>{escape(str(resultado))}</span>"

        adv_html = (
            f'<div style="color:#fbbf24;font-size:11px;margin-top:6px">'
            f'⚠ {escape(adv)}</div>'
        ) if adv else ""

        header_bg = "background:rgba(245,158,11,0.08)" if es_interp else ""
        return f"""
<div style="border-left:3px solid {borde};border-radius:0 8px 8px 0;
     padding:10px 14px;margin-bottom:10px;background:var(--bg-2)">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;{header_bg}">
    <span style="color:var(--muted);font-size:11px;min-width:20px">#{p.get('punto','?')}</span>
    <b style="font-size:13px">{escape(p.get('titulo',''))}</b>
    <span class="badge {'badge-warn' if es_interp else 'badge-ok'}">{escape(capa)}</span>
  </div>
  {res_html}
  {adv_html}
</div>"""

    # Puntos 1-7: señal | Puntos 8-10: interpretativa
    puntos_señal = [_render_punto(p) for p in puntos_lista if p.get("capa") == "señal"]
    puntos_interp = [_render_punto(p) for p in puntos_lista if p.get("capa") == "interpretativa"]

    # ── Tabla PA por tema + actores visibles ────────────────────────────────
    filas_pa = ""
    entidades_sin_match: list[tuple[str, str, int]] = []  # (tema, entidad, menciones)
    for tema, impacto_base in _IMPACTO_TEMA.items():
        nombre_tema = tema.replace("_", " ").title()
        g_b = next((g for g in globos_b if g["tema"] == tema), None)
        pa_info = pa_por_tema.get(tema)

        # Actor visible
        actor_vis = g_b.get("actor_visible") if g_b else None
        actor_vis_n = g_b.get("actor_visible_menciones", 0) if g_b else 0
        actor_vis_base = g_b.get("actor_visible_en_base", False) if g_b else False
        actor_det = g_b.get("actor_determinante") if g_b else None
        hay_brecha = g_b.get("hay_brecha", False) if g_b else False

        if actor_vis:
            if actor_vis_base:
                vis_html = f'<span style="color:#94a3b8">{escape(actor_vis)} ({actor_vis_n})</span>'
            else:
                vis_html = (
                    f'<span style="color:#f59e0b" title="Sin actor en base — agrégalo">'
                    f'⚠ {escape(actor_vis)} ({actor_vis_n})</span>'
                )
                entidades_sin_match.append((nombre_tema, actor_vis, actor_vis_n))
        else:
            vis_html = '<span style="color:var(--muted)">sin datos</span>'

        if hay_brecha:
            brecha_html = '<span style="color:#f59e0b;font-size:11px">⚠ percepción ≠ poder real</span>'
        else:
            brecha_html = ""

        if pa_info:
            pa_val = pa_info["pa"]
            actor_princ = escape(pa_info["actor_principal"])
            fuertes = pa_info["actores_fuertes"]
            bonus = pa_info["bonus"]
            n_actores = pa_info["n_actores"]
            lista_fuertes = ", ".join(
                f'{escape(a["nombre"])} ({a["peso"]:g})'
                for a in fuertes
            ) or "ninguno"
            badge_origen = '<span class="badge badge-ok">real</span>'
            detalle_html = (
                f'Principal: <b>{actor_princ}</b> ({pa_info["peso_mayor"]:g}) · '
                f'Fuertes (≥70): {lista_fuertes} · '
                f'Bonus: +{bonus:g}'
            )
        else:
            pa_val = float(impacto_base)
            badge_origen = '<span class="badge badge-alto">estimado</span>'
            n_actores = 0
            detalle_html = (
                '<span style="color:#f59e0b">⚠ Sin actores vinculados — '
                'usando impacto base. Vincula actores en <a href="/admin/actores" '
                'style="color:var(--accent)">/admin/actores</a>.</span>'
            )
        delta = round(pa_val - float(impacto_base), 1)
        delta_html = (
            f'<span style="color:#4ade80">+{delta:g}</span>' if delta > 0
            else (f'<span style="color:#f87171">{delta:g}</span>' if delta < 0
                  else '<span style="color:var(--muted)">—</span>')
        )

        # Velocidad 7d + urgencia (color del globo)
        velocidad = g_b.get("velocidad", 0) if g_b else 0
        urgencia = g_b.get("urgencia", "—") if g_b else "—"
        _urg_color = {
            "URGENTE": "#dc2626", "PRIORITARIO": "#f59e0b",
            "IMPORTANTE": "#94a3b8", "no_grave": "#475569",
        }.get(urgencia, "#64748b")
        _urg_label = "No grave" if urgencia == "no_grave" else urgencia
        _vel_signo = f"+{velocidad:g}" if velocidad > 0 else f"{velocidad:g}"
        urgencia_html = (
            f'<span style="display:inline-flex;align-items:center;gap:5px">'
            f'<span style="width:9px;height:9px;border-radius:50%;background:{_urg_color};'
            f'display:inline-block"></span>'
            f'<span style="color:{_urg_color};font-weight:600">{escape(_urg_label)}</span></span>'
            f'<br><span style="font-size:10px;color:var(--muted)">vel {_vel_signo} pts/7d</span>'
        )

        filas_pa += (
            f"<tr>"
            f"<td><b>{escape(nombre_tema)}</b></td>"
            f"<td style='text-align:center'>{float(impacto_base):g}</td>"
            f"<td style='text-align:center'><b>{pa_val:g}</b></td>"
            f"<td style='text-align:center'>{delta_html}</td>"
            f"<td>{urgencia_html}</td>"
            f"<td>{vis_html}<br>{brecha_html}</td>"
            f"<td style='text-align:center'>{n_actores}</td>"
            f"<td style='text-align:center'>{badge_origen}</td>"
            f"<td style='font-size:11px;color:var(--muted)'>{detalle_html}</td>"
            f"</tr>"
        )

    # ── Lista de entidades sin actor en base (señal de actores a cargar) ────
    if entidades_sin_match:
        items_sin_match = "".join(
            f"<li><b>{escape(ent)}</b> ({n} mencs) — visto en: {escape(t)}</li>"
            for t, ent, n in sorted(entidades_sin_match, key=lambda x: -x[2])
        )
        alerta_sin_match = f"""
<div class="card" style="border-left:4px solid #f59e0b">
  <div class="card-title" style="color:#f59e0b">⚠ Entidades visibles sin actor en la base</div>
  <p style="color:var(--muted);font-size:12px;margin:0 0 8px 0">
    Estas entidades aparecen en las noticias como las más mencionadas de su tema,
    pero no tienen actor correspondiente en tu base. Agrégalas en
    <a href="/admin/actores/nuevo" style="color:var(--accent)">→ Nuevo Actor</a>
    y vincula los alias (ej. "PNP, Policía") para que el emparejamiento funcione.
  </p>
  <ul style="margin:0 0 0 18px;font-size:13px;line-height:2">{items_sin_match}</ul>
</div>"""
    else:
        alerta_sin_match = ""

    # ── Chart.js CDN (solo si no está ya en la página) ───────────────────────
    chartjs_cdn = (
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>'
    )

    contenido = f"""
{chartjs_cdn}

<!-- ── MATRICES P×I LADO A LADO ── -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px">

  <!-- MATRIZ A — VOLUMEN -->
  <div class="card">
    <div class="card-title">Matriz A — Frecuencia × Impacto</div>
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:14px">
      {_score_chip(score_a, nivel_a)}
      <div style="font-size:12px;color:var(--muted);line-height:1.6">
        Score = Σ(score_tema × % volumen)<br>
        X = {'% del volumen 7 días (rolling)' if usando_7d else '% del volumen del ciclo'} · Y = impacto político<br>
        Radio = menciones {'7D' if usando_7d else 'del ciclo'} · {'<b style="color:#4ade80">ventana 7D activa</b> · ' + str(total_menc_7d) + ' artículos' if usando_7d else '<span style="color:#f59e0b">ventana 7D no disponible — usando ciclo actual</span>'}
      </div>
    </div>
    {matriz_a_html}
    <div style="margin-top:10px">{leyenda_html}</div>
  </div>

  <!-- MATRIZ B — ACTIVIDAD × GRAVEDAD ESTRUCTURAL · color = URGENCIA -->
  <div class="card">
    <div class="card-title">Matriz B — Urgencia (color) × Gravedad (posición)</div>
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:14px">
      {_score_chip(score_b, nivel_b)}
      <div style="font-size:12px;color:var(--muted);line-height:1.6">
        Score B = temperatura del momento (sube con temas escalando)<br>
        X = {'% del volumen 7 días (rolling)' if usando_7d else '% del volumen del ciclo'} · Y = max(piso, PA_tema)<br>
        <b>Color = urgencia</b> (velocidad 7d) · posición = actividad × gravedad<br>
        Cuadrantes: umbral X={umbral_x:g} · Y={umbral_y:g} · eje X 0→{x_max_viz:g}
        {'· <span style="color:#f59e0b">velocidad no disponible aún (falta ventana previa 7-14d)</span>' if not hay_velocidad else ''}
      </div>
    </div>
    {matriz_b_html}
    <div style="margin-top:10px">{leyenda_urgencia_html}</div>
    <p style="color:var(--muted);font-size:11px;margin-top:8px;line-height:1.5">
      <b>La gravedad ya no separa (casi todo es grave): lo que separa es el MOVIMIENTO.</b>
      El color codifica <b>urgencia</b> = velocidad de cambio en 7 días. Un tema grave
      <b>escalando fuerte</b> es urgente (rojo); grave pero quieto es importante (gris).
      La posición sigue mostrando la gravedad estructural (eje Y) y la actividad (eje X).
      El Score Global mide la <b>temperatura del momento</b>: sube cuando hay frentes
      escalando, baja cuando todo está grave pero quieto.
    </p>
  </div>

</div>

<!-- Aviso responsive para pantallas pequeñas -->
<style>
@media (max-width:900px) {{
  .mat-grid {{ grid-template-columns: 1fr !important; }}
}}
</style>

<!-- ── FACTORES GLOBALES DEL SEMÁFORO ── -->
<div class="card">
  <div class="card-title">Factores del semáforo (globales — nivel general del corpus)</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr><th>Factor</th><th>Valor</th><th>Peso (exp)</th>
             <th>Origen</th><th>Visual 0→1</th></tr></thead>
      <tbody>{filas_fact}</tbody>
    </table>
  </div>
  <p style="color:var(--muted);font-size:11px;margin-top:8px">
    Estos 5 factores (VC·PA·CE·IA·V) se calculan globales para todo el corpus, no por
    tema. Definen el nivel general del semáforo, no los ejes de la Matriz B (cuyos ejes
    son actividad por tema y gravedad estructural).
    <b>real</b> = dato computado · <b>proxy</b> = señal indirecta ·
    <b>estimado</b> = heurística no verificada.<br>
    Sin dato: <span style="color:var(--muted)">{escape(', '.join(sin_dato)) or 'ninguno'}</span>
    (requieren señales sociales/Twitter — pausadas).
  </p>
</div>

<!-- ── VOLUMEN Y ACTIVADORES ── -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px">
  <div class="card">
    <div class="card-title">Volumen del corpus</div>
    <table class="tbl">
      <tr><th>Clase</th><td><b>{escape(vol.get('clase','?'))}</b></td></tr>
      <tr><th>Artículos brutos</th><td>{vol.get('n_bruto',0)}</td></tr>
      <tr><th>Fuentes distintas</th><td>{vol.get('n_fuentes',0)}</td></tr>
      <tr><th>Índice duplicación</th><td>{vol.get('jaccard_dup',0):.2f}</td></tr>
      <tr><th>Procesado</th><td style="font-size:11px">{escape(procesado)}</td></tr>
    </table>
  </div>
  <div class="card">
    <div class="card-title">Activadores de ROJO</div>
    <div style="margin-bottom:10px">{act_html}</div>
    <table class="tbl">
      <thead><tr><th>Activador</th><th>Tipo</th><th>Solapamiento</th></tr></thead>
      <tbody>{filas_act}</tbody>
    </table>
    <p style="color:var(--muted);font-size:11px;margin-top:8px">
      absoluto → fuerza ROJO · condicional → la fórmula modula
    </p>
  </div>
</div>

<!-- ── REPORTE DE 10 PUNTOS ── -->
<div class="card">
  <div class="card-title">Señales computadas (puntos 1–7)</div>
  {''.join(puntos_señal)}
</div>

<div class="card" style="border-left:4px solid #f59e0b">
  <div class="card-title" style="color:#f59e0b">⚠ Capa interpretativa (puntos 8–10)</div>
  <div class="alert-box alert-warn" style="margin-bottom:12px;font-size:12px">
    Los puntos 8-10 son estimaciones del motor, no señales verificadas.
    Requieren validación del analista antes de actuar.
  </div>
  {''.join(puntos_interp)}
</div>

{alerta_sin_match}

<!-- ── PA POR TEMA — tabla de actores que alimentan el eje Y de Matriz B ── -->
<div class="card">
  <div class="card-title">🎭 PA por Tema — actores que alimentan la Matriz B</div>
  <p style="color:var(--muted);font-size:12px;margin:0 0 10px 0">
    El eje Y de la Matriz B usa el PA calculado desde actores reales cuando están
    vinculados. <b>real</b> = calculado desde actores · <b>estimado</b> = impacto
    base sin actores.<br>
    <b>Actor visible</b> = entidad más mencionada en noticias del tema ·
    <b>⚠ percepción ≠ poder real</b> = la prensa nombra un actor distinto al más poderoso ·
    <b>⚠ naranja</b> en visible = entidad sin actor en tu base (agrégala en
    <a href="/admin/actores" style="color:var(--accent)">→ Actores</a>).
  </p>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead>
        <tr>
          <th>Tema</th>
          <th style="text-align:center">Impacto base</th>
          <th style="text-align:center">PA actores</th>
          <th style="text-align:center">Δ</th>
          <th>Urgencia (vel 7d)</th>
          <th>Actor visible (prensa)</th>
          <th style="text-align:center">N° actores</th>
          <th style="text-align:center">Origen PA</th>
          <th>Detalle determinante</th>
        </tr>
      </thead>
      <tbody>{filas_pa}</tbody>
    </table>
  </div>
</div>

<div class="card" style="border-left:4px solid var(--accent)">
  <div class="card-title">🎛️ Calibración de la Matriz B</div>
  <p style="color:var(--muted);font-size:13px;margin:0 0 10px 0">
    Edita los pisos estructurales por tema y los parámetros del Score Global B
    (umbrales, coeficientes, escala visual del eje X) sin necesidad de redeploy.
    Los cambios se reflejan en la próxima carga de esta página.
  </p>
  <a href="/admin/semaforo/calibrar"
     style="display:inline-block;background:var(--accent);color:#000;
            border-radius:6px;padding:8px 20px;font-weight:600;font-size:13px">
    Ir a Calibración →
  </a>
</div>"""

    return HTMLResponse(_page("Semáforo OSINT", contenido, "semaforo",
                              sesion["username"]))


# ──────────────────────────────────────────────────────────────────────────────
# Calibración del semáforo — pisos estructurales + parámetros Score_B
# ──────────────────────────────────────────────────────────────────────────────

def _fila_piso(tema: str, impacto_base: int, piso_actual: float,
               actualizado_en: str, msg_tema: str = "") -> str:
    """Fila editable de un tema en la tabla de pisos estructurales."""
    y_efectivo = max(piso_actual, float(impacto_base))
    nombre = tema.replace("_", " ").title()
    flag_piso = (f'<span class="badge badge-warn" title="Piso activo: {piso_actual}">'
                 f'▲ piso {piso_actual:g}</span>'
                 if piso_actual > impacto_base else
                 '<span class="badge badge-off">base</span>')
    ts = actualizado_en[:16].replace("T", " ") if actualizado_en else "—"
    ok_html = (f'<span class="badge badge-ok">✓ {escape(msg_tema)}</span>'
               if msg_tema else "")
    return f"""<tr>
  <td><b>{escape(nombre)}</b></td>
  <td style="text-align:center">{impacto_base}</td>
  <td style="text-align:center">
    <form method="post" action="/admin/semaforo/pisos" style="display:inline-flex;gap:6px;align-items:center">
      <input type="hidden" name="tema" value="{escape(tema)}">
      <input type="number" name="piso" value="{piso_actual:g}" min="0" max="100" step="1"
             style="width:70px;background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:4px 8px;font-size:13px">
      <input type="text" name="notas" placeholder="motivo (opcional)"
             style="width:160px;background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:4px 8px;font-size:12px">
      <button type="submit"
              style="background:var(--accent);color:#000;border:none;border-radius:4px;
                     padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer">
        Guardar
      </button>
    </form>
    {ok_html}
  </td>
  <td style="text-align:center">{flag_piso}</td>
  <td style="text-align:center;font-weight:700;color:var(--accent)">{y_efectivo:g}</td>
  <td style="color:var(--muted);font-size:11px">{ts}</td>
</tr>"""


def _campo_param(clave: str, valor: float, descripcion: str,
                 msg_clave: str = "") -> str:
    """Campo editable inline para un parámetro numérico del semáforo."""
    ok_html = (f'<span class="badge badge-ok" style="margin-left:6px">✓ {escape(msg_clave)}</span>'
               if msg_clave else "")
    return f"""<tr>
  <td style="font-family:monospace;color:var(--accent);white-space:nowrap">{escape(clave)}</td>
  <td style="color:var(--muted);font-size:12px">{escape(descripcion)}</td>
  <td>
    <form method="post" action="/admin/semaforo/params"
          style="display:inline-flex;gap:6px;align-items:center">
      <input type="hidden" name="clave" value="{escape(clave)}">
      <input type="number" name="valor" value="{valor:g}" step="0.5"
             style="width:80px;background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:4px 8px;font-size:13px">
      <button type="submit"
              style="background:var(--bg-3);color:var(--accent);border:1px solid var(--accent);
                     border-radius:4px;padding:4px 10px;font-size:12px;cursor:pointer">
        Guardar
      </button>
    </form>
    {ok_html}
  </td>
  <td style="font-weight:700;color:var(--text);text-align:right">{valor:g}</td>
</tr>"""


@router.get("/semaforo/calibrar", response_class=HTMLResponse)
async def admin_calibrar(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    from ..storage.config_loader import (
        cargar_pisos_estructurales, cargar_parametros_semaforo,
    )
    db = _get_db_path()
    pisos = cargar_pisos_estructurales(db, "PE")
    params = cargar_parametros_semaforo(db)

    # Cargar fecha de última actualización por tema
    fechas: dict[str, str] = {}
    try:
        with _db_conn() as c:
            rows = c.execute(
                "SELECT tema, actualizado_en FROM config_piso_estructural WHERE pais='PE'"
            ).fetchall()
            fechas = {r["tema"]: r["actualizado_en"] for r in rows}
    except Exception:
        pass

    # Mensajes de confirmación por campo (query params)
    msg_piso = request.query_params.get("piso_ok", "")   # "electoral"
    msg_param = request.query_params.get("param_ok", "")  # "SEMAFORO_UMBRAL_ACTIVIDAD_X"
    err_msg = request.query_params.get("err", "")

    # ── Tabla de pisos ──────────────────────────────────────────────────────
    filas_pisos = ""
    for tema, impacto_base in _IMPACTO_TEMA.items():
        piso_actual = pisos.get(tema, 0.0)
        msg_t = tema if msg_piso == tema else ""
        filas_pisos += _fila_piso(tema, impacto_base, piso_actual,
                                   fechas.get(tema, ""), msg_t)

    # ── Tabla de parámetros ─────────────────────────────────────────────────
    PARAMS_META = [
        ("SEMAFORO_UMBRAL_ACTIVIDAD_X", params["umbral_x"],
         "Umbral eje X (% vol.) que separa silencioso/activo en cuadrantes"),
        ("SEMAFORO_UMBRAL_GRAVEDAD_Y",  params["umbral_y"],
         "Umbral eje Y que separa menor/grave en cuadrantes"),
        ("SCORE_B_COEF_ACTIVIDAD",      params["coef_actividad"],
         "Score B: agravante máximo por actividad del tema más grave (puntos sobre Y_max)"),
        ("SCORE_B_COEF_SIMULTANEIDAD",  params["coef_simultaneidad"],
         "Score B: agravante por cada tema grave-y-activo adicional (puntos)"),
        ("SCORE_B_BONUS_MAX",           params["bonus_max"],
         "Score B: tope total del agravante sobre Y_max (puntos)"),
        ("SEMAFORO_X_MAX_VIZ",          params["x_max_viz"],
         "Escala visual eje X. 0 = dinámica (máximo real + margen). >0 = escala fija"),
    ]
    filas_params = ""
    for clave, valor, desc in PARAMS_META:
        msg_p = clave if msg_param == clave else ""
        filas_params += _campo_param(clave, valor, desc, msg_p)

    err_html = (
        f'<div class="alert-box alert-alto" style="margin-bottom:14px">'
        f'⚠ {escape(err_msg)}</div>'
    ) if err_msg else ""

    contenido = f"""
{err_html}

<div class="card">
  <div class="card-title">Pisos estructurales por tema
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      Y efectivo = max(piso, impacto base). Con piso=0, Y = impacto base.
    </span>
  </div>
  <p style="color:var(--muted);font-size:12px;margin:0 0 12px 0">
    El piso fija un mínimo de gravedad estructural independientemente de la
    actividad semanal. Úsalo cuando un tema adquiere peso estructural mayor
    que su impacto base histórico (ej. electoral en año de elecciones).
  </p>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Tema</th>
        <th style="text-align:center">Impacto base</th>
        <th>Nuevo piso + motivo</th>
        <th style="text-align:center">Estado</th>
        <th style="text-align:center">Y efectivo</th>
        <th>Última edición</th>
      </tr></thead>
      <tbody>{filas_pisos}</tbody>
    </table>
  </div>
  <p style="color:var(--muted);font-size:11px;margin-top:10px">
    Rango válido: 0–100. Piso = 0 significa sin piso definido; Y = impacto base.
    Los cambios se reflejan en la siguiente carga de la página del semáforo.
    <a href="/admin/semaforo/calibrar/log" style="color:var(--accent)">
      Ver historial de cambios →
    </a>
  </p>
</div>

<div class="card" style="margin-top:0">
  <div class="card-title">Parámetros del Score Global B y escala visual</div>
  <p style="color:var(--muted);font-size:12px;margin:0 0 12px 0">
    Score_B = min(100, Y_max + min(bonus_max,
    coef_actividad·(X_Ymax/100) + coef_simultaneidad·max(0, n_graves_activos−1))).
    El silencio nunca resta; Y_max fija el piso del score aunque el peor tema esté callado.
  </p>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Parámetro</th><th>Descripción</th><th>Editar</th>
        <th style="text-align:right">Valor actual</th>
      </tr></thead>
      <tbody>{filas_params}</tbody>
    </table>
  </div>
  <p style="color:var(--muted);font-size:11px;margin-top:10px">
    Los valores son provisionales — calibrar con criterio analítico tras
    observar la matriz con datos reales y pisos definidos.
    <a href="/admin/semaforo/calibrar/log" style="color:var(--accent)">
      Ver historial de cambios →
    </a>
  </p>
</div>

<div style="margin-top:10px">
  <a href="/admin/semaforo" style="color:var(--accent);font-size:13px">
    ← Volver al Semáforo OSINT
  </a>
</div>"""

    return HTMLResponse(_page("Calibración · Semáforo", contenido, "calibrar",
                              sesion["username"]))


@router.post("/semaforo/pisos")
async def admin_calibrar_piso(request: Request):
    """Guarda el piso estructural de un tema y redirige con confirmación."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import actualizar_piso_estructural, LockTimeoutError
    form = await request.form()
    try:
        tema = form.get("tema", "").strip()
        piso_raw = form.get("piso", "0").strip()
        notas = form.get("notas", "").strip() or None
        if tema not in _IMPACTO_TEMA:
            return RedirectResponse(
                f"/admin/semaforo/calibrar?err=Tema+inválido:+{escape(tema)}", status_code=303)
        piso = float(piso_raw)
        actualizar_piso_estructural(_get_db_path(), tema, piso,
                                    usuario=sesion["username"], notas=notas)
        return RedirectResponse(
            f"/admin/semaforo/calibrar?piso_ok={tema}", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            "/admin/semaforo/calibrar?err=BD+ocupada+(ciclo+automático).+"
            "El+cambio+NO+se+guardó.+Reintenta+en+unos+segundos.", status_code=303)
    except ValueError:
        return RedirectResponse(
            "/admin/semaforo/calibrar?err=Valor+de+piso+inválido+(debe+ser+número+0-100).",
            status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/semaforo/calibrar?err={escape(str(e))}", status_code=303)


@router.post("/semaforo/params")
async def admin_calibrar_param(request: Request):
    """Guarda un parámetro numérico del semáforo y redirige con confirmación."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import actualizar_parametro_semaforo, LockTimeoutError

    # Claves permitidas (whitelist explícita — nunca escritura arbitraria a config_parametros)
    _CLAVES_PERMITIDAS = {
        "SEMAFORO_UMBRAL_ACTIVIDAD_X", "SEMAFORO_UMBRAL_GRAVEDAD_Y",
        "SCORE_B_COEF_ACTIVIDAD", "SCORE_B_COEF_SIMULTANEIDAD",
        "SCORE_B_BONUS_MAX", "SEMAFORO_X_MAX_VIZ",
    }
    form = await request.form()
    try:
        clave = form.get("clave", "").strip()
        valor_raw = form.get("valor", "").strip()
        if clave not in _CLAVES_PERMITIDAS:
            return RedirectResponse(
                f"/admin/semaforo/calibrar?err=Parámetro+no+editable:+{escape(clave)}",
                status_code=303)
        valor = float(valor_raw)
        actualizar_parametro_semaforo(_get_db_path(), clave, valor,
                                      usuario=sesion["username"])
        return RedirectResponse(
            f"/admin/semaforo/calibrar?param_ok={clave}", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            "/admin/semaforo/calibrar?err=BD+ocupada+(ciclo+automático).+"
            "El+cambio+NO+se+guardó.+Reintenta+en+unos+segundos.", status_code=303)
    except ValueError:
        return RedirectResponse(
            "/admin/semaforo/calibrar?err=Valor+inválido+(debe+ser+número).",
            status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/semaforo/calibrar?err={escape(str(e))}", status_code=303)


@router.get("/semaforo/calibrar/log", response_class=HTMLResponse)
async def admin_calibrar_log(request: Request):
    """Historial de cambios de calibración (pisos, umbrales, coeficientes)."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import listar_log_semaforo
    logs = listar_log_semaforo(_get_db_path(), limite=200)

    filas = ""
    for l in logs:
        ts = (l.get("cambiado_en") or "")[:19].replace("T", " ")
        campo = l.get("campo") or "—"
        va = str(l.get("valor_anterior") if l.get("valor_anterior") is not None else "—")
        vn = str(l.get("valor_nuevo") if l.get("valor_nuevo") is not None else "—")
        # Pisos se llaman "piso:tema" — etiqueta más legible
        if campo.startswith("piso:"):
            campo_html = (f'<span class="badge badge-warn">piso</span> '
                          f'{escape(campo[5:].replace("_"," ").title())}')
        else:
            campo_html = f'<span class="badge badge-off">{escape(campo)}</span>'
        filas += f"""<tr>
  <td style="color:var(--muted);font-size:12px;white-space:nowrap">{ts}</td>
  <td>{campo_html}</td>
  <td style="color:var(--muted)">{escape(va)} → <b style="color:var(--text)">{escape(vn)}</b></td>
  <td style="color:var(--accent);font-size:12px">{escape(l.get('usuario') or '—')}</td>
  <td style="color:var(--muted);font-size:12px">{escape(l.get('motivo') or '—')}</td>
</tr>\n"""

    cuerpo = (filas if logs else
              '<tr><td colspan="5" style="color:var(--muted);padding:16px">'
              'Sin cambios registrados todavía.</td></tr>')

    contenido = f"""
<div class="alert-box alert-info">
  Registro de todos los cambios de calibración del semáforo (pisos, umbrales, coeficientes).
  <a href="/admin/semaforo/calibrar" style="color:var(--accent)">← Volver a calibración</a>
</div>
<div class="card">
  <div class="card-title">Historial de calibración — config_semaforo_log ({len(logs)})</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Fecha</th><th>Campo</th><th>Cambio</th><th>Usuario</th><th>Motivo</th>
      </tr></thead>
      <tbody>{cuerpo}</tbody>
    </table>
  </div>
</div>"""

    return HTMLResponse(_page("Log calibración · Semáforo", contenido, "calibrar",
                              sesion["username"]))


# ──────────────────────────────────────────────────────────────────────────────
# Actores — Capas 1 y 2 de la base de poder instalada
# ──────────────────────────────────────────────────────────────────────────────

# JS inline para recálculo en vivo del peso al editar criterios o nivel
_ACTOR_CALC_JS = """
<script>
(function() {
  var NIVELES = {I:95,II:85,III:72,IV:60,V:48,VI:36,VII:24,VIII:12};

  function recalc() {
    var form = document.getElementById('actor-form');
    if (!form) return;

    // nivel_base: si manual checked, usa el campo; si no, usa parámetro del nivel
    var nivel = form.querySelector('[name=nivel]').value;
    var manualCb = form.querySelector('[name=nivel_base_manual]');
    var nbInput  = form.querySelector('[name=nivel_base]');
    var nb;
    if (manualCb && manualCb.checked) {
      nb = parseFloat(nbInput.value) || NIVELES[nivel] || 60;
    } else {
      nb = NIVELES[nivel] || 60;
      if (nbInput) nbInput.value = nb;
    }

    function v(name) {
      var el = form.querySelector('[name=' + name + ']');
      return el ? (parseInt(el.value) || 3) : 3;
    }
    var d = v('crit_decision'), r = v('crit_recursos'), a = v('crit_articulacion');
    var l = v('crit_legitimidad'), rs = v('crit_resiliencia'), p = v('crit_proyeccion');
    var cap = ((d + r + a) * 2 + (l + rs + p)) / 45.0;
    var peso = nb * (0.5 + 0.5 * cap);

    var elCap  = document.getElementById('live-cap');
    var elPeso = document.getElementById('live-peso');
    var elBar  = document.getElementById('live-bar');
    if (elCap)  elCap.textContent  = cap.toFixed(3);
    if (elPeso) elPeso.textContent = peso.toFixed(1);
    if (elBar)  elBar.style.width  = Math.min(100, peso) + '%';

    // Color del peso
    var color = peso >= 72 ? '#ef4444' : peso >= 55 ? '#f97316' : peso >= 36 ? '#f59e0b' : '#22c55e';
    if (elPeso) elPeso.style.color = color;
    if (elBar)  elBar.style.background = color;
  }

  document.addEventListener('DOMContentLoaded', function() {
    var form = document.getElementById('actor-form');
    if (!form) return;
    form.addEventListener('change', recalc);
    form.addEventListener('input',  recalc);
    recalc();
  });
})();
</script>
"""


def _slider_criterio(name: str, label: str, val: int, doble: bool) -> str:
    """Input range 1-5 con etiqueta y valor visual, para los 6 criterios."""
    badge = ' <span style="font-size:10px;color:var(--accent)">×2</span>' if doble else ""
    return f"""
<div style="margin-bottom:10px">
  <div style="display:flex;justify-content:space-between;margin-bottom:3px">
    <span style="font-size:12px;color:var(--muted)">{escape(label)}{badge}</span>
    <span style="font-size:13px;font-weight:700;color:var(--text)" id="val-{name}">{val}</span>
  </div>
  <input type="range" name="{name}" min="1" max="5" value="{val}"
         style="width:100%;accent-color:var(--accent)"
         oninput="document.getElementById('val-{name}').textContent=this.value">
</div>"""


def _actor_form_html(actor: dict = None, niveles_base: dict = None,
                     temas_disponibles: list = None) -> str:
    """Bloque HTML del formulario de creación/edición de un actor."""
    if actor is None:
        actor = {}
    if niveles_base is None:
        niveles_base = {"I":95,"II":85,"III":72,"IV":60,"V":48,"VI":36,"VII":24,"VIII":12}
    if temas_disponibles is None:
        temas_disponibles = []

    # Importamos NIVELES_ACTOR y TIPOS_ACTOR aquí para no contaminar el módulo
    from ..storage.config_loader import NIVELES_ACTOR, TIPOS_ACTOR

    nivel_sel = actor.get("nivel", "IV")
    tipo_sel  = actor.get("tipo", "formal")
    nb_manual = bool(actor.get("nivel_base_manual", 0))

    opts_nivel = "".join(
        f'<option value="{k}"{" selected" if k == nivel_sel else ""}>'
        f'Nivel {k} — {nombre} ({niveles_base.get(k, v):.0f})</option>'
        for k, (nombre, v) in NIVELES_ACTOR.items()
    )
    opts_tipo = "".join(
        f'<option value="{t}"{" selected" if t == tipo_sel else ""}>{escape(t)}</option>'
        for t in TIPOS_ACTOR
    )

    temas_actor = set(actor.get("temas", []))
    checkboxes = "".join(
        f'<label style="display:inline-flex;align-items:center;gap:5px;'
        f'margin:3px 8px 3px 0;font-size:12px;cursor:pointer">'
        f'<input type="checkbox" name="temas" value="{t}"'
        f'{"  checked" if t in temas_actor else ""}> '
        f'{escape(t.replace("_"," ").title())}</label>'
        for t in temas_disponibles
    )

    sliders = (
        _slider_criterio("crit_decision",    "Decisión / Veto",      actor.get("crit_decision",    3), True) +
        _slider_criterio("crit_recursos",    "Control de recursos",  actor.get("crit_recursos",    3), True) +
        _slider_criterio("crit_articulacion","Articulación",         actor.get("crit_articulacion",3), True) +
        _slider_criterio("crit_legitimidad", "Legitimidad",          actor.get("crit_legitimidad", 3), False) +
        _slider_criterio("crit_resiliencia", "Resiliencia",          actor.get("crit_resiliencia", 3), False) +
        _slider_criterio("crit_proyeccion",  "Proyección externa",   actor.get("crit_proyeccion",  3), False)
    )

    nb_val = actor.get("nivel_base", niveles_base.get(nivel_sel, 60))
    peso_actual = actor.get("peso_calculado", 0)

    return f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">

  <!-- Columna izquierda: identidad -->
  <div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted)">Nombre del actor</label>
      <input type="text" name="nombre" value="{escape(actor.get('nombre',''))}" required
             style="width:100%;box-sizing:border-box;margin-top:3px;
                    background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:6px 10px;font-size:13px">
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted)">Tipo</label>
      <select name="tipo"
              style="width:100%;margin-top:3px;background:var(--bg-3);color:var(--text);
                     border:1px solid #334155;border-radius:4px;padding:6px 8px;font-size:13px">
        {opts_tipo}
      </select>
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted)">Nivel estratégico (Capa 1)</label>
      <select name="nivel" id="select-nivel"
              style="width:100%;margin-top:3px;background:var(--bg-3);color:var(--text);
                     border:1px solid #334155;border-radius:4px;padding:6px 8px;font-size:13px">
        {opts_nivel}
      </select>
    </div>
    <div style="margin-bottom:12px">
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)">
        <input type="checkbox" name="nivel_base_manual"
               {"checked" if nb_manual else ""}
               style="accent-color:var(--accent)">
        Ajuste manual del valor base (excluye de propagación automática)
      </label>
      <input type="number" name="nivel_base" value="{nb_val:g}" min="1" max="100" step="1"
             style="width:90px;margin-top:5px;background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:5px 8px;font-size:13px"
             {"" if nb_manual else "disabled"}>
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted)">Territorio</label>
      <input type="text" name="territorio" value="{escape(actor.get('territorio','nacional'))}"
             style="width:100%;box-sizing:border-box;margin-top:3px;
                    background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:6px 10px;font-size:13px">
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted)">Temas donde influye</label>
      <div style="margin-top:5px;padding:8px;background:var(--bg-3);border-radius:4px;
                  border:1px solid #334155">
        {checkboxes or '<span style="color:var(--muted);font-size:12px">Sin temas configurados</span>'}
      </div>
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted)">
        Alias en prensa
        <span style="font-weight:400;color:#64748b"> — variantes del nombre que aparecen en noticias</span>
      </label>
      <input type="text" name="alias"
             placeholder="ej: FFAA, Ejército, militares, Fuerzas Armadas"
             value="{escape(actor.get('alias') or '')}"
             style="width:100%;box-sizing:border-box;margin-top:3px;
                    background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:6px 10px;font-size:12px">
      <div style="font-size:11px;color:#64748b;margin-top:3px">
        Separa variantes por comas · se usan para emparejar con entidades detectadas en noticias
      </div>
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted)">Notas del analista</label>
      <textarea name="notas_analista" rows="3"
                style="width:100%;box-sizing:border-box;margin-top:3px;
                       background:var(--bg-3);color:var(--text);resize:vertical;
                       border:1px solid #334155;border-radius:4px;padding:6px 10px;font-size:12px"
      >{escape(actor.get('notas_analista') or '')}</textarea>
    </div>
  </div>

  <!-- Columna derecha: criterios Capa 2 + peso en vivo -->
  <div>
    <div style="margin-bottom:14px;padding:12px;background:var(--bg-3);border-radius:8px;
                border:1px solid #334155">
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px;font-weight:600">
        CAPA 2 — CAPACIDAD EFECTIVA
        <span style="font-weight:400"> · cap = [(D+R+A)×2 + (L+Rs+P)] / 45</span>
      </div>
      {sliders}
    </div>

    <!-- Peso en vivo -->
    <div style="padding:14px;background:var(--bg-1);border-radius:8px;
                border:2px solid #334155;text-align:center">
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px;font-weight:600">
        PESO DEL ACTOR (en vivo)
      </div>
      <div style="font-size:42px;font-weight:800;line-height:1" id="live-peso">
        {peso_actual:.1f}
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:4px">
        Capacidad efectiva: <b id="live-cap">{actor.get('capacidad_efectiva', 0):.3f}</b>
      </div>
      <div style="background:#1e293b;border-radius:4px;height:6px;margin-top:8px;overflow:hidden">
        <div id="live-bar"
             style="height:6px;border-radius:4px;width:{min(100, peso_actual):.0f}%;
                    background:#22c55e;transition:width .2s,background .2s">
        </div>
      </div>
      <div style="font-size:10px;color:var(--muted);margin-top:6px">
        nivel_base × (0.5 + 0.5 × capacidad)
      </div>
    </div>
  </div>
</div>
<script>
// Habilitar/deshabilitar campo nivel_base manual
(function() {{
  var cb = document.querySelector('[name=nivel_base_manual]');
  var inp = document.querySelector('[name=nivel_base]');
  if (cb && inp) {{
    cb.addEventListener('change', function() {{
      inp.disabled = !cb.checked;
      if (!cb.checked) {{
        var sel = document.querySelector('[name=nivel]');
        if (sel) {{
          var nivs = {{I:95,II:85,III:72,IV:60,V:48,VI:36,VII:24,VIII:12}};
          inp.value = nivs[sel.value] || 60;
        }}
      }}
    }});
  }}
}})();
</script>"""


@router.get("/actores", response_class=HTMLResponse)
async def admin_actores(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import listar_actores, NIVELES_ACTOR
    db = _get_db_path()
    actores = listar_actores(db, "PE")
    msg = request.query_params.get("msg", "")
    err_msg = request.query_params.get("err", "")

    filas = ""
    for a in actores:
        nivel_nombre = NIVELES_ACTOR.get(a["nivel"], ("—", 0))[0]
        temas_str = ", ".join(t.replace("_", " ").title() for t in a["temas"]) or "—"
        estado_cls = "badge-ok" if a["activo"] else "badge-off"
        peso = a["peso_calculado"]
        if peso >= 72:
            peso_color = "#ef4444"
        elif peso >= 55:
            peso_color = "#f97316"
        elif peso >= 36:
            peso_color = "#f59e0b"
        else:
            peso_color = "#22c55e"
        filas += f"""<tr>
  <td><a href="/admin/actores/{a['id']}" style="color:var(--accent);font-weight:600">
      {escape(a['nombre'])}</a></td>
  <td><span class="badge badge-off">{escape(a['tipo'])}</span></td>
  <td style="white-space:nowrap">
    <b>Nivel {escape(a['nivel'])}</b>
    <span style="color:var(--muted);font-size:11px"> {escape(nivel_nombre)}</span>
  </td>
  <td style="font-size:11px;color:var(--muted)">{escape(a.get('territorio',''))}</td>
  <td style="text-align:center">
    <b style="font-size:16px;color:{peso_color}">{peso:.1f}</b>
  </td>
  <td style="font-size:11px;color:var(--muted)">{escape(temas_str)}</td>
  <td><span class="badge {estado_cls}">{"activo" if a['activo'] else "inactivo"}</span></td>
  <td>
    <a href="/admin/actores/{a['id']}" style="color:var(--accent);font-size:12px">editar</a>
  </td>
</tr>\n"""

    if not filas:
        filas = '<tr><td colspan="8" style="color:var(--muted);padding:20px;text-align:center">Sin actores registrados. <a href="/admin/actores/nuevo" style="color:var(--accent)">Crear el primero →</a></td></tr>'

    msg_html = (f'<div class="alert-box alert-info" style="margin-bottom:12px">'
                f'✓ {escape(msg)}</div>') if msg else ""
    err_html = (f'<div class="alert-box alert-alto" style="margin-bottom:12px">'
                f'⚠ {escape(err_msg)}</div>') if err_msg else ""

    contenido = f"""
{msg_html}{err_html}
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
  <div style="font-size:13px;color:var(--muted)">
    {len(actores)} actor{'es' if len(actores)!=1 else ''} registrado{'s' if len(actores)!=1 else ''} · ordenados por peso
  </div>
  <div style="display:flex;gap:8px">
    <a href="/admin/actores/log"
       style="background:var(--bg-3);color:var(--muted);border:1px solid #334155;
              border-radius:6px;padding:6px 14px;font-size:12px">
      Historial →
    </a>
    <a href="/admin/actores/nuevo"
       style="background:var(--accent);color:#000;border-radius:6px;
              padding:7px 18px;font-size:13px;font-weight:600">
      + Nuevo actor
    </a>
  </div>
</div>
<div class="card">
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Nombre</th><th>Tipo</th><th>Nivel</th><th>Territorio</th>
        <th style="text-align:center">Peso</th><th>Temas</th><th>Estado</th><th></th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
</div>"""

    return HTMLResponse(_page("Actores", contenido, "actores", sesion["username"]))


@router.get("/actores/nuevo", response_class=HTMLResponse)
async def admin_actores_nuevo(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import cargar_niveles_base, NIVELES_ACTOR
    db = _get_db_path()
    niveles_base = cargar_niveles_base(db)
    temas = list(_IMPACTO_TEMA.keys())
    err_msg = request.query_params.get("err", "")
    err_html = (f'<div class="alert-box alert-alto" style="margin-bottom:12px">'
                f'⚠ {escape(err_msg)}</div>') if err_msg else ""

    form_html = _actor_form_html(None, niveles_base, temas)
    contenido = f"""
{_ACTOR_CALC_JS}
{err_html}
<div class="card">
  <div class="card-title">Nuevo actor</div>
  <form id="actor-form" method="post" action="/admin/actores/crear">
    {form_html}
    <div style="margin-top:16px;display:flex;gap:10px">
      <button type="submit"
              style="background:var(--accent);color:#000;border:none;border-radius:6px;
                     padding:9px 24px;font-size:14px;font-weight:700;cursor:pointer">
        Guardar actor
      </button>
      <a href="/admin/actores"
         style="color:var(--muted);padding:9px 16px;font-size:13px">Cancelar</a>
    </div>
  </form>
</div>"""

    return HTMLResponse(_page("Nuevo actor", contenido, "actores", sesion["username"]))


@router.post("/actores/crear")
async def admin_actores_crear(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import crear_actor, LockTimeoutError
    form = await request.form()
    try:
        nombre = form.get("nombre", "").strip()
        if not nombre:
            return RedirectResponse("/admin/actores/nuevo?err=El+nombre+es+obligatorio",
                                    status_code=303)
        temas = form.getlist("temas")
        nb_manual = bool(form.get("nivel_base_manual"))
        nivel = form.get("nivel", "IV")
        datos = {
            "nombre": nombre,
            "tipo": form.get("tipo", "formal"),
            "nivel": nivel,
            "nivel_base": float(form.get("nivel_base") or 60),
            "nivel_base_manual": nb_manual,
            "crit_decision":    int(form.get("crit_decision", 3)),
            "crit_recursos":    int(form.get("crit_recursos", 3)),
            "crit_articulacion":int(form.get("crit_articulacion", 3)),
            "crit_legitimidad": int(form.get("crit_legitimidad", 3)),
            "crit_resiliencia": int(form.get("crit_resiliencia", 3)),
            "crit_proyeccion":  int(form.get("crit_proyeccion", 3)),
            "territorio": form.get("territorio", "nacional"),
            "alias": form.get("alias", "").strip() or None,
            "notas_analista": form.get("notas_analista", ""),
            "temas": temas,
            "pais": "PE",
        }
        r = crear_actor(_get_db_path(), datos, usuario=sesion["username"])
        return RedirectResponse(
            f"/admin/actores/{r['id']}?msg=Actor+creado+correctamente", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            "/admin/actores/nuevo?err=BD+ocupada.+El+actor+NO+se+guardó.+Reintenta.",
            status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/actores/nuevo?err={escape(str(e))}", status_code=303)


@router.get("/actores/{actor_id}", response_class=HTMLResponse)
async def admin_actores_detalle(request: Request, actor_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import (
        obtener_actor, cargar_niveles_base, listar_log_actores, NIVELES_ACTOR,
    )
    db = _get_db_path()
    actor = obtener_actor(db, actor_id)
    if not actor:
        return RedirectResponse("/admin/actores?err=Actor+no+encontrado", status_code=303)

    niveles_base = cargar_niveles_base(db)
    temas = list(_IMPACTO_TEMA.keys())
    logs = listar_log_actores(db, actor_id, limite=30)

    msg = request.query_params.get("msg", "")
    err_msg = request.query_params.get("err", "")
    msg_html = (f'<div class="alert-box alert-info" style="margin-bottom:12px">'
                f'✓ {escape(msg)}</div>') if msg else ""
    err_html = (f'<div class="alert-box alert-alto" style="margin-bottom:12px">'
                f'⚠ {escape(err_msg)}</div>') if err_msg else ""

    form_html = _actor_form_html(actor, niveles_base, temas)

    filas_log = ""
    for l in logs:
        ts = (l.get("cambiado_en") or "")[:19].replace("T", " ")
        campo = l.get("campo") or "—"
        va = str(l.get("valor_anterior") or "—")
        vn = str(l.get("valor_nuevo") or "—")
        filas_log += f"""<tr>
  <td style="color:var(--muted);font-size:11px;white-space:nowrap">{ts}</td>
  <td><span class="badge badge-off">{escape(campo)}</span></td>
  <td style="color:var(--muted);font-size:12px">
    {escape(va)} → <b style="color:var(--text)">{escape(vn)}</b>
  </td>
  <td style="color:var(--accent);font-size:11px">{escape(l.get('usuario') or '—')}</td>
  <td style="color:var(--muted);font-size:11px">{escape(l.get('motivo') or '—')}</td>
</tr>\n"""

    if not filas_log:
        filas_log = '<tr><td colspan="5" style="color:var(--muted);padding:12px">Sin cambios registrados.</td></tr>'

    nivel_nombre = NIVELES_ACTOR.get(actor["nivel"], ("—",))[0]
    estado_badge = ('<span class="badge badge-ok">activo</span>'
                    if actor["activo"] else '<span class="badge badge-off">inactivo</span>')

    contenido = f"""
{_ACTOR_CALC_JS}
{msg_html}{err_html}
<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
  <div>
    <h2 style="margin:0;font-size:20px">{escape(actor['nombre'])}</h2>
    <div style="color:var(--muted);font-size:12px;margin-top:2px">
      Nivel {escape(actor['nivel'])} — {escape(nivel_nombre)} ·
      {escape(actor['tipo'])} · {escape(actor.get('territorio',''))}
      · {estado_badge}
    </div>
  </div>
  <div style="margin-left:auto;display:flex;gap:8px">
    <form method="post" action="/admin/actores/{actor_id}/toggle" style="display:inline">
      <button type="submit"
              style="background:var(--bg-3);color:var(--muted);border:1px solid #334155;
                     border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer">
        {'Desactivar' if actor['activo'] else 'Activar'}
      </button>
    </form>
    <a href="/admin/actores" style="color:var(--muted);font-size:12px;padding:6px 14px">
      ← Lista
    </a>
  </div>
</div>

<div class="card">
  <div class="card-title">Editar actor</div>
  <form id="actor-form" method="post" action="/admin/actores/{actor_id}/editar">
    <input type="hidden" name="actor_id" value="{actor_id}">
    {form_html}
    <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
      <button type="submit"
              style="background:var(--accent);color:#000;border:none;border-radius:6px;
                     padding:9px 24px;font-size:14px;font-weight:700;cursor:pointer">
        Guardar cambios
      </button>
      <input type="text" name="motivo" placeholder="motivo del cambio (opcional)"
             style="width:220px;background:var(--bg-3);color:var(--text);
                    border:1px solid #334155;border-radius:4px;padding:6px 10px;font-size:12px">
      <a href="/admin/actores" style="color:var(--muted);font-size:13px">Cancelar</a>
    </div>
  </form>
</div>

<div class="card" style="margin-top:0">
  <div class="card-title">Historial de cambios (últimos 30)</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Fecha</th><th>Campo</th><th>Cambio</th><th>Usuario</th><th>Motivo</th>
      </tr></thead>
      <tbody>{filas_log}</tbody>
    </table>
  </div>
</div>"""

    return HTMLResponse(_page(f"Actor · {actor['nombre']}", contenido, "actores",
                              sesion["username"]))


@router.post("/actores/{actor_id}/editar")
async def admin_actores_editar(request: Request, actor_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import actualizar_actor, LockTimeoutError
    form = await request.form()
    try:
        temas = form.getlist("temas")
        nb_manual = bool(form.get("nivel_base_manual"))
        datos = {
            "nombre":          form.get("nombre", "").strip(),
            "tipo":            form.get("tipo", "formal"),
            "nivel":           form.get("nivel", "IV"),
            "nivel_base":      float(form.get("nivel_base") or 60),
            "nivel_base_manual": nb_manual,
            "crit_decision":    int(form.get("crit_decision", 3)),
            "crit_recursos":    int(form.get("crit_recursos", 3)),
            "crit_articulacion":int(form.get("crit_articulacion", 3)),
            "crit_legitimidad": int(form.get("crit_legitimidad", 3)),
            "crit_resiliencia": int(form.get("crit_resiliencia", 3)),
            "crit_proyeccion":  int(form.get("crit_proyeccion", 3)),
            "territorio":  form.get("territorio", "nacional"),
            "alias": form.get("alias", "").strip() or None,
            "notas_analista": form.get("notas_analista", ""),
            "temas": temas,
        }
        motivo = form.get("motivo", "").strip() or None
        r = actualizar_actor(_get_db_path(), actor_id, datos,
                             usuario=sesion["username"], motivo=motivo)
        return RedirectResponse(
            f"/admin/actores/{actor_id}?msg=Guardado+·+peso={r['peso_nuevo']}",
            status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            f"/admin/actores/{actor_id}?err=BD+ocupada.+El+cambio+NO+se+guardó.+Reintenta.",
            status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/actores/{actor_id}?err={escape(str(e))}",
                                status_code=303)


@router.post("/actores/{actor_id}/toggle")
async def admin_actores_toggle(request: Request, actor_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import toggle_actor, LockTimeoutError
    try:
        r = toggle_actor(_get_db_path(), actor_id, usuario=sesion["username"])
        estado = "activado" if r["activo_nuevo"] else "desactivado"
        return RedirectResponse(f"/admin/actores/{actor_id}?msg=Actor+{estado}",
                                status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            f"/admin/actores/{actor_id}?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/actores/{actor_id}?err={escape(str(e))}",
                                status_code=303)


@router.get("/actores/log", response_class=HTMLResponse)
async def admin_actores_log(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import listar_log_actores
    logs = listar_log_actores(_get_db_path(), limite=200)

    filas = ""
    for l in logs:
        ts = (l.get("cambiado_en") or "")[:19].replace("T", " ")
        campo = l.get("campo") or "—"
        va = str(l.get("valor_anterior") or "—")
        vn = str(l.get("valor_nuevo") or "—")
        nombre = l.get("actor_nombre") or f"#{l.get('actor_id','?')}"
        filas += f"""<tr>
  <td style="color:var(--muted);font-size:11px;white-space:nowrap">{ts}</td>
  <td style="color:var(--accent);font-size:12px">{escape(nombre)}</td>
  <td><span class="badge badge-off">{escape(campo)}</span></td>
  <td style="color:var(--muted);font-size:12px">
    {escape(va)} → <b style="color:var(--text)">{escape(vn)}</b>
  </td>
  <td style="color:var(--accent);font-size:11px">{escape(l.get('usuario') or '—')}</td>
  <td style="color:var(--muted);font-size:11px">{escape(l.get('motivo') or '—')}</td>
</tr>\n"""

    cuerpo = (filas if logs else
              '<tr><td colspan="6" style="color:var(--muted);padding:16px">'
              'Sin cambios registrados todavía.</td></tr>')

    contenido = f"""
<div class="alert-box alert-info">
  Registro completo de cambios en actores (creación, edición, propagación de nivel).
  <a href="/admin/actores" style="color:var(--accent)">← Volver a actores</a>
</div>
<div class="card">
  <div class="card-title">Historial de actores — config_actores_log ({len(logs)})</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Fecha</th><th>Actor</th><th>Campo</th><th>Cambio</th>
        <th>Usuario</th><th>Motivo</th>
      </tr></thead>
      <tbody>{cuerpo}</tbody>
    </table>
  </div>
</div>"""

    return HTMLResponse(_page("Log actores", contenido, "actores", sesion["username"]))
