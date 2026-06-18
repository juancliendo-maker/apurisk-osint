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
        ("fuentes",  "📡", "Fuentes RSS",   "/admin/fuentes"),
        ("factores", "⚖️",  "Factores",      "/admin/factores"),
        ("alertas",  "🚨", "Alertas",       "/admin/alertas"),
        ("logs",     "📋", "Logs sistema",  "/admin/logs"),
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
        filas_html += (f'<tr><td colspan="5" style="background:var(--bg-2);color:var(--muted);'
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
              background:var(--bg-3);color:var(--text);font-size:11px;cursor:pointer">Guardar</button>
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
        <th>Fuente</th><th>Estado</th><th>Calidad</th><th>Acción</th><th>URL</th>
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
# GET /admin/factores   Factores P×I activos
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/factores", response_class=HTMLResponse)
async def admin_factores(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err

    # Último snapshot para el último ciclo
    snap = _ultimo_snapshot()
    factores_snap = (snap.get("riesgo") or {}).get("factores") if snap else None

    # Leer de BD: último snapshot de factores
    factores_bd: list[dict] = []
    try:
        with _db_conn() as conn:
            rows = conn.execute("""
                SELECT f.factor_id, f.nombre, f.categoria,
                       f.probabilidad, f.impacto, f.score,
                       f.nivel, f.tendencia, f.menciones_24h
                FROM factores f
                JOIN snapshots s ON f.snapshot_id = s.id
                WHERE s.id = (SELECT MAX(id) FROM snapshots)
                ORDER BY f.score DESC
            """).fetchall()
            factores_bd = [dict(r) for r in rows]
    except Exception:
        pass

    # Usar lista del snapshot si la BD no tiene datos aún
    fuente = factores_bd or []
    if not fuente and factores_snap and isinstance(factores_snap, list):
        fuente = factores_snap

    filas = ""
    for f in fuente:
        fid   = escape(f.get("factor_id") or "—")
        fname = escape(f.get("nombre") or "—")
        fcat  = escape(f.get("categoria") or "—")
        prob  = f.get("probabilidad", 0) or 0
        imp   = f.get("impacto", 0) or 0
        score = f.get("score", 0) or 0
        nivel = f.get("nivel") or "—"
        men24 = f.get("menciones_24h", 0) or 0
        tend  = f.get("tendencia") or "—"
        tend_ico = {"subiendo": "▲", "bajando": "▼", "estable": "▬"}.get(tend, "—")
        tend_col = {"subiendo": "var(--alto)", "bajando": "var(--bajo)", "estable": "var(--muted)"}.get(tend, "var(--muted)")
        filas += f"""<tr>
  <td><span style="color:var(--muted);font-size:10px">{fid}</span><br><b>{fname}</b></td>
  <td style="color:var(--muted)">{fcat}</td>
  <td style="font-weight:600">{prob}%</td>
  <td style="font-weight:600">{imp}</td>
  <td style="font-size:16px;font-weight:700;color:var(--accent)">{score:.1f}</td>
  <td>{_badge_nivel(nivel)}</td>
  <td style="{'color:var(--muted)' if men24 == 0 else 'color:var(--text);font-weight:600'}">{men24}</td>
  <td style="color:{tend_col}">{tend_ico}</td>
</tr>\n"""

    sin_datos = not fuente
    aviso = '<div class="alert-box alert-warn">Sin datos de factores en BD aún. Ejecuta un ciclo del pipeline.</div>' if sin_datos else ""

    contenido = f"""
<div class="alert-box alert-info">
  ℹ️ Fase A: factores leídos desde el último snapshot del pipeline.
  En Fase B serán editables (impacto_base, prob_base, keywords) desde este panel.
</div>
{aviso}
<div class="card">
  <div class="card-title">Factores P×I · último ciclo ({len(fuente)} factores)</div>
  <div style="font-size:11px;color:var(--muted);margin-bottom:12px">
    Coincidencia de keywords: % de keywords fuerte/contexto que matchearon —
    <b>es coincidencia léxica, no un modelo de NLP.</b>
    Menciones 24h: artículos que activaron el factor en las últimas 24h.
  </div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Factor</th><th>Categoría</th>
        <th>Prob.</th><th>Impacto</th><th>Score P×I</th>
        <th>Nivel</th><th>Mencs 24h</th><th>Tend.</th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
</div>
"""
    return HTMLResponse(_page("Factores de riesgo", contenido, "factores", sesion["username"]))


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
