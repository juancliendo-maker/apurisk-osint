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
        ("proyeccion","🔮", "Proyección",     "/admin/proyeccion"),
        ("quiebres",  "🔻", "Puntos de Quiebre", "/admin/quiebres"),
        ("inteligencia","🧠","Inteligencia",  "/admin/inteligencia"),
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
    # El score nacional vive bajo la clave "global" (igual que el dashboard
    # público). Antes leía "score" (inexistente) → 0.0 fijo desde el día uno.
    score   = snap.get("riesgo", {}).get("global", 0) if snap else 0
    nivel   = snap.get("riesgo", {}).get("nivel", "—") if snap else "—"
    # Artículos: dato REAL de la BD (no del snapshot del ciclo, que puede ser 0
    # en un ciclo puntual y dar un falso "0"). 24h = capturados en la ventana;
    # total = todos los artículos persistidos.
    n_arts = 0
    n_24h = 0

    # Alertas recientes (últimas 24h) desde BD — se cuentan DISTINTAS por título:
    # cada ciclo re-inserta las mismas alertas con snapshot_id nuevo (necesario
    # para alertas_persistentes), así que COUNT(*) inflaría el KPI. DISTINCT titulo
    # da el número real de alertas únicas sin tocar el almacenamiento.
    n_alertas_24h = 0
    n_alertas_crit = 0
    try:
        with _db_conn() as conn:
            cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            r_tot = conn.execute("SELECT COUNT(*) as c FROM articulos").fetchone()
            n_arts = r_tot["c"] if r_tot else 0
            r_24 = conn.execute(
                "SELECT COUNT(*) as c FROM articulos WHERE capturado_en >= ?", (cutoff,)
            ).fetchone()
            n_24h = r_24["c"] if r_24 else 0
            row = conn.execute(
                "SELECT COUNT(DISTINCT titulo) as c FROM alertas WHERE timestamp >= ?", (cutoff,)
            ).fetchone()
            n_alertas_24h = row["c"] if row else 0
            row2 = conn.execute(
                "SELECT COUNT(DISTINCT titulo) as c FROM alertas "
                "WHERE nivel='CRÍTICA' AND timestamp >= ?",
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

    # ── Urgencia combinada: gravedad + actividad + velocidad ──────────────────
    # La velocidad sola no mide urgencia: un riesgo crónico crítico (grave y muy
    # activo de forma sostenida) tiene velocidad baja porque ya está saturado en
    # cobertura, pero ES urgente por su criticidad persistente. Combinamos los tres.
    #
    # Clasificación (umbrales editables desde calibración):
    #   APAGADO     → Y < umbral_y (no grave, aunque sea ruidoso)
    #   LATENTE     → Y ≥ umbral_y pero actividad < act_prioritario
    #   PRIORITARIO → Y ≥ umbral_y y (act_prioritario ≤ actividad < act_urgente, O escalando)
    #   URGENTE     → Y ≥ umbral_y y actividad ≥ act_urgente  (rojo graduado por índice)
    act_urgente     = params.get("act_urgente", 10.0)
    act_prioritario = params.get("act_prioritario", 5.0)
    peso_g = params.get("peso_gravedad", 0.6)
    peso_a = params.get("peso_actividad", 0.25)
    peso_v = params.get("peso_velocidad", 0.15)
    act_ref = max(0.1, params.get("act_ref", 15.0))
    vel_ref = max(0.1, params.get("vel_ref", 5.0))
    vel_prioritario = params.get("vel_prioritario", 10.0)  # usado para "escalando"

    # Colores no-urgentes
    _COLOR_LATENTE  = "#94a3b8"  # gris claro — grave pero quieto
    _COLOR_APAGADO  = "#475569"  # slate apagado — no grave
    _COLOR_PRIORITARIO = "#f59e0b"  # ámbar — grave, empieza a moverse

    # Rampa de intensidad del rojo para URGENTES: índice → color.
    # El índice más alto arde más brillante (#ef4444); el más bajo, más oscuro (#7f1d1d).
    _RAMPA_I_MIN, _RAMPA_I_MAX = 0.65, 0.90  # rango del índice mapeado a la rampa
    _ROJO_LO = (127, 29, 29)   # #7f1d1d — urgente apagado
    _ROJO_HI = (239, 68, 68)   # #ef4444 — urgente intenso

    def _indice_urgencia(y: float, actividad: float, velocidad: float) -> float:
        """Índice 0-1 que combina gravedad (manda), actividad y velocidad positiva."""
        grav_norm = min(1.0, max(0.0, y / 100.0))
        act_norm = min(1.0, max(0.0, actividad) / act_ref)
        vel_norm = min(1.0, max(0.0, velocidad) / vel_ref)  # vel negativa no resta
        return round(peso_g * grav_norm + peso_a * act_norm + peso_v * vel_norm, 3)

    def _rojo_intensidad(indice: float) -> str:
        """Mapea el índice de urgencia a un tono de rojo (oscuro→brillante)."""
        t = (indice - _RAMPA_I_MIN) / max(1e-6, _RAMPA_I_MAX - _RAMPA_I_MIN)
        t = min(1.0, max(0.0, t))
        r = round(_ROJO_LO[0] + t * (_ROJO_HI[0] - _ROJO_LO[0]))
        g = round(_ROJO_LO[1] + t * (_ROJO_HI[1] - _ROJO_LO[1]))
        b = round(_ROJO_LO[2] + t * (_ROJO_HI[2] - _ROJO_LO[2]))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _clasificar_urgencia(y: float, actividad: float,
                             velocidad: float) -> tuple[str, str, float]:
        """Devuelve (clase, color_hex, indice). Indice solo significativo para urgentes."""
        if y < umbral_y:
            return "no_grave", _COLOR_APAGADO, 0.0
        indice = _indice_urgencia(y, actividad, velocidad)
        if actividad >= act_urgente:
            return "URGENTE", _rojo_intensidad(indice), indice
        escalando = velocidad >= vel_prioritario
        if actividad >= act_prioritario or escalando:
            return "PRIORITARIO", _COLOR_PRIORITARIO, indice
        return "LATENTE", _COLOR_LATENTE, indice

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

        # Velocidad 7d + clasificación de URGENCIA combinada (gravedad+actividad+velocidad).
        # El color codifica urgencia; la intensidad del rojo, el índice (criticidad).
        velocidad = _velocidad(tema)
        urgencia, color_urgencia, indice_urg = _clasificar_urgencia(y, x, velocidad)

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
            "indice_urgencia": indice_urg,
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
    coef_sim_idx = params.get("coef_sim_idx", 0.03)
    piso_gravedad = params.get("piso_gravedad", 65.0)

    score_global_b = 0.0
    formula_score_b = (
        f"G_base + (100−G_base)·U_norm · "
        f"G_base={piso_gravedad:g}·(n_graves/n_total) · "
        f"U_norm=indice_max + (1−indice_max)·min(1, {coef_sim_idx:g}·(n_urgentes−1))"
    )
    if globos_b:
        n_total = len(globos_b)
        graves = [g for g in globos_b if g["y"] >= umbral_y]
        n_graves = len(graves)
        frac_graves = n_graves / max(1, n_total)
        g_base = piso_gravedad * frac_graves

        # Índice de urgencia máximo entre los graves (frente más crítico) y nº de urgentes.
        # Combina los tres factores (gravedad+actividad+velocidad), no solo velocidad.
        indice_max = max([g.get("indice_urgencia", 0.0) for g in graves], default=0.0)
        n_urgentes = sum(1 for g in graves if g["urgencia"] == "URGENTE")

        # La simultaneidad llena parte del HEADROOM restante (1−indice_max), no se suma
        # cruda: así el Score nunca satura en 100 mientras el frente más crítico sea <1.
        sim_factor = min(1.0, coef_sim_idx * max(0, n_urgentes - 1))
        u_norm = indice_max + (1.0 - indice_max) * sim_factor
        score_global_b = round(g_base + (100.0 - g_base) * u_norm, 1)
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
        "act_urgente": act_urgente,
        "act_prioritario": act_prioritario,
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

            # Línea de urgencia combinada (clasificación + índice + velocidad)
            _vel = g.get("velocidad", 0)
            _urg = g.get("urgencia", "—")
            _idx = g.get("indice_urgencia", 0.0)
            _vel_signo = f"+{_vel:g}" if _vel > 0 else f"{_vel:g}"
            _idx_str = f" · índice {_idx:.2f}" if _urg in ("URGENTE", "PRIORITARIO") else ""
            linea_urg = f"URGENCIA: {_urg}{_idx_str} · velocidad 7d {_vel_signo} pts"

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
  // Inline plugin — NOT registered globally so it only fires for this chart instance.
  // Global Chart.register would apply the plugin to every chart on the page,
  // causing both sets of quadrant labels to overlap on each matrix.
  var drawQ = {{
    id: 'quadrants_{canvas_id}',
    afterDraw: function(chart) {{
      var ctx = chart.ctx;
      var ca = chart.chartArea;
      var xs = chart.scales.x, ys = chart.scales.y;
      ctx.save();
      // ── Divider lines ──────────────────────────────────────────────────────
      ctx.strokeStyle = '#334155'; ctx.setLineDash([4,4]); ctx.lineWidth = 1;
      var xm = xs.getPixelForValue(umbralX), ym = ys.getPixelForValue(umbralY);
      ctx.beginPath(); ctx.moveTo(xm, ca.top); ctx.lineTo(xm, ca.bottom); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(ca.left, ym); ctx.lineTo(ca.right, ym); ctx.stroke();
      ctx.setLineDash([]);
      // ── Quadrant labels at the extreme corners, on top of the border ───────
      // Each label is anchored to its corner pixel (ca.left/right, ca.top/bottom)
      // so it sits in the ~4px gutter right at the chart edge, away from bubbles.
      var qlabels = [
        [etq.ti, 'left',  'top'],
        [etq.td, 'right', 'top'],
        [etq.bi, 'left',  'bottom'],
        [etq.bd, 'right', 'bottom'],
      ];
      ctx.font = 'bold 9px sans-serif';
      var PAD = 3;
      qlabels.forEach(function(l) {{
        var text = l[0], ha = l[1], va = l[2];
        var isLeft  = (ha === 'left');
        var isTop   = (va === 'top');
        // Pixel anchor: 4px inside each corner of the chart area border
        var tx = isLeft ? (ca.left  + 4) : (ca.right  - 4);
        var ty = isTop  ? (ca.top   + 3) : (ca.bottom - 3);
        ctx.textAlign     = ha;
        ctx.textBaseline  = isTop ? 'top' : 'bottom';
        var tw = ctx.measureText(text).width;
        var th = 11; // text height ~9px + 2
        // Background pill: darkens the corner so text reads clearly over gridlines
        var bx = isLeft ? (tx - PAD) : (tx - tw - PAD);
        var by = isTop  ? (ty - PAD) : (ty - th - PAD);
        ctx.fillStyle = 'rgba(8,14,26,0.88)';
        ctx.fillRect(bx, by, tw + PAD * 2, th + PAD * 2);
        ctx.fillStyle = '#64748b';
        ctx.fillText(text, tx, ty);
      }});
      ctx.textBaseline = 'alphabetic';
      ctx.restore();
    }}
  }};
  if (window.Chart) {{
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
      }},
      plugins: [drawQ]
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
    act_urgente_p     = md["act_urgente"]
    act_prioritario_p = md["act_prioritario"]

    # Leyenda de URGENCIA para la Matriz B. El color codifica clasificación; la
    # intensidad del rojo (en los urgentes) gradúa la criticidad por índice combinado.
    _leyenda_urgencia = [
        ("#e64141", f"Urgente intenso (grave, act ≥ {act_urgente_p:g}, índice alto)"),
        ("#a62b2b", "Urgente apagado (grave y activo, índice menor)"),
        ("#f59e0b", f"Prioritario (grave, act ≥ {act_prioritario_p:g} o escalando)"),
        ("#94a3b8", "Latente (grave pero quieto)"),
        ("#475569", "Apagado (no grave)"),
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

        # Urgencia combinada: color del globo (intensidad = índice) + velocidad + índice
        velocidad = g_b.get("velocidad", 0) if g_b else 0
        urgencia = g_b.get("urgencia", "—") if g_b else "—"
        indice_urg = g_b.get("indice_urgencia", 0.0) if g_b else 0.0
        # Usa el color real del globo (ya graduado por índice en los urgentes)
        _urg_color = g_b.get("color", "#64748b") if g_b else "#64748b"
        _urg_label = "Apagado" if urgencia == "no_grave" else urgencia
        _vel_signo = f"+{velocidad:g}" if velocidad > 0 else f"{velocidad:g}"
        _idx_html = (
            f' · índice {indice_urg:.2f}' if urgencia in ("URGENTE", "PRIORITARIO") else ""
        )
        urgencia_html = (
            f'<span style="display:inline-flex;align-items:center;gap:5px">'
            f'<span style="width:9px;height:9px;border-radius:50%;background:{_urg_color};'
            f'display:inline-block"></span>'
            f'<span style="color:{_urg_color};font-weight:600">{escape(_urg_label)}</span></span>'
            f'<br><span style="font-size:10px;color:var(--muted)">vel {_vel_signo} pts/7d{_idx_html}</span>'
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

def _tabla_velocidades_html(
    vel_temas: dict,
    params: dict,
    hay_prev: bool,
    snapshot_ts: str,
) -> str:
    """Bloque HTML: tabla de velocidades 7d de los 8 temas para calibrar VEL_REF.

    La velocidad ya NO clasifica la urgencia por sí sola (eso lo hace el índice
    combinado en la Matriz B). Esta tabla sirve para calibrar URGENCIA_VEL_REF:
    elige un vel_ref que normalice a ~1.0 las aceleraciones genuinas.
    """
    vel_ref = max(0.1, params.get("vel_ref", 5.0))

    if not vel_temas:
        return """
<div class="card" style="margin-top:0">
  <div class="card-title">Velocidades 7d por tema</div>
  <p style="color:var(--muted);font-size:13px">
    Sin snapshot disponible — ejecuta un ciclo del motor primero.
  </p>
</div>"""

    sin_prev_aviso = (
        '<div style="color:#f59e0b;font-size:12px;margin-bottom:8px">'
        '⚠ Ventana previa (7-14d) aún no disponible — velocidades serán 0 hasta completar 14 días.</div>'
    ) if not hay_prev else ""

    filas = ""
    temas_nombres = {
        "estabilidad_gobierno": "Estabilidad gobierno",
        "conflictos_sociales": "Conflictos sociales",
        "riesgo_regulatorio": "Riesgo regulatorio",
        "polarizacion": "Polarización",
        "corrupcion": "Corrupción",
        "seguridad": "Seguridad / Criminalidad",
        "electoral": "Electoral",
        "economico_inversion": "Económico / Inversión",
    }
    for tema in _IMPACTO_TEMA:
        vel = vel_temas.get(tema, 0.0)
        vel_str = f"+{vel:g}" if vel > 0 else f"{vel:g}"
        vel_norm = min(1.0, max(0.0, vel) / vel_ref)  # cómo contribuye al índice
        if vel >= vel_ref:
            color = "#22c55e"
            etiq  = "acelera (norm 1.0)"
        elif vel > 0:
            color = "#94a3b8"
            etiq  = f"acelera (norm {vel_norm:.2f})"
        elif vel < 0:
            color = "#64748b"
            etiq  = "desacelera (no resta)"
        else:
            color = "#64748b"
            etiq  = "estable"
        barra_w = min(100, max(0, int(abs(vel) * 6))) if hay_prev else 0
        barra_color = "#22c55e" if vel > 0 else "#94a3b8"
        barra_html = (
            f'<div style="width:{barra_w}px;height:8px;background:{barra_color};'
            f'border-radius:3px;display:inline-block;vertical-align:middle"></div>'
            if hay_prev and barra_w > 0 else
            '<div style="width:4px;height:8px;background:#334155;border-radius:3px;display:inline-block;vertical-align:middle"></div>'
        )
        filas += (
            f'<tr>'
            f'<td>{temas_nombres.get(tema, tema)}</td>'
            f'<td style="text-align:center;font-weight:600;color:{color}">{vel_str}</td>'
            f'<td>{barra_html}</td>'
            f'<td><span style="background:{color}22;color:{color};padding:2px 7px;'
            f'border-radius:4px;font-size:11px;font-weight:600">{etiq}</span></td>'
            f'</tr>'
        )

    ts_str = f' · snapshot {snapshot_ts}' if snapshot_ts else ""
    return f"""
<div class="card" style="margin-top:0">
  <div class="card-title">Velocidades 7d por tema
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      vel = %actividad(0-7d) − %actividad(7-14d){ts_str}
    </span>
  </div>
  {sin_prev_aviso}
  <p style="color:var(--muted);font-size:12px;margin:0 0 10px 0">
    Referencia actual <b>URGENCIA_VEL_REF = {vel_ref:g}</b>: una velocidad ≥ {vel_ref:g}
    contribuye al máximo (norm 1.0) al índice de urgencia. La velocidad negativa no resta.
    La urgencia final (clasificación + intensidad) se ve en la Matriz B del semáforo.
  </p>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Tema</th>
        <th style="text-align:center">Velocidad 7d</th>
        <th>Magnitud</th>
        <th>Contribución al índice</th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
  <p style="color:var(--muted);font-size:11px;margin-top:8px">
    Velocidad positiva = el tema gana cuota de cobertura mediática respecto a la semana anterior.
    Referencia de calibración: observa el rango real de tus temas y elige umbrales que
    distingan los picos genuinos del ruido de fondo.
  </p>
</div>"""


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

    # Velocidades 7d desde último snapshot (para tabla de referencia)
    _vel_temas: dict[str, float] = {}
    _vel_hay_prev = False
    _vel_snapshot_ts = ""
    try:
        out_dir = Path(OUTPUT_DIR)
        _snaps = sorted(out_dir.glob("apurisk_snapshot_*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        for _s in _snaps[:1]:
            _sdata = json.loads(_s.read_text(encoding="utf-8"))
            _osint = _sdata.get("osint_motor", {})
            _vel_snapshot_ts = _sdata.get("timestamp", "")[:16].replace("T", " ")
            _conteos_rec  = _osint.get("temas_7d_conteos", {})
            _conteos_prev = _osint.get("temas_prev7d_conteos", {})
            _total_rec  = max(1, sum(_conteos_rec.values()))
            _total_prev = max(1, sum(_conteos_prev.values()))
            _vel_hay_prev = bool(_conteos_prev)
            for _tema in _IMPACTO_TEMA:
                _act_rec  = (_conteos_rec.get(_tema, 0)  / _total_rec)  * 100
                _act_prev = (_conteos_prev.get(_tema, 0) / _total_prev) * 100
                _vel_temas[_tema] = round(_act_rec - _act_prev, 1)
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
        # ── Urgencia combinada (clasificación + intensidad del rojo) ──
        ("SEMAFORO_ACTIVIDAD_URGENTE",  params["act_urgente"],
         "Actividad (% vol.) mínima para clasificar un tema grave como URGENTE (rojo)"),
        ("SEMAFORO_ACTIVIDAD_PRIORITARIO", params["act_prioritario"],
         "Actividad (% vol.) mínima para clasificar un tema grave como PRIORITARIO (ámbar)"),
        ("URGENCIA_PESO_GRAVEDAD",      params["peso_gravedad"],
         "Índice de urgencia: peso de la gravedad (Y/100). La gravedad manda"),
        ("URGENCIA_PESO_ACTIVIDAD",     params["peso_actividad"],
         "Índice de urgencia: peso de la actividad normalizada"),
        ("URGENCIA_PESO_VELOCIDAD",     params["peso_velocidad"],
         "Índice de urgencia: peso de la velocidad positiva normalizada"),
        ("URGENCIA_ACT_REF",            params["act_ref"],
         "Índice de urgencia: actividad de referencia (act_norm = min(1, act/ref))"),
        ("URGENCIA_VEL_REF",            params["vel_ref"],
         "Índice de urgencia: velocidad de referencia (vel_norm = min(1, vel/ref))"),
        ("SCORE_B_PISO_GRAVEDAD",        params["piso_gravedad"],
         "Score B: peso del componente estructural G_base = piso × frac_graves"),
        ("SCORE_B_COEF_SIM_IDX",         params["coef_sim_idx"],
         "Score B: aporte al índice por cada tema URGENTE adicional (simultaneidad)"),
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

{_tabla_velocidades_html(_vel_temas, params, _vel_hay_prev, _vel_snapshot_ts)}

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
        # Urgencia combinada (gravedad + actividad + velocidad)
        "SEMAFORO_ACTIVIDAD_URGENTE", "SEMAFORO_ACTIVIDAD_PRIORITARIO",
        "URGENCIA_PESO_GRAVEDAD", "URGENCIA_PESO_ACTIVIDAD", "URGENCIA_PESO_VELOCIDAD",
        "URGENCIA_ACT_REF", "URGENCIA_VEL_REF",
        "SCORE_B_PISO_GRAVEDAD", "SCORE_B_COEF_SIM_IDX",
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
    <a href="/admin/actores/activacion"
       style="background:var(--bg-3);color:var(--muted);border:1px solid #334155;
              border-radius:6px;padding:6px 14px;font-size:12px">
      Activación CVO →
    </a>
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


@router.get("/actores/{actor_id:int}", response_class=HTMLResponse)
async def admin_actores_detalle(request: Request, actor_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import (
        obtener_actor, cargar_niveles_base, listar_log_actores, NIVELES_ACTOR,
        cargar_cvo_actor, cargar_dinamica_actor, cargar_parametros_trayectoria,
    )
    db = _get_db_path()
    actor = obtener_actor(db, actor_id)
    if not actor:
        return RedirectResponse("/admin/actores?err=Actor+no+encontrado", status_code=303)

    niveles_base = cargar_niveles_base(db)
    temas = list(_IMPACTO_TEMA.keys())
    logs = listar_log_actores(db, actor_id, limite=30)
    cvo_rows = {r["tema"]: r for r in cargar_cvo_actor(db, actor_id)}
    din = cargar_dinamica_actor(db, actor_id)
    par_tray = cargar_parametros_trayectoria(db)

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

    # Bloque de Capa 3 CVO: un formulario independiente por tema vinculado.
    # Los temas vinculados provienen de actor["temas"]; cvo_rows tiene los datos guardados.
    temas_actor = actor.get("temas", [])
    cvo_bloques_html = ""
    peso_actor = actor.get("peso_calculado", 0)
    for t in sorted(temas_actor):
        row = cvo_rows.get(t, {
            "tema": t, "interes_directo": 3, "postura_declarada": 3,
            "antecedente_accion": 3, "ventana_coyuntural": 3,
            "ausencia_contrapesos": 3, "recursos_movilizables": 3,
            "indice_activacion": None,
        })
        cvo_bloques_html += f"""
<form id="cvo-form-{escape(t)}" method="post"
      action="/admin/actores/{actor_id}/cvo" style="display:none">
  <input type="hidden" name="tema" value="{escape(t)}">
</form>
{_cvo_tema_bloque(t, row, peso_actor)}"""

    if temas_actor:
        cvo_section = f"""
<div class="card" style="margin-top:0">
  <div class="card-title">Capa 3 — Índice de Activación Estratégica (CVO)
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      Índice = (C × V × O)^(1/3) × 100 · C = peso/100
    </span>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 12px 0">
    Por cada tema vinculado: evalúa las 6 señales y guarda por separado.
    El recálculo es inmediato en pantalla; el guardar persiste en la BD y aparece en el log.
    <a href="/admin/actores/activacion?tema={escape(temas_actor[0])}"
       style="color:var(--accent)">Ver activación por tema →</a>
  </p>
  <div id="actor-peso-value" data-peso="{peso_actor}" style="display:none"></div>
  {cvo_bloques_html}
</div>"""
    else:
        cvo_section = ""

    dinamica_section = _dinamica_section_html(
        actor_id, din, cvo_rows, temas_actor, par_tray)

    contenido = f"""
{_ACTOR_CALC_JS}
{_CVO_CALC_JS}
{_din_calc_js(par_tray)}
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

{cvo_section}

{dinamica_section}

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


@router.post("/actores/{actor_id:int}/editar")
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


@router.post("/actores/{actor_id:int}/toggle")
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


# ──────────────────────────────────────────────────────────────────────────────
# Capa 3 CVO — Índice de Activación Estratégica
# ──────────────────────────────────────────────────────────────────────────────

_CVO_CALC_JS = """
<script>
(function() {
  // Recalcula el índice CVO en tiempo real para cada bloque de tema.
  // indice = (C × V × O)^(1/3) × 100
  // V = (interes×2 + postura + antecedente) / 20
  // O = (ventana×2 + contrapesos + recursos) / 20
  // C = peso_actor / 100
  function recalcCvo(tema) {
    var prefix = 'cvo_' + tema + '_';
    function gi(name) {
      var el = document.querySelector('[name="' + prefix + name + '"]');
      return el ? (parseInt(el.value) || 3) : 3;
    }
    var pesoEl = document.getElementById('actor-peso-value');
    var C = pesoEl ? (parseFloat(pesoEl.dataset.peso) || 0) / 100 : 0;
    var interes = gi('interes_directo');
    var postura = gi('postura_declarada');
    var anteced = gi('antecedente_accion');
    var ventana = gi('ventana_coyuntural');
    var contrap = gi('ausencia_contrapesos');
    var recurs  = gi('recursos_movilizables');
    var V = (interes * 2 + postura + anteced) / 20;
    var O = (ventana * 2 + contrap + recurs) / 20;
    var idx = (C > 0 && V > 0 && O > 0) ? Math.pow(C * V * O, 1/3) * 100 : 0;

    var elIdx = document.getElementById('cvo-idx-' + tema);
    var elV   = document.getElementById('cvo-v-'   + tema);
    var elO   = document.getElementById('cvo-o-'   + tema);
    var elBar = document.getElementById('cvo-bar-' + tema);
    if (elIdx) {
      elIdx.textContent = idx.toFixed(1);
      var color = idx >= 70 ? '#ef4444' : idx >= 50 ? '#f97316' : idx >= 30 ? '#f59e0b' : '#94a3b8';
      elIdx.style.color = color;
      if (elBar) { elBar.style.width = Math.min(100, idx) + '%'; elBar.style.background = color; }
    }
    if (elV) elV.textContent = 'V=' + V.toFixed(2);
    if (elO) elO.textContent = 'O=' + O.toFixed(2);
  }

  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[data-cvo-tema]').forEach(function(block) {
      var tema = block.dataset.cvoTema;
      block.querySelectorAll('input[type=range]').forEach(function(sl) {
        sl.addEventListener('input', function() { recalcCvo(tema); });
      });
      recalcCvo(tema);
    });
  });
})();
</script>
"""


def _cvo_slider(prefix: str, name: str, label: str, val: int,
                doble: bool, form_id: str = "") -> str:
    full_name = f"{prefix}{name}"
    badge = ' <span style="font-size:10px;color:var(--accent)">×2</span>' if doble else ""
    form_attr = f' form="{escape(form_id)}"' if form_id else ""
    return f"""
<div style="margin-bottom:6px">
  <div style="display:flex;justify-content:space-between;margin-bottom:2px">
    <span style="font-size:11px;color:var(--muted)">{escape(label)}{badge}</span>
    <span style="font-size:12px;font-weight:700;color:var(--text)" id="val-{full_name}">{val}</span>
  </div>
  <input type="range" name="{full_name}" min="1" max="5" value="{val}"{form_attr}
         style="width:100%;accent-color:var(--accent)"
         oninput="document.getElementById('val-{full_name}').textContent=this.value">
</div>"""


def _cvo_tema_bloque(tema: str, row: dict, peso_actor: float) -> str:
    """Bloque CVO editable para un tema vinculado al actor."""
    from ..storage.config_loader import _calcular_indice_cvo
    prefix = f"cvo_{tema}_"
    idx_actual = row.get("indice_activacion")
    v_norm = (row.get("interes_directo", 3) * 2 + row.get("postura_declarada", 3)
              + row.get("antecedente_accion", 3)) / 20.0
    o_norm = (row.get("ventana_coyuntural", 3) * 2 + row.get("ausencia_contrapesos", 3)
              + row.get("recursos_movilizables", 3)) / 20.0

    if idx_actual is not None:
        idx_color = ("#ef4444" if idx_actual >= 70 else
                     "#f97316" if idx_actual >= 50 else
                     "#f59e0b" if idx_actual >= 30 else "#94a3b8")
        idx_str = f"{idx_actual:.1f}"
    else:
        idx_color, idx_str = "#64748b", "—"

    def g(field): return int(row.get(field, 3))

    fid = f"cvo-form-{tema}"
    sliders_v = (
        _cvo_slider(prefix, "interes_directo",    "Interés directo",       g("interes_directo"),    True,  fid) +
        _cvo_slider(prefix, "postura_declarada",  "Postura declarada",     g("postura_declarada"),  False, fid) +
        _cvo_slider(prefix, "antecedente_accion", "Antecedente de acción", g("antecedente_accion"), False, fid)
    )
    sliders_o = (
        _cvo_slider(prefix, "ventana_coyuntural",   "Ventana coyuntural",     g("ventana_coyuntural"),   True,  fid) +
        _cvo_slider(prefix, "ausencia_contrapesos", "Ausencia contrapesos",   g("ausencia_contrapesos"), False, fid) +
        _cvo_slider(prefix, "recursos_movilizables","Recursos movilizables",  g("recursos_movilizables"),False, fid)
    )

    return f"""
<div data-cvo-tema="{escape(tema)}"
     style="border:1px solid #1e293b;border-radius:6px;padding:12px;margin-bottom:10px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
    <span style="font-size:13px;font-weight:600;color:var(--text)">
      {escape(tema.replace('_',' ').title())}
    </span>
    <div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:11px;color:var(--muted)">
        <span id="cvo-v-{escape(tema)}">V={v_norm:.2f}</span> ·
        <span id="cvo-o-{escape(tema)}">O={o_norm:.2f}</span>
      </span>
      <div style="text-align:right">
        <div style="font-size:20px;font-weight:700;color:{idx_color}"
             id="cvo-idx-{escape(tema)}">{idx_str}</div>
        <div style="font-size:9px;color:var(--muted)">ACTIVACIÓN</div>
      </div>
    </div>
  </div>
  <div style="height:4px;background:#1e293b;border-radius:2px;margin-bottom:10px">
    <div id="cvo-bar-{escape(tema)}"
         style="height:100%;width:{min(100, idx_actual or 0):.0f}%;background:{idx_color};
                border-radius:2px;transition:width .2s"></div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
    <div>
      <div style="font-size:11px;font-weight:600;color:var(--accent);margin-bottom:6px;
                  text-transform:uppercase;letter-spacing:.5px">Voluntad (V)</div>
      {sliders_v}
    </div>
    <div>
      <div style="font-size:11px;font-weight:600;color:var(--accent);margin-bottom:6px;
                  text-transform:uppercase;letter-spacing:.5px">Oportunidad (O)</div>
      {sliders_o}
    </div>
  </div>
  <div style="margin-top:10px;text-align:right">
    <button type="submit" form="cvo-form-{escape(tema)}" name="guardar_cvo" value="1"
            style="background:var(--accent);color:#000;border:none;border-radius:4px;
                   padding:5px 14px;font-size:12px;font-weight:600;cursor:pointer">
      Guardar {escape(tema.replace('_',' ').title())}
    </button>
  </div>
</div>"""


@router.post("/actores/{actor_id:int}/cvo")
async def admin_actores_cvo(request: Request, actor_id: int):
    """Guarda las 6 señales CVO de un actor para un tema específico."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import (
        guardar_cvo_actor_tema, obtener_actor, LockTimeoutError,
    )
    form = await request.form()
    tema = form.get("tema", "").strip()
    if not tema or tema not in _IMPACTO_TEMA:
        return RedirectResponse(
            f"/admin/actores/{actor_id}?err=Tema+inválido", status_code=303)
    db = _get_db_path()
    actor = obtener_actor(db, actor_id)
    if not actor:
        return RedirectResponse("/admin/actores?err=Actor+no+encontrado", status_code=303)
    prefix = f"cvo_{tema}_"
    senales = {
        "interes_directo":       int(form.get(f"{prefix}interes_directo",    3)),
        "postura_declarada":     int(form.get(f"{prefix}postura_declarada",  3)),
        "antecedente_accion":    int(form.get(f"{prefix}antecedente_accion", 3)),
        "ventana_coyuntural":    int(form.get(f"{prefix}ventana_coyuntural", 3)),
        "ausencia_contrapesos":  int(form.get(f"{prefix}ausencia_contrapesos", 3)),
        "recursos_movilizables": int(form.get(f"{prefix}recursos_movilizables", 3)),
    }
    try:
        indice = guardar_cvo_actor_tema(
            db, actor_id, tema, senales,
            actor["peso_calculado"], sesion["username"],
        )
        return RedirectResponse(
            f"/admin/actores/{actor_id}?msg=CVO+guardado+·+índice={indice:.1f}",
            status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            f"/admin/actores/{actor_id}?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(
            f"/admin/actores/{actor_id}?err={escape(str(e))}", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# Capa 4 Dinámica — trayectoria de poder
# ──────────────────────────────────────────────────────────────────────────────

_DIN_SENALES = [
    ("din_alianzas",       "Alianzas",        "¿gana o pierde aliados clave?"),
    ("din_financiamiento", "Financiamiento",  "¿recursos crecen o se erosionan?"),
    ("din_territorio",     "Territorio",      "¿amplía o pierde base territorial/social?"),
    ("din_instituciones",  "Instituciones",   "¿gana posiciones institucionales?"),
    ("din_internacional",  "Internacional",   "¿respaldo o presión externa?"),
    ("din_relevo_lideres", "Relevo de líderes","¿liderazgo sólido o en disputa?"),
    ("din_adaptacion",     "Adaptación",      "¿adapta su estrategia al contexto?"),
]


def _din_etiqueta_html(valor, par):
    """Devuelve (texto, color) de la etiqueta de trayectoria según umbrales."""
    if valor >= par["umbral_ascenso"]:
        return "ASCENSO", "#22c55e"
    if valor <= par["umbral_declive"]:
        return "DECLIVE", "#ef4444"
    return "ESTABLE", "#94a3b8"


def _din_calc_js(par):
    """JS de recálculo en vivo de la trayectoria base y por tema."""
    return f"""
<script>
(function() {{
  var UMBRAL_ASCENSO = {par['umbral_ascenso']};
  var UMBRAL_DECLIVE = {par['umbral_declive']};
  var FACTOR_DIV = {par['factor_div']};
  function etiq(v) {{
    if (v >= UMBRAL_ASCENSO) return ['ASCENSO', '#22c55e', '↑'];
    if (v <= UMBRAL_DECLIVE) return ['DECLIVE', '#ef4444', '↓'];
    return ['ESTABLE', '#94a3b8', '→'];
  }}
  function recalcDin() {{
    var base = 0;
    document.querySelectorAll('input[data-din-senal]').forEach(function(sl) {{
      base += parseInt(sl.value) || 0;
    }});
    var e = etiq(base);
    var elBase = document.getElementById('din-base-val');
    var elLbl  = document.getElementById('din-base-lbl');
    if (elBase) elBase.textContent = (base > 0 ? '+' : '') + base;
    if (elLbl) {{ elLbl.textContent = e[2] + ' ' + e[0]; elLbl.style.color = e[1]; }}
    // Por tema: base + divergencia × factor
    document.querySelectorAll('[data-div-tema]').forEach(function(sel) {{
      var tema = sel.dataset.divTema;
      var div = parseInt(sel.value) || 0;
      var enTema = base + div * FACTOR_DIV;
      var et = etiq(enTema);
      var out = document.getElementById('div-out-' + tema);
      if (out) {{
        var extra = div !== 0 ? ' (base ' + (base>0?'+':'') + base + ', ' +
                    (div>0?'+':'') + (div*FACTOR_DIV) + ' divergencia)' : '';
        out.innerHTML = '<b style="color:' + et[1] + '">' + et[2] + ' ' + et[0] +
                        ' ' + (enTema>0?'+':'') + enTema + '</b>' +
                        '<span style="color:var(--muted);font-size:10px">' + extra + '</span>';
      }}
    }});
  }}
  document.addEventListener('DOMContentLoaded', function() {{
    document.querySelectorAll('input[data-din-senal]').forEach(function(sl) {{
      sl.addEventListener('input', function() {{
        var o = document.getElementById('val-' + sl.name);
        if (o) o.textContent = (parseInt(sl.value)>0?'+':'') + sl.value;
        recalcDin();
      }});
    }});
    document.querySelectorAll('[data-div-tema]').forEach(function(sel) {{
      sel.addEventListener('change', recalcDin);
    }});
    recalcDin();
  }});
}})();
</script>
"""


def _din_slider(name, label, hint, val):
    sign = "+" if val > 0 else ""
    return f"""
<div style="margin-bottom:8px">
  <div style="display:flex;justify-content:space-between;margin-bottom:2px">
    <span style="font-size:11px;color:var(--text)">{escape(label)}
      <span style="color:var(--muted);font-size:10px">— {escape(hint)}</span></span>
    <span style="font-size:12px;font-weight:700;color:var(--accent)" id="val-{name}">{sign}{val}</span>
  </div>
  <input type="range" name="{name}" data-din-senal min="-2" max="2" step="1" value="{val}"
         form="din-form" style="width:100%;accent-color:var(--accent)">
  <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted)">
    <span>−2 retrocede</span><span>0</span><span>+2 avanza</span>
  </div>
</div>"""


def _dinamica_section_html(actor_id, din, cvo_rows, temas_actor, par):
    """Sección Capa 4 — Dinámica: 7 sliders + divergencia por tema."""
    sliders = "".join(_din_slider(n, lbl, hint, int(din.get(n, 0)))
                      for n, lbl, hint in _DIN_SENALES)
    base = din.get("trayectoria_base", 0)
    base_lbl, base_color = _din_etiqueta_html(base, par)
    flecha = "↑" if base_lbl == "ASCENSO" else "↓" if base_lbl == "DECLIVE" else "→"

    # Divergencia por tema
    div_filas = ""
    for t in sorted(temas_actor):
        row = cvo_rows.get(t, {})
        div_actual = int(row.get("divergencia_dinamica") or 0)
        en_tema = base + div_actual * par["factor_div"]
        et_lbl, et_color = _din_etiqueta_html(en_tema, par)
        et_flecha = "↑" if et_lbl == "ASCENSO" else "↓" if et_lbl == "DECLIVE" else "→"
        opts = "".join(
            f'<option value="{v}"{" selected" if v == div_actual else ""}>{lbl}</option>'
            for v, lbl in [(-1, "−1 Retrocede"), (0, "0 Neutro"), (+1, "+1 Avanza")]
        )
        extra = (f' <span style="color:var(--muted);font-size:10px">(base {"+" if base>0 else ""}{base}, '
                 f'{"+" if div_actual>0 else ""}{div_actual*par["factor_div"]:g} divergencia)</span>'
                 ) if div_actual != 0 else ""
        div_filas += f"""<tr>
  <td style="font-size:12px;color:var(--text)">{escape(t.replace('_',' ').title())}</td>
  <td>
    <select name="div_{escape(t)}" data-div-tema="{escape(t)}" form="din-form"
            style="background:var(--bg-3);color:var(--text);border:1px solid #334155;
                   border-radius:4px;padding:3px 8px;font-size:12px">{opts}</select>
  </td>
  <td id="div-out-{escape(t)}" style="font-size:12px">
    <b style="color:{et_color}">{et_flecha} {et_lbl} {"+" if en_tema>0 else ""}{en_tema:g}</b>{extra}
  </td>
</tr>\n"""

    div_tabla = (f"""
  <div style="font-size:11px;font-weight:600;color:var(--accent);margin:14px 0 6px;
              text-transform:uppercase;letter-spacing:.5px">Divergencia por tema</div>
  <p style="font-size:11px;color:var(--muted);margin:0 0 8px">
    Si en un tema concreto el actor avanza o retrocede distinto a su tendencia general,
    ajústalo aquí (±1 = ±{par['factor_div']:g} pts sobre la base).
  </p>
  <table class="tbl"><thead><tr>
    <th>Tema</th><th>Divergencia</th><th>Trayectoria en el tema</th>
  </tr></thead><tbody>{div_filas}</tbody></table>"""
                 if temas_actor else
                 '<p style="font-size:12px;color:var(--muted)">Sin temas vinculados — '
                 'vincula temas al actor para fijar divergencias por tema.</p>')

    return f"""
<div class="card" style="margin-top:0">
  <div class="card-title">Capa 4 — Dinámica (trayectoria de poder)
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      trayectoria = suma de 7 señales (−14..+14) · ASCENSO ≥{par['umbral_ascenso']:g} ·
      DECLIVE ≤{par['umbral_declive']:g}
    </span>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 12px">
    ¿El poder del actor está subiendo, estable o erosionándose? Cada señal va de
    −2 (retrocede fuerte) a +2 (avanza fuerte). Es contexto para el analista — no altera
    el motor, el peso ni el semáforo.
  </p>
  <form id="din-form" method="post" action="/admin/actores/{actor_id}/dinamica"></form>
  <div style="display:flex;align-items:center;gap:16px;margin-bottom:14px;
              padding:10px 14px;background:var(--bg-3);border-radius:8px">
    <div>
      <div style="font-size:9px;color:var(--muted);text-transform:uppercase">Trayectoria base</div>
      <div><span id="din-base-val" style="font-size:24px;font-weight:700;color:var(--text)">{"+" if base>0 else ""}{base}</span></div>
    </div>
    <div id="din-base-lbl" style="font-size:18px;font-weight:700;color:{base_color}">{flecha} {base_lbl}</div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
    <div>{sliders}</div>
    <div>
      {div_tabla}
    </div>
  </div>
  <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
    <button type="submit" form="din-form"
            style="background:var(--accent);color:#000;border:none;border-radius:6px;
                   padding:8px 22px;font-size:13px;font-weight:700;cursor:pointer">
      Guardar dinámica
    </button>
    <input type="text" name="motivo" form="din-form" placeholder="motivo (opcional)"
           style="width:200px;background:var(--bg-3);color:var(--text);
                  border:1px solid #334155;border-radius:4px;padding:6px 10px;font-size:12px">
  </div>
</div>"""


@router.post("/actores/{actor_id:int}/dinamica")
async def admin_actores_dinamica(request: Request, actor_id: int):
    """Guarda las 7 señales dinámicas del actor y las divergencias por tema."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import (
        guardar_dinamica_actor, guardar_divergencia_tema, obtener_actor,
        LockTimeoutError,
    )
    db = _get_db_path()
    actor = obtener_actor(db, actor_id)
    if not actor:
        return RedirectResponse("/admin/actores?err=Actor+no+encontrado", status_code=303)
    form = await request.form()
    senales = {n: int(form.get(n, 0)) for n, _, _ in _DIN_SENALES}
    try:
        base = guardar_dinamica_actor(db, actor_id, senales, sesion["username"])
        for t in actor.get("temas", []):
            campo = f"div_{t}"
            if campo in form:
                guardar_divergencia_tema(db, actor_id, t, int(form.get(campo, 0)))
        return RedirectResponse(
            f"/admin/actores/{actor_id}?msg=Dinámica+guardada+·+trayectoria={base:+d}",
            status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            f"/admin/actores/{actor_id}?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(
            f"/admin/actores/{actor_id}?err={escape(str(e))}", status_code=303)


def _celda_trayectoria(a: dict) -> str:
    """Celda de trayectoria de poder (Capa 4) para la tabla de activación."""
    et = a.get("trayectoria_etiqueta", "ESTABLE")
    en_tema = a.get("trayectoria_en_tema", 0)
    base = a.get("trayectoria_base", 0)
    div = a.get("divergencia_dinamica", 0)
    color = "#22c55e" if et == "ASCENSO" else "#ef4444" if et == "DECLIVE" else "#94a3b8"
    flecha = "↑" if et == "ASCENSO" else "↓" if et == "DECLIVE" else "→"
    val_s = f"{en_tema:+g}"
    if div != 0:
        det = (f'<div style="font-size:9px;color:var(--muted)">'
               f'base {base:+d}, {en_tema - base:+g} div.</div>')
    else:
        det = ""
    return (f'<td><span style="background:{color}22;color:{color};padding:2px 7px;'
            f'border-radius:4px;font-size:11px;font-weight:700;white-space:nowrap">'
            f'{flecha} {et} {val_s}</span>{det}</td>')


@router.get("/actores/activacion", response_class=HTMLResponse)
async def admin_actores_activacion(request: Request):
    """Subpágina: actores ordenados por Índice de Activación para un tema dado."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import listar_actores_por_activacion, NIVELES_ACTOR

    tema_sel = request.query_params.get("tema", list(_IMPACTO_TEMA.keys())[0])
    if tema_sel not in _IMPACTO_TEMA:
        tema_sel = list(_IMPACTO_TEMA.keys())[0]

    db = _get_db_path()
    actores = listar_actores_por_activacion(db, tema_sel)

    # Selector de tema
    opts = "".join(
        f'<option value="{t}"{" selected" if t == tema_sel else ""}>'
        f'{escape(t.replace("_"," ").title())}</option>'
        for t in _IMPACTO_TEMA
    )

    def _color_idx(idx):
        if idx is None: return "#64748b"
        return "#ef4444" if idx >= 70 else "#f97316" if idx >= 50 else "#f59e0b" if idx >= 30 else "#94a3b8"

    def _etiq_idx(idx):
        if idx is None: return "sin datos"
        return "alto" if idx >= 70 else "medio-alto" if idx >= 50 else "medio" if idx >= 30 else "bajo"

    filas = ""
    for a in actores:
        idx    = a.get("indice_activacion")
        idx_s  = f"{idx:.1f}" if idx is not None else "—"
        color  = _color_idx(idx)
        etiq   = _etiq_idx(idx)
        peso   = a.get("peso_calculado", 0)
        c_norm = round(peso / 100, 2)
        v_norm = a.get("v_norm", 0)
        o_norm = a.get("o_norm", 0)
        bar_w  = int(min(100, idx)) if idx is not None else 0
        nivel_nombre = NIVELES_ACTOR.get(a.get("nivel","IV"), ("—",))[0]
        filas += f"""<tr>
  <td>
    <a href="/admin/actores/{a['id']}" style="color:var(--accent);font-weight:600;text-decoration:none">
      {escape(a['nombre'])}
    </a>
    <div style="font-size:10px;color:var(--muted)">Nivel {escape(a.get('nivel','?'))} · {escape(nivel_nombre)}</div>
  </td>
  <td style="text-align:center">
    <span style="font-weight:600;color:var(--text)">{peso:.1f}</span>
    <div style="font-size:10px;color:var(--muted)">C={c_norm:.2f}</div>
  </td>
  <td style="text-align:center;color:#94a3b8">{v_norm:.2f}</td>
  <td style="text-align:center;color:#94a3b8">{o_norm:.2f}</td>
  <td style="text-align:center">
    <span style="font-size:18px;font-weight:700;color:{color}">{idx_s}</span>
    <div style="height:4px;background:#1e293b;border-radius:2px;margin-top:3px;width:60px">
      <div style="height:100%;width:{bar_w}%;background:{color};border-radius:2px"></div>
    </div>
  </td>
  <td>
    <span style="background:{color}22;color:{color};padding:2px 7px;
                 border-radius:4px;font-size:11px;font-weight:600">{etiq}</span>
  </td>
  {_celda_trayectoria(a)}
  <td style="text-align:right">
    <a href="/admin/actores/{a['id']}" style="color:var(--muted);font-size:12px">editar CVO →</a>
  </td>
</tr>\n"""

    if not filas:
        filas = (f'<tr><td colspan="8" style="color:var(--muted);padding:20px;text-align:center">'
                 f'Sin actores vinculados a este tema. '
                 f'<a href="/admin/actores" style="color:var(--accent)">Vincúlalos desde el panel de actores →</a>'
                 f'</td></tr>')

    contenido = f"""
<div style="display:flex;align-items:center;gap:14px;margin-bottom:16px">
  <div style="font-size:15px;font-weight:600;color:var(--text)">Activación por tema:</div>
  <form method="get" action="/admin/actores/activacion" style="display:flex;gap:8px;align-items:center">
    <select name="tema" onchange="this.form.submit()"
            style="background:var(--bg-3);color:var(--text);border:1px solid #334155;
                   border-radius:4px;padding:5px 10px;font-size:13px">
      {opts}
    </select>
  </form>
  <a href="/admin/actores" style="color:var(--muted);font-size:12px;margin-left:auto">← Actores</a>
</div>

<div class="card">
  <div class="card-title">
    {escape(tema_sel.replace('_',' ').title())} — actores por Índice de Activación Estratégica
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      Índice = (C × V × O)^(1/3) × 100 · C = peso/100 · V = voluntad · O = oportunidad
    </span>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 12px 0">
    Arriba: actores que <b>usarán su poder AHORA</b> en este tema (índice alto).
    Abajo: poder estructural alto pero activación baja (quietos en este momento).
    Edita las señales CVO desde la página de cada actor.
  </p>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Actor</th>
        <th style="text-align:center">Peso (C)</th>
        <th style="text-align:center">V (voluntad)</th>
        <th style="text-align:center">O (oportunidad)</th>
        <th style="text-align:center">Índice</th>
        <th>Nivel activación</th>
        <th>Trayectoria</th>
        <th></th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
  <p style="font-size:11px;color:var(--muted);margin-top:8px">
    V = (interés×2 + postura + antecedente) / 20 ·
    O = (ventana×2 + contrapesos + recursos) / 20 ·
    Señales en escala 1-5, default 3 (neutro).
    <a href="/admin/actores/log" style="color:var(--accent)">Ver log de cambios →</a>
  </p>
</div>"""

    return HTMLResponse(_page(
        f"Activación · {tema_sel.replace('_',' ').title()}",
        contenido, "actores", sesion["username"],
    ))


# ══════════════════════════════════════════════════════════════════════════════
# PROYECCIÓN — Capa de futuro (30/60/90 días)
#   A: actividad mediática extrapolada (tendencia pura, sin quiebres)
#   B: gravedad actual + puntos de quiebre (mi criterio, sin tendencia)
# Solo LEE globos_b del semáforo + las tablas de quiebre. No toca el motor.
# ══════════════════════════════════════════════════════════════════════════════

def _cargar_osint_snapshot() -> dict | None:
    """Lee el motor OSINT del snapshot más reciente (mismo origen que el semáforo)."""
    out_dir = Path(OUTPUT_DIR)
    snaps = sorted(out_dir.glob("apurisk_snapshot_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for s in snaps[:1]:
        try:
            data = json.loads(s.read_text(encoding="utf-8"))
            return data.get("osint_motor")
        except Exception:
            pass
    return None


def _temas_datos_desde_globos(db_path: str) -> list:
    """Construye [{tema, actividad, velocidad, gravedad}] desde globos_b del semáforo."""
    osint = _cargar_osint_snapshot()
    if not osint:
        return []
    md = _construir_datos_semaforo(osint, db_path)
    datos = []
    for g in md.get("globos_b", []):
        datos.append({
            "tema": g["tema"],
            "actividad": g.get("x", 0.0),     # % cobertura 7D
            "velocidad": g.get("velocidad", 0.0),
            "gravedad": g.get("y", 0.0),      # eje Y matriz B = max(piso, PA_tema)
        })
    return datos


_NIVEL_PROY_COLOR = [
    (80, "#dc2626"), (65, "#ef4444"), (50, "#f97316"),
    (35, "#f59e0b"), (0, "#94a3b8"),
]


def _color_nivel_proy(v: float) -> str:
    for umbral, color in _NIVEL_PROY_COLOR:
        if v >= umbral:
            return color
    return "#94a3b8"


def _matriz_proyectada_html(proy: dict, h_obj: int,
                            umbral_x: float, umbral_y: float) -> str:
    """Matriz B Proyectada — visual de reporte (solo lectura).

    Lee SOLO datos ya calculados en `proy`:
      · HOY:  X = proyeccion_a[tema].hoy   · Y = proyeccion_b[tema].base
      · {h}d: X = proyeccion_a[tema].h{h}  · Y = proyeccion_b[tema].h{h}
    Dibuja ○ hueco (hoy) → flecha → ● sólido (proyección). Color del ● = gravedad
    proyectada (misma rampa que la Matriz B del presente). No recalcula nada.
    """
    a_por_tema = {r["tema"]: r for r in proy["proyeccion_a"]}
    puntos = []
    max_x = 1.0
    for fb in proy["proyeccion_b"]:
        tema = fb["tema"]
        fa = a_por_tema.get(tema, {})
        x0 = float(fa.get("hoy", 0.0))
        y0 = float(fb.get("base", 0.0))
        x1 = float(fa.get(f"h{h_obj}", x0))
        y1 = float(fb.get(f"h{h_obj}", y0))
        max_x = max(max_x, x0, x1)
        mueve = max(abs(x1 - x0), abs(y1 - y0)) >= 2.0
        puntos.append({
            "tema": tema,
            "label": tema.replace("_", " ").title(),
            "x0": round(x0, 1), "y0": round(y0, 1),
            "x1": round(x1, 1), "y1": round(y1, 1),
            "color": _color_nivel_proy(y1),
            "mueve": mueve,
        })
    # Eje X dinámico: máximo real + margen, con tope mínimo legible
    x_max = max(20.0, round(max_x * 1.25 + 2))
    puntos_json = json.dumps(puntos, ensure_ascii=False)
    cid = "matrizProyectada"

    return f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<div class="card">
  <div style="margin-bottom:8px">
    <span style="font-size:11px;font-weight:700;color:#f59e0b;background:#f59e0b22;
                 padding:3px 8px;border-radius:4px">PIEZA DE REPORTE · SOLO LECTURA</span>
  </div>
  <div class="card-title">Matriz B Proyectada — movimiento hoy → {h_obj}d
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      misma matriz del presente (Y = gravedad · X = actividad), proyectada a {h_obj} días
    </span>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 6px">
    <span style="color:#94a3b8">○ hoy</span> →
    <span style="color:#ef4444;font-weight:700">● proyección {h_obj}d</span> ·
    color = gravedad · la flecha aparece solo donde hay movimiento real.
  </p>
  <div style="position:relative;height:440px">
    <canvas id="{cid}"></canvas>
  </div>
</div>
<script>
(function() {{
  var P = {puntos_json};
  var X_MAX = {x_max}, UMBRAL_X = {umbral_x}, UMBRAL_Y = {umbral_y};
  if (!window.Chart) return;
  var el = document.getElementById('{cid}');
  if (!el) return;

  // Dataset real = burbujas proyectadas (sólidas) → habilita tooltips nativos.
  var solid = P.map(function(p) {{
    return {{ x: p.x1, y: p.y1, _p: p }};
  }});

  // Plugin INLINE (solo este canvas): líneas guía, ○ hoy, flechas, etiquetas dispersas.
  var draw = {{
    id: 'proy_{cid}',
    afterDraw: function(chart) {{
      var ctx = chart.ctx, ca = chart.chartArea;
      var xs = chart.scales.x, ys = chart.scales.y;
      ctx.save();
      // ── Líneas guía de umbral (sutiles, punteadas) ──
      ctx.strokeStyle = '#334155'; ctx.setLineDash([4,4]); ctx.lineWidth = 1;
      var xm = xs.getPixelForValue(UMBRAL_X), ym = ys.getPixelForValue(UMBRAL_Y);
      ctx.beginPath(); ctx.moveTo(xm, ca.top); ctx.lineTo(xm, ca.bottom); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(ca.left, ym); ctx.lineTo(ca.right, ym); ctx.stroke();
      ctx.setLineDash([]);

      var R = 8, R0 = 6;
      // ── ○ hoy + flecha hoy→proyección ──
      P.forEach(function(p) {{
        var x0 = xs.getPixelForValue(p.x0), y0 = ys.getPixelForValue(p.y0);
        var x1 = xs.getPixelForValue(p.x1), y1 = ys.getPixelForValue(p.y1);
        if (p.mueve) {{
          var ang = Math.atan2(y1 - y0, x1 - x0);
          // flecha desde el borde del ○ hasta el borde del ●
          var sx = x0 + Math.cos(ang) * R0, sy = y0 + Math.sin(ang) * R0;
          var ex = x1 - Math.cos(ang) * R,  ey = y1 - Math.sin(ang) * R;
          ctx.strokeStyle = '#64748b'; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke();
          // punta
          var ah = 5;
          ctx.fillStyle = '#64748b';
          ctx.beginPath();
          ctx.moveTo(ex, ey);
          ctx.lineTo(ex - ah*Math.cos(ang - 0.4), ey - ah*Math.sin(ang - 0.4));
          ctx.lineTo(ex - ah*Math.cos(ang + 0.4), ey - ah*Math.sin(ang + 0.4));
          ctx.closePath(); ctx.fill();
        }}
        // ○ hueco (hoy): gris neutro, sin relleno fuerte
        ctx.beginPath(); ctx.arc(x0, y0, R0, 0, 2*Math.PI);
        ctx.fillStyle = 'rgba(8,14,26,0.6)'; ctx.fill();
        ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.5; ctx.stroke();
      }});

      // ── Etiquetas con dispersión vertical + línea-guía ──
      var items = P.map(function(p) {{
        return {{
          p: p,
          bx: xs.getPixelForValue(p.x1),
          by: ys.getPixelForValue(p.y1),
          ly: ys.getPixelForValue(p.y1),
        }};
      }});
      items.sort(function(a, b) {{ return a.ly - b.ly; }});
      var GAP = 15;
      // empuje hacia abajo para separar
      for (var i = 1; i < items.length; i++) {{
        if (items[i].ly - items[i-1].ly < GAP) items[i].ly = items[i-1].ly + GAP;
      }}
      // si se desbordó por abajo, reparte hacia arriba
      var bottom = ca.bottom - 4;
      for (var j = items.length - 1; j > 0; j--) {{
        if (items[j].ly > bottom) items[j].ly = bottom;
        if (items[j].ly - items[j-1].ly < GAP) items[j-1].ly = items[j].ly - GAP;
      }}
      ctx.font = '600 11px sans-serif';
      ctx.textBaseline = 'middle';
      items.forEach(function(it) {{
        var lx = it.bx + R + 7;
        var tw = ctx.measureText(it.p.label).width;
        if (lx + tw + 6 > ca.right) lx = it.bx - R - 7 - tw;  // si no cabe, a la izquierda
        var ha = (lx < it.bx) ? 'left' : 'left';
        // línea-guía si la etiqueta se desplazó de su burbuja
        if (Math.abs(it.ly - it.by) > 2) {{
          ctx.strokeStyle = 'rgba(100,116,139,0.5)'; ctx.lineWidth = 0.8;
          ctx.beginPath();
          ctx.moveTo(it.bx + (lx < it.bx ? -R : R), it.by);
          ctx.lineTo(lx - (lx < it.bx ? -3 : 3), it.ly);
          ctx.stroke();
        }}
        // píldora de fondo
        var px = lx - 3, py = it.ly - 8;
        ctx.fillStyle = 'rgba(8,14,26,0.85)';
        ctx.fillRect(px, py, tw + 6, 16);
        ctx.fillStyle = '#e2e8f0';
        ctx.textAlign = 'left';
        ctx.fillText(it.p.label, lx, it.ly);
      }});
      ctx.textBaseline = 'alphabetic';
      ctx.restore();
    }}
  }};

  new window.Chart(el.getContext('2d'), {{
    type: 'bubble',
    data: {{ datasets: [{{
      data: solid.map(function(s) {{ return {{ x: s.x, y: s.y, r: 8 }}; }}),
      backgroundColor: P.map(function(p) {{ return p.color; }}),
      borderColor: P.map(function(p) {{ return p.color; }}),
      borderWidth: 1,
      _pts: P,
    }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      layout: {{ padding: {{ right: 90 }} }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{
          title: function(items) {{ return items[0].chart.data.datasets[0]._pts[items[0].dataIndex].label; }},
          label: function(item) {{
            var p = item.chart.data.datasets[0]._pts[item.dataIndex];
            return ['hoy: act ' + p.x0 + ' · grav ' + p.y0,
                    '{h_obj}d: act ' + p.x1 + ' · grav ' + p.y1];
          }}
        }} }}
      }},
      scales: {{
        x: {{ min: 0, max: X_MAX,
             title: {{ display: true, text: 'Actividad mediática', color: '#94a3b8',
                       font: {{ size: 10, weight: '600' }} }},
             grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }},
        y: {{ min: 0, max: 100,
             title: {{ display: true, text: 'Gravedad', color: '#94a3b8',
                       font: {{ size: 10, weight: '600' }} }},
             grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8' }} }}
      }}
    }},
    plugins: [draw]
  }});
}})();
</script>"""


@router.get("/proyeccion", response_class=HTMLResponse)
async def admin_proyeccion(request: Request):
    """Vista de proyección: dos secciones apiladas (A actividad, B gravedad)."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import (
        calcular_proyecciones, factor_tendencia, cargar_parametros_semaforo,
    )

    db = _get_db_path()
    temas_datos = _temas_datos_desde_globos(db)

    if not temas_datos:
        contenido = """
<div class="card card-accent">
  <div class="card-title">🔮 Proyección 30/60/90</div>
  <p style="color:var(--muted)">Sin snapshot OSINT disponible aún. La proyección
  lee los mismos datos del semáforo; espera el próximo ciclo del motor.</p>
  <a href="/admin/quiebres" style="color:var(--accent)">Gestionar puntos de quiebre →</a>
</div>"""
        return HTMLResponse(_page("Proyección", contenido, "proyeccion", sesion["username"]))

    proy = calcular_proyecciones(db, temas_datos)
    par = proy["par"]
    horizontes = proy["horizontes"]
    h_ult = horizontes[-1]   # horizonte más lejano (para ordenar y la flecha de tendencia)
    factores_str = " / ".join(f"{factor_tendencia(h, par):.1f}" for h in horizontes)
    horizontes_str = "/".join(str(h) for h in horizontes)
    th_horizontes = "".join(
        f'<th style="text-align:center">{h}d</th>' for h in horizontes)

    # Matriz B Proyectada (visual): horizonte objetivo = 30d si existe, si no el último.
    h_obj = 30 if 30 in horizontes else h_ult
    sem = cargar_parametros_semaforo(db)
    matriz_html = _matriz_proyectada_html(
        proy, h_obj, sem.get("umbral_x", 25.0), sem.get("umbral_y", 65.0))

    def _tn(t): return escape(t.replace("_", " ").title())

    # ── Sección A — Actividad (tendencia pura) ──
    filas_a = ""
    for fa in sorted(proy["proyeccion_a"], key=lambda r: r[f"h{h_ult}"], reverse=True):
        cells = ""
        for h in horizontes:
            v = fa[f"h{h}"]
            cells += (f'<td style="text-align:center;font-weight:700;'
                      f'color:{_color_nivel_proy(v)}">{v:.1f}</td>')
        delta = fa[f"h{h_ult}"] - fa["hoy"]
        flecha = "↑" if delta > 0.5 else "↓" if delta < -0.5 else "→"
        fcol = "#22c55e" if delta > 0.5 else "#ef4444" if delta < -0.5 else "#94a3b8"
        filas_a += f"""<tr>
  <td><span style="color:{fcol};font-weight:700">{flecha}</span> {_tn(fa['tema'])}</td>
  <td style="text-align:center;color:var(--muted)">{fa['hoy']:.1f}</td>
  {cells}
</tr>\n"""

    # ── Sección B — Gravedad (base + quiebres), con estado honesto por celda ──
    filas_b = ""
    for fb in sorted(proy["proyeccion_b"], key=lambda r: r[f"h{h_ult}"], reverse=True):
        cells = ""
        for h in horizontes:
            ef = fb["efectos"][h]
            total = ef["total"]
            qpts = ef["quiebre"]
            estado = ef["estado"]
            color = _color_nivel_proy(total)
            if estado == "aplicado":
                qcol = "#22c55e" if qpts > 0 else "#ef4444"
                nota = (f'base {ef["base"]:.0f} '
                        f'<span style="color:{qcol};font-weight:700">{qpts:+.0f} quiebre</span>')
            elif estado == "diluido":
                nota = ('<span style="color:#f59e0b">efecto temporal diluido (≈0 aquí)</span>')
            elif estado == "fuera_horizonte":
                nota = ('<span style="color:#64748b">efecto definido · el quiebre cae fuera de este horizonte</span>')
            else:  # sin_efecto
                nota = "sin efecto definido"
            desglose = f'<div style="font-size:9px;color:var(--muted)">{nota}</div>'
            cells += (f'<td style="text-align:center">'
                      f'<span style="font-weight:700;color:{color}">{total:.1f}</span>'
                      f'{desglose}</td>')
        filas_b += f"""<tr>
  <td>{_tn(fb['tema'])}</td>
  <td style="text-align:center;color:var(--muted)">{fb['base']:.1f}</td>
  {cells}
</tr>\n"""

    # Resumen de quiebres activos que afectan B
    q_activos = proy["quiebres"]
    chips = ""
    for q in q_activos:
        dh = q.get("dias_hasta")
        cuando = (f"en {dh}d" if dh is not None and dh >= 0
                  else f"hace {abs(dh)}d" if dh is not None else "—")
        n_ef = sum(1 for e in q["efectos"].values() if e["direccion"] != "no_toca")
        chips += (f'<a href="/admin/quiebres/{q["id"]}" '
                  f'style="display:inline-block;background:var(--bg-3);border:1px solid #334155;'
                  f'border-radius:6px;padding:4px 10px;font-size:12px;margin:2px;color:var(--text)">'
                  f'🔻 {escape(q["nombre"])} <span style="color:var(--muted)">'
                  f'({escape(q.get("fecha",""))} · {cuando} · {n_ef} temas)</span></a>')
    if not chips:
        chips = ('<span style="color:var(--muted);font-size:12px">Sin quiebres activos. '
                 '<a href="/admin/quiebres/nuevo" style="color:var(--accent)">Crea el primero →</a>'
                 ' La Proyección B mostrará solo la gravedad estructural actual.</span>')

    contenido = f"""
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
  <div style="font-size:13px;color:var(--muted)">
    Hoy {escape(proy['hoy'])} · horizontes {escape(horizontes_str)}d ·
    factores de tendencia = {escape(factores_str)}
  </div>
  <a href="/admin/quiebres"
     style="background:var(--accent);color:#000;border-radius:6px;padding:7px 18px;
            font-size:13px;font-weight:600">Gestionar quiebres →</a>
</div>

{matriz_html}

<div class="card">
  <div style="margin-bottom:8px">
    <span style="font-size:11px;font-weight:700;color:#22c55e;background:#22c55e22;
                 padding:3px 8px;border-radius:4px">TENDENCIA AUTOMÁTICA</span>
  </div>
  <div class="card-title">Proyección A — Actividad mediática futura
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      extrapolación de la cobertura por velocidad 7d · amortiguada · acotada 0-100
    </span>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 12px">
    <b>Todo aquí es tendencia automática.</b> Los puntos de quiebre NO afectan esta
    matriz. nivel(H) = actividad_hoy + velocidad × factor(H), donde la velocidad pesa
    menos a mayor horizonte (factor {escape(factores_str)} a {escape(horizontes_str)}d).
  </p>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Tema</th><th style="text-align:center">Hoy</th>
        {th_horizontes}
      </tr></thead>
      <tbody>{filas_a}</tbody>
    </table>
  </div>
</div>

<div class="card">
  <div style="margin-bottom:8px">
    <span style="font-size:11px;font-weight:700;color:#f59e0b;background:#f59e0b22;
                 padding:3px 8px;border-radius:4px">GRAVEDAD ESTRUCTURAL + TUS QUIEBRES</span>
  </div>
  <div class="card-title">Proyección B — Gravedad / riesgo futuro
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      gravedad actual (eje Y de la Matriz B) ajustada por puntos de quiebre
    </span>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 10px">
    La gravedad <b>no se extrapola por cobertura</b>: parte de la gravedad estructural de
    hoy y solo se mueve por los eventos que tú defines. Cada celda separa
    <b>base</b> (estructural) de <b>quiebre</b> (tu ajuste). Un efecto temporal que ya
    se diluyó se marca como tal — nunca como "sin efecto".
  </p>
  <div style="margin-bottom:12px">{chips}</div>
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Tema</th><th style="text-align:center">Gravedad hoy</th>
        {th_horizontes}
      </tr></thead>
      <tbody>{filas_b}</tbody>
    </table>
  </div>
  <p style="font-size:11px;color:var(--muted);margin-top:8px">
    Intensidad → puntos: leve {par['pts_leve']:.0f} · moderado {par['pts_moderado']:.0f} ·
    fuerte {par['pts_fuerte']:.0f}. Efecto temporal se diluye en
    {par['dilucion_dias']:.0f} días. Horizontes y pesos editables en calibración.
  </p>
</div>"""

    return HTMLResponse(_page("Proyección", contenido, "proyeccion", sesion["username"]))


# ── Gestión de puntos de quiebre ──────────────────────────────────────────────

def _quiebre_efectos_form(efectos: dict) -> str:
    """Tabla editable de efectos por tema (una fila por tema del sistema)."""
    filas = ""
    for tema in _IMPACTO_TEMA:
        ef = efectos.get(tema, {})
        dir_act = ef.get("direccion", "no_toca")
        int_act = ef.get("intensidad", "moderado")
        dur_act = ef.get("duracion", "permanente")
        def _sel(name, val, opciones):
            o = "".join(
                f'<option value="{v}"{" selected" if v == val else ""}>{lbl}</option>'
                for v, lbl in opciones)
            return (f'<select name="{name}_{escape(tema)}" form="efectos-form" '
                    f'style="background:var(--bg-3);color:var(--text);border:1px solid #334155;'
                    f'border-radius:4px;padding:3px 6px;font-size:12px">{o}</select>')
        filas += f"""<tr>
  <td style="font-size:12px;color:var(--text)">{escape(tema.replace('_',' ').title())}</td>
  <td>{_sel('dir', dir_act, [('no_toca','no toca'),('sube','sube'),('baja','baja')])}</td>
  <td>{_sel('int', int_act, [('leve','leve'),('moderado','moderado'),('fuerte','fuerte')])}</td>
  <td>{_sel('dur', dur_act, [('permanente','permanente'),('temporal','temporal')])}</td>
</tr>\n"""
    return f"""
<table class="tbl">
  <thead><tr>
    <th>Tema</th><th>Dirección</th><th>Intensidad</th><th>Duración</th>
  </tr></thead>
  <tbody>{filas}</tbody>
</table>"""


@router.get("/quiebres", response_class=HTMLResponse)
async def admin_quiebres(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import listar_puntos_quiebre
    db = _get_db_path()
    quiebres = listar_puntos_quiebre(db, solo_activos=False, con_efectos=True)

    msg = request.query_params.get("msg", "")
    err_msg = request.query_params.get("err", "")
    msg_html = (f'<div class="alert-box alert-info" style="margin-bottom:12px">✓ {escape(msg)}</div>'
                ) if msg else ""
    err_html = (f'<div class="alert-box alert-alto" style="margin-bottom:12px">⚠ {escape(err_msg)}</div>'
                ) if err_msg else ""

    filas = ""
    for q in quiebres:
        n_ef = sum(1 for e in q["efectos"].values() if e["direccion"] != "no_toca")
        estado = ('<span class="badge badge-ok">activo</span>' if q["activo"]
                  else '<span class="badge badge-off">inactivo</span>')
        filas += f"""<tr>
  <td><a href="/admin/quiebres/{q['id']}" style="color:var(--accent);font-weight:600;text-decoration:none">{escape(q['nombre'])}</a></td>
  <td style="color:var(--muted);font-size:12px">{escape(q.get('fecha',''))}</td>
  <td style="text-align:center">{n_ef}</td>
  <td>{estado}</td>
  <td style="text-align:right"><a href="/admin/quiebres/{q['id']}" style="color:var(--muted);font-size:12px">editar →</a></td>
</tr>\n"""
    if not filas:
        filas = ('<tr><td colspan="5" style="color:var(--muted);padding:20px;text-align:center">'
                 'Sin puntos de quiebre. <a href="/admin/quiebres/nuevo" style="color:var(--accent)">'
                 'Crea el primero →</a></td></tr>')

    contenido = f"""
{msg_html}{err_html}
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
  <div style="font-size:13px;color:var(--muted)">
    {len(quiebres)} punto{'s' if len(quiebres)!=1 else ''} de quiebre · afectan solo la Proyección B (gravedad)
  </div>
  <div style="display:flex;gap:8px">
    <a href="/admin/proyeccion" style="background:var(--bg-3);color:var(--muted);border:1px solid #334155;border-radius:6px;padding:6px 14px;font-size:12px">← Proyección</a>
    <a href="/admin/quiebres/log" style="background:var(--bg-3);color:var(--muted);border:1px solid #334155;border-radius:6px;padding:6px 14px;font-size:12px">Historial →</a>
    <a href="/admin/quiebres/nuevo" style="background:var(--accent);color:#000;border-radius:6px;padding:7px 18px;font-size:13px;font-weight:600">+ Nuevo quiebre</a>
  </div>
</div>
<div class="card">
  <div style="overflow-x:auto">
    <table class="tbl">
      <thead><tr>
        <th>Nombre</th><th>Fecha</th><th style="text-align:center">Temas afectados</th>
        <th>Estado</th><th></th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
</div>"""
    return HTMLResponse(_page("Puntos de quiebre", contenido, "quiebres", sesion["username"]))


@router.get("/quiebres/nuevo", response_class=HTMLResponse)
async def admin_quiebres_nuevo(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    err_msg = request.query_params.get("err", "")
    err_html = (f'<div class="alert-box alert-alto" style="margin-bottom:12px">⚠ {escape(err_msg)}</div>'
                ) if err_msg else ""
    contenido = f"""
{err_html}
<div class="card">
  <div class="card-title">Nuevo punto de quiebre</div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 14px">
    Un evento futuro que la tendencia mediática no puede prever. Primero defines
    nombre y fecha; luego, al editarlo, fijas sus efectos por tema.
  </p>
  <form method="post" action="/admin/quiebres/nuevo">
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Nombre</label>
      <input type="text" name="nombre" required placeholder="ej. Cambio de gobierno"
             style="width:100%;max-width:420px;background:var(--bg-3);color:var(--text);border:1px solid #334155;border-radius:4px;padding:7px 10px;font-size:14px">
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Fecha (cuándo ocurre)</label>
      <input type="date" name="fecha" required
             style="background:var(--bg-3);color:var(--text);border:1px solid #334155;border-radius:4px;padding:7px 10px;font-size:14px">
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Notas (opcional)</label>
      <textarea name="notas" rows="2" style="width:100%;max-width:420px;background:var(--bg-3);color:var(--text);border:1px solid #334155;border-radius:4px;padding:7px 10px;font-size:13px"></textarea>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <button type="submit" style="background:var(--accent);color:#000;border:none;border-radius:6px;padding:9px 22px;font-size:14px;font-weight:700;cursor:pointer">Crear y definir efectos</button>
      <a href="/admin/quiebres" style="color:var(--muted);font-size:13px">Cancelar</a>
    </div>
  </form>
</div>"""
    return HTMLResponse(_page("Nuevo quiebre", contenido, "quiebres", sesion["username"]))


@router.post("/quiebres/nuevo")
async def admin_quiebres_nuevo_post(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import crear_punto_quiebre, LockTimeoutError
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    fecha = (form.get("fecha") or "").strip()
    if not nombre or not fecha:
        return RedirectResponse("/admin/quiebres/nuevo?err=Nombre+y+fecha+son+obligatorios", status_code=303)
    try:
        r = crear_punto_quiebre(_get_db_path(), {
            "nombre": nombre, "fecha": fecha, "notas": form.get("notas", "").strip() or None,
        }, usuario=sesion["username"])
        return RedirectResponse(f"/admin/quiebres/{r['id']}?msg=Quiebre+creado+·+ahora+define+sus+efectos", status_code=303)
    except LockTimeoutError:
        return RedirectResponse("/admin/quiebres/nuevo?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/quiebres/nuevo?err={escape(str(e))}", status_code=303)


@router.get("/quiebres/log", response_class=HTMLResponse)
async def admin_quiebres_log(request: Request):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import listar_log_quiebres
    logs = listar_log_quiebres(_get_db_path(), limite=200)
    filas = ""
    for l in logs:
        ts = (l.get("cambiado_en") or "")[:19].replace("T", " ")
        filas += f"""<tr>
  <td style="color:var(--muted);font-size:11px;white-space:nowrap">{ts}</td>
  <td style="color:var(--accent);font-size:12px">{escape(l.get('quiebre_nombre') or '—')}</td>
  <td><span class="badge badge-off">{escape(l.get('campo') or '—')}</span></td>
  <td style="color:var(--muted);font-size:12px">{escape(str(l.get('valor_anterior') or '—'))} → <b style="color:var(--text)">{escape(str(l.get('valor_nuevo') or '—'))}</b></td>
  <td style="color:var(--accent);font-size:11px">{escape(l.get('usuario') or '—')}</td>
  <td style="color:var(--muted);font-size:11px">{escape(l.get('motivo') or '—')}</td>
</tr>\n"""
    cuerpo = filas if logs else '<tr><td colspan="6" style="color:var(--muted);padding:16px">Sin cambios registrados.</td></tr>'
    contenido = f"""
<div class="alert-box alert-info">Registro de cambios en puntos de quiebre.
  <a href="/admin/quiebres" style="color:var(--accent)">← Volver</a></div>
<div class="card">
  <div class="card-title">Historial — config_quiebre_log ({len(logs)})</div>
  <div style="overflow-x:auto"><table class="tbl">
    <thead><tr><th>Fecha</th><th>Quiebre</th><th>Campo</th><th>Cambio</th><th>Usuario</th><th>Motivo</th></tr></thead>
    <tbody>{cuerpo}</tbody>
  </table></div>
</div>"""
    return HTMLResponse(_page("Log quiebres", contenido, "quiebres", sesion["username"]))


@router.get("/quiebres/{quiebre_id:int}", response_class=HTMLResponse)
async def admin_quiebres_detalle(request: Request, quiebre_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import obtener_punto_quiebre, listar_log_quiebres
    db = _get_db_path()
    q = obtener_punto_quiebre(db, quiebre_id)
    if not q:
        return RedirectResponse("/admin/quiebres?err=Quiebre+no+encontrado", status_code=303)
    logs = listar_log_quiebres(db, quiebre_id, limite=20)

    msg = request.query_params.get("msg", "")
    err_msg = request.query_params.get("err", "")
    msg_html = (f'<div class="alert-box alert-info" style="margin-bottom:12px">✓ {escape(msg)}</div>') if msg else ""
    err_html = (f'<div class="alert-box alert-alto" style="margin-bottom:12px">⚠ {escape(err_msg)}</div>') if err_msg else ""

    estado_badge = ('<span class="badge badge-ok">activo</span>' if q["activo"]
                    else '<span class="badge badge-off">inactivo</span>')
    efectos_form = _quiebre_efectos_form(q["efectos"])

    filas_log = ""
    for l in logs:
        ts = (l.get("cambiado_en") or "")[:19].replace("T", " ")
        filas_log += f"""<tr>
  <td style="color:var(--muted);font-size:11px;white-space:nowrap">{ts}</td>
  <td><span class="badge badge-off">{escape(l.get('campo') or '—')}</span></td>
  <td style="color:var(--muted);font-size:12px">{escape(str(l.get('valor_nuevo') or '—'))}</td>
  <td style="color:var(--accent);font-size:11px">{escape(l.get('usuario') or '—')}</td>
</tr>\n"""
    if not filas_log:
        filas_log = '<tr><td colspan="4" style="color:var(--muted);padding:12px">Sin cambios.</td></tr>'

    contenido = f"""
{msg_html}{err_html}
<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
  <div>
    <h2 style="margin:0;font-size:20px">{escape(q['nombre'])}</h2>
    <div style="color:var(--muted);font-size:12px;margin-top:2px">{escape(q.get('fecha',''))} · {estado_badge}</div>
  </div>
  <div style="margin-left:auto;display:flex;gap:8px">
    <form method="post" action="/admin/quiebres/{quiebre_id}/toggle" style="display:inline">
      <button type="submit" style="background:var(--bg-3);color:var(--muted);border:1px solid #334155;border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer">{'Desactivar' if q['activo'] else 'Activar'}</button>
    </form>
    <form method="post" action="/admin/quiebres/{quiebre_id}/eliminar" style="display:inline"
          onsubmit="return confirm('¿Eliminar este punto de quiebre y todos sus efectos?')">
      <button type="submit" style="background:var(--bg-3);color:#ef4444;border:1px solid #7f1d1d;border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer">Eliminar</button>
    </form>
    <a href="/admin/quiebres" style="color:var(--muted);font-size:12px;padding:6px 14px">← Lista</a>
  </div>
</div>

<div class="card">
  <div class="card-title">Datos del quiebre</div>
  <form method="post" action="/admin/quiebres/{quiebre_id}/editar">
    <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end">
      <div>
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Nombre</label>
        <input type="text" name="nombre" value="{escape(q['nombre'])}" required style="background:var(--bg-3);color:var(--text);border:1px solid #334155;border-radius:4px;padding:7px 10px;font-size:14px;width:260px">
      </div>
      <div>
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Fecha</label>
        <input type="date" name="fecha" value="{escape(q.get('fecha','')[:10])}" required style="background:var(--bg-3);color:var(--text);border:1px solid #334155;border-radius:4px;padding:7px 10px;font-size:14px">
      </div>
      <div style="flex:1;min-width:200px">
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">Notas</label>
        <input type="text" name="notas" value="{escape(q.get('notas') or '')}" style="width:100%;background:var(--bg-3);color:var(--text);border:1px solid #334155;border-radius:4px;padding:7px 10px;font-size:13px">
      </div>
      <button type="submit" style="background:var(--accent);color:#000;border:none;border-radius:6px;padding:9px 20px;font-size:13px;font-weight:700;cursor:pointer">Guardar datos</button>
    </div>
  </form>
</div>

<div class="card">
  <div class="card-title">Efectos por tema (Proyección B)
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">
      cómo este evento mueve la gravedad de cada tema
    </span>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:0 0 12px">
    <b>no toca</b> = el tema sigue su gravedad estructural actual.
    <b>sube/baja</b> + intensidad (leve/moderado/fuerte) = puntos.
    <b>permanente</b> se mantiene; <b>temporal</b> se diluye tras la fecha.
  </p>
  <form id="efectos-form" method="post" action="/admin/quiebres/{quiebre_id}/efectos"></form>
  {efectos_form}
  <div style="margin-top:14px">
    <button type="submit" form="efectos-form" style="background:var(--accent);color:#000;border:none;border-radius:6px;padding:9px 22px;font-size:14px;font-weight:700;cursor:pointer">Guardar efectos</button>
  </div>
</div>

<div class="card" style="margin-top:0">
  <div class="card-title">Historial (últimos 20)</div>
  <div style="overflow-x:auto"><table class="tbl">
    <thead><tr><th>Fecha</th><th>Campo</th><th>Valor</th><th>Usuario</th></tr></thead>
    <tbody>{filas_log}</tbody>
  </table></div>
</div>"""
    return HTMLResponse(_page(f"Quiebre · {q['nombre']}", contenido, "quiebres", sesion["username"]))


@router.post("/quiebres/{quiebre_id:int}/editar")
async def admin_quiebres_editar(request: Request, quiebre_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import actualizar_punto_quiebre, LockTimeoutError
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    fecha = (form.get("fecha") or "").strip()
    if not nombre or not fecha:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err=Nombre+y+fecha+obligatorios", status_code=303)
    try:
        actualizar_punto_quiebre(_get_db_path(), quiebre_id, {
            "nombre": nombre, "fecha": fecha, "notas": form.get("notas", "").strip() or None,
        }, usuario=sesion["username"])
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?msg=Datos+guardados", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err={escape(str(e))}", status_code=303)


@router.post("/quiebres/{quiebre_id:int}/efectos")
async def admin_quiebres_efectos(request: Request, quiebre_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import guardar_efectos_quiebre, LockTimeoutError
    form = await request.form()
    efectos = {}
    for tema in _IMPACTO_TEMA:
        efectos[tema] = {
            "direccion": form.get(f"dir_{tema}", "no_toca"),
            "intensidad": form.get(f"int_{tema}", "moderado"),
            "duracion": form.get(f"dur_{tema}", "permanente"),
        }
    try:
        r = guardar_efectos_quiebre(_get_db_path(), quiebre_id, efectos, usuario=sesion["username"])
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?msg=Efectos+guardados+·+{r['n']}+temas", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err={escape(str(e))}", status_code=303)


@router.post("/quiebres/{quiebre_id:int}/toggle")
async def admin_quiebres_toggle(request: Request, quiebre_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import toggle_punto_quiebre, LockTimeoutError
    try:
        toggle_punto_quiebre(_get_db_path(), quiebre_id, usuario=sesion["username"])
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?msg=Estado+actualizado", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err={escape(str(e))}", status_code=303)


@router.post("/quiebres/{quiebre_id:int}/eliminar")
async def admin_quiebres_eliminar(request: Request, quiebre_id: int):
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import eliminar_punto_quiebre, LockTimeoutError
    try:
        eliminar_punto_quiebre(_get_db_path(), quiebre_id, usuario=sesion["username"])
        return RedirectResponse("/admin/quiebres?msg=Quiebre+eliminado", status_code=303)
    except LockTimeoutError:
        return RedirectResponse(f"/admin/quiebres/{quiebre_id}?err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/quiebres?err={escape(str(e))}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MOTOR DE INTELIGENCIA — análisis de 7 pasos por tema (Reportes A/B)
# Etapa 1: pasos AUTOMÁTICOS (2,3,7) = Reporte A. Solo LEE datos existentes.
# ══════════════════════════════════════════════════════════════════════════════

def _reporte_automatico(db: str, tema: str) -> dict | None:
    """Arma los pasos automáticos (2,3,7) del análisis de un tema.

    Lee de funciones existentes — sin recálculo nuevo:
      · Paso 2 (coyuntura): globos_b del semáforo (gravedad, actividad, velocidad, urgencia)
      · Paso 3 (actores):   listar_actores_por_activacion (CVO + trayectoria)
      · Paso 7 (proyección): calcular_proyecciones (A/B a 30d + quiebres del tema)
    Devuelve None si no hay snapshot OSINT.
    """
    from ..storage.config_loader import (
        listar_actores_por_activacion, calcular_proyecciones,
        cargar_factores_pxi_por_tema,
    )
    osint = _cargar_osint_snapshot()
    if not osint:
        return None
    md = _construir_datos_semaforo(osint, db)

    # ── Paso 2 (NUEVO) — Factores de riesgo (Matriz P×I) ──
    # Lectura pura de la tabla `factores` del último snapshot, filtrada por
    # categoría que mapea al tema. Sin recálculo.
    pxi = cargar_factores_pxi_por_tema(db, tema)

    # ── Paso 2 — Evento / coyuntura ──
    globo = next((g for g in md.get("globos_b", []) if g["tema"] == tema), None)
    paso2 = None
    if globo:
        paso2 = {
            "gravedad": globo.get("y", 0.0),
            "actividad": globo.get("x", 0.0),
            "velocidad": globo.get("velocidad", 0.0),
            "urgencia": globo.get("urgencia", "—"),
            "color": globo.get("color", "#94a3b8"),
            "cuadrante": globo.get("cuadrante", "—"),
            "indice_urgencia": globo.get("indice_urgencia", 0.0),
            "actor_determinante": globo.get("actor_determinante"),
        }

    # ── Paso 5 (propuesta automática del híbrido) — sustancia vs ruido ──
    # Reutiliza los cuadrantes de la Matriz B: sustancia = temas graves
    # estructurales (Y ≥ umbral_y); ruido = activos en cobertura pero no graves.
    umbral_y = md.get("umbral_y", 65.0)
    umbral_x = md.get("umbral_x", 25.0)
    sustancia_items, ruido_items = [], []
    for g in md.get("globos_b", []):
        nom = g["tema"].replace("_", " ").title()
        if g.get("y", 0) >= umbral_y:
            sustancia_items.append(f"{nom} (gravedad {g.get('y',0):.0f})")
        elif g.get("x", 0) >= umbral_x:
            ruido_items.append(f"{nom} (actividad {g.get('x',0):.1f}, gravedad {g.get('y',0):.0f})")
    propuesta_paso5 = {
        "sustancia": "; ".join(sustancia_items) if sustancia_items else "(sin temas graves estructurales)",
        "ruido": "; ".join(ruido_items) if ruido_items else "(sin temas activos no-graves)",
    }

    # ── Paso 3 — Actores e intereses ──
    paso3 = listar_actores_por_activacion(db, tema)

    # ── Paso 7 — Proyecciones (hoy → 30d) ──
    temas_datos = _temas_datos_desde_globos(db)
    proy = calcular_proyecciones(db, temas_datos)
    h_obj = 30 if 30 in proy["horizontes"] else proy["horizontes"][-1]
    pa = next((r for r in proy["proyeccion_a"] if r["tema"] == tema), None)
    pb = next((r for r in proy["proyeccion_b"] if r["tema"] == tema), None)
    quiebres_tema = []
    for q in proy.get("quiebres", []):
        ef = q.get("efectos", {}).get(tema)
        if ef and ef.get("direccion", "no_toca") != "no_toca":
            quiebres_tema.append({
                "nombre": q["nombre"], "fecha": q.get("fecha", ""),
                "direccion": ef["direccion"], "intensidad": ef["intensidad"],
                "duracion": ef["duracion"], "dias_hasta": q.get("dias_hasta"),
            })
    paso7 = {
        "h_obj": h_obj,
        "actividad_hoy": pa["hoy"] if pa else 0.0,
        "actividad_30d": pa.get(f"h{h_obj}") if pa else 0.0,
        "gravedad_hoy": pb["base"] if pb else 0.0,
        "gravedad_30d": pb.get(f"h{h_obj}") if pb else 0.0,
        "efecto_quiebre": (pb["efectos"][h_obj]["quiebre"] if pb else 0.0),
        "quiebres": quiebres_tema,
    }

    return {"tema": tema, "pxi": pxi, "paso2": paso2, "paso3": paso3, "paso7": paso7,
            "propuesta_paso5": propuesta_paso5, "hoy": proy.get("hoy", "")}


def _auto_badge():
    return ('<span style="font-size:10px;font-weight:700;color:#22c55e;'
            'background:#22c55e22;padding:2px 7px;border-radius:4px">AUTOMÁTICO</span>')


def _crit_badge():
    return ('<span style="font-size:10px;font-weight:700;color:#a78bfa;'
            'background:#a78bfa22;padding:2px 7px;border-radius:4px">CRITERIO</span>')


def _paso2_html(p2: dict) -> str:
    if not p2:
        return '<p style="color:var(--muted);font-size:12px">Sin datos del semáforo para este tema.</p>'
    col = p2["color"]
    det = (f' · actor determinante: <b>{escape(str(p2["actor_determinante"]))}</b>'
           if p2.get("actor_determinante") else "")
    return f"""
  <div style="display:flex;gap:24px;flex-wrap:wrap;margin:6px 0 4px">
    <div><div style="font-size:9px;color:var(--muted);text-transform:uppercase">Gravedad</div>
      <div style="font-size:22px;font-weight:700;color:{_color_nivel_proy(p2['gravedad'])}">{p2['gravedad']:.0f}</div></div>
    <div><div style="font-size:9px;color:var(--muted);text-transform:uppercase">Actividad</div>
      <div style="font-size:22px;font-weight:700;color:var(--text)">{p2['actividad']:.1f}</div></div>
    <div><div style="font-size:9px;color:var(--muted);text-transform:uppercase">Velocidad 7d</div>
      <div style="font-size:22px;font-weight:700;color:var(--text)">{p2['velocidad']:+.1f}</div></div>
    <div><div style="font-size:9px;color:var(--muted);text-transform:uppercase">Urgencia</div>
      <div><span style="background:{col}22;color:{col};padding:3px 9px;border-radius:5px;
                       font-weight:700;font-size:13px">{escape(p2['urgencia'])}</span></div></div>
  </div>
  <p style="font-size:12px;color:var(--muted);margin:4px 0 0">
    Cuadrante: <b style="color:var(--text)">{escape(p2['cuadrante'])}</b> ·
    índice de urgencia {p2['indice_urgencia']:.2f}{det}
  </p>"""


def _pxi_html(pxi: list) -> str:
    """Tabla compacta de factores P×I (probabilidad × impacto) del tema."""
    if not pxi:
        return ('<p style="color:var(--muted);font-size:12px">'
                'Sin factores P×I definidos para este tema.</p>')
    def _nivcol(niv):
        n = (niv or "").upper()
        return ("#ef4444" if n in ("CRÍTICO", "CRITICO", "ALTO") else
                "#f59e0b" if n in ("MEDIO",) else "#94a3b8")
    def _tend(t):
        t = (t or "").lower()
        return ("↑" if "sub" in t or "alza" in t or "crec" in t else
                "↓" if "baj" in t or "desc" in t else "→")
    filas = ""
    for f in pxi:
        niv = f.get("nivel") or "—"
        col = _nivcol(niv)
        filas += f"""<tr>
  <td style="font-size:12px;color:var(--text)">{escape(f.get('nombre') or f.get('factor_id') or '—')}
    <span style="font-size:10px;color:var(--muted)">· {escape(f.get('categoria') or '')}</span></td>
  <td style="text-align:center;color:var(--muted)">{f.get('probabilidad') if f.get('probabilidad') is not None else '—'}</td>
  <td style="text-align:center;color:var(--muted)">{f.get('impacto') if f.get('impacto') is not None else '—'}</td>
  <td style="text-align:center;font-weight:700;color:{col}">{f.get('score') if f.get('score') is not None else '—'}</td>
  <td style="text-align:center"><span style="color:{col};font-weight:600;font-size:11px">{escape(niv)}</span></td>
  <td style="text-align:center;color:var(--muted)">{_tend(f.get('tendencia'))}</td>
</tr>\n"""
    return f"""
  <table class="tbl" style="margin-top:6px">
    <thead><tr><th>Factor</th>
      <th style="text-align:center">Prob.</th><th style="text-align:center">Impacto</th>
      <th style="text-align:center">Score</th><th style="text-align:center">Nivel</th>
      <th style="text-align:center">Tend.</th></tr></thead>
    <tbody>{filas}</tbody>
  </table>"""


def _paso3_html(p3: list) -> str:
    if not p3:
        return ('<p style="color:var(--muted);font-size:12px;margin-top:6px">'
                'Sin actores vinculados a este tema. '
                '<a href="/admin/actores" style="color:var(--accent)">Vincúlalos →</a></p>')
    filas = ""
    for a in p3:
        idx = a.get("indice_activacion")
        idx_s = f"{idx:.1f}" if idx is not None else "—"
        et = a.get("trayectoria_etiqueta", "ESTABLE")
        etc = "#22c55e" if et == "ASCENSO" else "#ef4444" if et == "DECLIVE" else "#94a3b8"
        fl = "↑" if et == "ASCENSO" else "↓" if et == "DECLIVE" else "→"
        filas += f"""<tr>
  <td style="font-size:12px;color:var(--text)">{escape(a['nombre'])}
    <span style="font-size:10px;color:var(--muted)">· Nivel {escape(a.get('nivel','?'))}</span></td>
  <td style="text-align:center;color:var(--muted)">{a.get('peso_calculado',0):.0f}</td>
  <td style="text-align:center;font-weight:700;color:var(--text)">{idx_s}</td>
  <td style="text-align:center"><span style="color:{etc};font-weight:700">{fl} {et}</span>
    <span style="font-size:10px;color:var(--muted)">{a.get('trayectoria_en_tema',0):+g}</span></td>
</tr>\n"""
    return f"""
  <table class="tbl" style="margin-top:6px">
    <thead><tr><th>Actor</th><th style="text-align:center">Peso</th>
      <th style="text-align:center">Índice CVO</th><th style="text-align:center">Trayectoria</th></tr></thead>
    <tbody>{filas}</tbody>
  </table>"""


def _paso7_html(p7: dict) -> str:
    h = p7["h_obj"]
    da = p7["actividad_30d"] - p7["actividad_hoy"]
    dg = p7["gravedad_30d"] - p7["gravedad_hoy"]
    qpts = p7["efecto_quiebre"]
    q_nota = ""
    if p7["quiebres"]:
        chips = " · ".join(
            f'{escape(q["nombre"])} ({escape(q["direccion"])}/{escape(q["intensidad"])}/{escape(q["duracion"])})'
            for q in p7["quiebres"])
        q_nota = f'<p style="font-size:11px;color:var(--muted);margin:6px 0 0">Quiebres aplicables: {chips}</p>'
    return f"""
  <table class="tbl" style="margin-top:6px">
    <thead><tr><th></th><th style="text-align:center">Hoy</th>
      <th style="text-align:center">{h}d</th><th style="text-align:center">Δ</th></tr></thead>
    <tbody>
      <tr><td style="font-size:12px">Actividad mediática (Proy. A · tendencia)</td>
        <td style="text-align:center;color:var(--muted)">{p7['actividad_hoy']:.1f}</td>
        <td style="text-align:center;font-weight:700;color:var(--text)">{p7['actividad_30d']:.1f}</td>
        <td style="text-align:center;color:{'#22c55e' if da>0 else '#ef4444' if da<0 else '#94a3b8'}">{da:+.1f}</td></tr>
      <tr><td style="font-size:12px">Gravedad / riesgo (Proy. B · base + quiebres)</td>
        <td style="text-align:center;color:var(--muted)">{p7['gravedad_hoy']:.0f}</td>
        <td style="text-align:center;font-weight:700;color:{_color_nivel_proy(p7['gravedad_30d'])}">{p7['gravedad_30d']:.0f}</td>
        <td style="text-align:center;color:{'#22c55e' if dg>0 else '#ef4444' if dg<0 else '#94a3b8'}">{dg:+.0f}
          {f'<span style="font-size:10px;color:var(--muted)">(quiebre {qpts:+.0f})</span>' if abs(qpts)>=0.05 else ''}</td></tr>
    </tbody>
  </table>{q_nota}"""


def _card(titulo: str, badge: str, sub: str, inner: str) -> str:
    return f"""
<div class="card">
  <div class="card-title">{titulo} {badge}
    <span style="font-size:12px;font-weight:400;color:var(--muted);margin-left:8px">{sub}</span></div>
  {inner}
</div>"""


def _crit_textarea(name: str, valor: str, placeholder: str) -> str:
    return (f'<textarea name="{name}" form="intel-form" rows="3" placeholder="{escape(placeholder)}" '
            f'style="width:100%;background:var(--bg-3);color:var(--text);border:1px solid #334155;'
            f'border-radius:6px;padding:8px 10px;font-size:13px;line-height:1.5;resize:vertical">'
            f'{escape(valor or "")}</textarea>')


def _reporte_a_html(rep: dict) -> str:
    """Reporte A (solo pasos automáticos 2,3,4,8), en cards."""
    return (
        _card("Paso 2 — Factores de riesgo (Matriz P×I)", _auto_badge(), "de la tabla de factores · probabilidad × impacto", _pxi_html(rep.get("pxi", []))) +
        _card("Paso 3 — Evento / coyuntura", _auto_badge(), "del semáforo y la Matriz B", _paso2_html(rep["paso2"])) +
        _card("Paso 4 — Actores e intereses", _auto_badge(), "del modelo de actores · orden por índice de activación (CVO)", _paso3_html(rep["paso3"])) +
        _card("Paso 8 — Proyecciones", _auto_badge(), f"de Proyección A/B a {rep['paso7']['h_obj']} días", _paso7_html(rep["paso7"]))
    )


def _intel_selector(tema_sel: str, vista: str) -> str:
    opts = "".join(
        f'<option value="{t}"{" selected" if t == tema_sel else ""}>'
        f'{escape(t.replace("_"," ").title())}</option>'
        for t in _IMPACTO_TEMA
    )
    def _tab(v, label):
        activo = (v == vista)
        bg = "var(--accent)" if activo else "var(--bg-3)"
        fg = "#000" if activo else "var(--muted)"
        return (f'<a href="/admin/inteligencia?tema={escape(tema_sel)}&vista={v}" '
                f'style="background:{bg};color:{fg};border:1px solid #334155;border-radius:6px;'
                f'padding:6px 16px;font-size:12px;font-weight:600;text-decoration:none">{label}</a>')
    return f"""
<div style="display:flex;align-items:center;gap:14px;margin-bottom:16px;flex-wrap:wrap">
  <div style="font-size:15px;font-weight:600;color:var(--text)">Análisis del tema:</div>
  <form method="get" action="/admin/inteligencia" style="display:flex;gap:8px;align-items:center">
    <select name="tema" onchange="this.form.submit()"
            style="background:var(--bg-3);color:var(--text);border:1px solid #334155;
                   border-radius:4px;padding:5px 10px;font-size:13px">{opts}</select>
    <input type="hidden" name="vista" value="{escape(vista)}">
  </form>
  <div style="margin-left:auto;display:flex;gap:6px;align-items:center">
    <span style="font-size:11px;color:var(--muted)">Profundidad:</span>
    {_tab('A', 'Reporte A')}{_tab('B', 'Reporte B')}
  </div>
</div>"""


@router.get("/inteligencia", response_class=HTMLResponse)
async def admin_inteligencia(request: Request):
    """Motor de Inteligencia — Reportes A (automático) y B (con criterio)."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import obtener_ultima_version, listar_versiones

    tema_sel = request.query_params.get("tema", list(_IMPACTO_TEMA.keys())[0])
    if tema_sel not in _IMPACTO_TEMA:
        tema_sel = list(_IMPACTO_TEMA.keys())[0]
    vista = request.query_params.get("vista", "B")
    if vista not in ("A", "B"):
        vista = "B"

    db = _get_db_path()
    rep = _reporte_automatico(db, tema_sel)
    msg = request.query_params.get("msg", "")
    msg_html = (f'<div class="alert-box alert-info" style="margin-bottom:12px">✓ {escape(msg)}</div>'
                ) if msg else ""
    selector = _intel_selector(tema_sel, vista)

    if rep is None:
        cuerpo = """
<div class="card card-accent">
  <div class="card-title">🧠 Motor de Inteligencia</div>
  <p style="color:var(--muted)">Sin snapshot OSINT disponible aún. El reporte lee
  los datos del semáforo, actores y proyección; espera el próximo ciclo del motor.</p>
</div>"""
        return HTMLResponse(_page(f"Inteligencia · {tema_sel.replace('_',' ').title()}",
                                  selector + cuerpo, "inteligencia", sesion["username"]))

    titulo_tema = escape(tema_sel.replace('_', ' ').title())

    # ── Reporte A: solo automáticos ──
    if vista == "A":
        cuerpo = f"""
<div class="card" style="border-left:3px solid #22c55e">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
    <span style="font-size:11px;font-weight:700;color:#22c55e;background:#22c55e22;
                 padding:3px 8px;border-radius:4px">REPORTE A · FOTO AUTOMÁTICA</span>
    <span style="font-size:12px;color:var(--muted)">
      {titulo_tema} · generado {escape(rep['hoy'])} · solo pasos automáticos (2 · 3 · 4 · 8)
    </span>
  </div>
</div>
{_reporte_a_html(rep)}"""
        return HTMLResponse(_page(f"Inteligencia · {titulo_tema}",
                                  msg_html + selector + cuerpo, "inteligencia", sesion["username"]))

    # ── Reporte B: criterio (editable) + automáticos, intercalados 1-7 ──
    ultima = obtener_ultima_version(db, tema_sel) or {}
    versiones = listar_versiones(db, tema_sel)
    prop = rep.get("propuesta_paso5", {})

    # Prefill del paso 5: criterio guardado, o la propuesta automática si está vacío
    p5_sust = ultima.get("paso5_sustancia") or prop.get("sustancia", "")
    p5_ruido = ultima.get("paso5_ruido") or prop.get("ruido", "")

    paso1 = _card("Paso 1 — Escenario estructural", _crit_badge(), "tu lectura de fondo",
                  _crit_textarea("paso1_escenario", ultima.get("paso1_escenario"),
                                 "Marco estructural del tema: tendencias de fondo, condiciones permanentes…"))
    pasopxi = _card("Paso 2 — Factores de riesgo (Matriz P×I)", _auto_badge(), "de la tabla de factores · probabilidad × impacto", _pxi_html(rep.get("pxi", [])))
    paso2 = _card("Paso 3 — Evento / coyuntura", _auto_badge(), "del semáforo y la Matriz B", _paso2_html(rep["paso2"]))
    paso3 = _card("Paso 4 — Actores e intereses", _auto_badge(), "del modelo de actores · orden por índice CVO", _paso3_html(rep["paso3"]))
    paso4 = _card("Paso 5 — Organización de actores", _crit_badge(), "cómo se alinean / enfrentan",
                  _crit_textarea("paso4_organizacion", ultima.get("paso4_organizacion"),
                                 "Coaliciones, alineamientos, rivalidades entre los actores del paso 4…"))
    paso5 = _card("Paso 6 — Filtro ruido / sustancia", _crit_badge() +
                  ' <span style="font-size:10px;color:#f59e0b;background:#f59e0b22;padding:2px 7px;border-radius:4px">HÍBRIDO</span>',
                  "la plataforma propone · tú editas · el ruido se conserva",
                  f"""
  <div style="background:var(--bg-3);border-radius:6px;padding:8px 10px;margin:0 0 10px;font-size:11px;color:var(--muted)">
    <b>Propuesta automática</b> (de los cuadrantes de la Matriz B):<br>
    · sustancia (grave estructural): {escape(prop.get('sustancia',''))}<br>
    · ruido (activo, no grave): {escape(prop.get('ruido',''))}
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div>
      <div style="font-size:11px;font-weight:600;color:#22c55e;margin-bottom:4px">SUSTANCIA (lo que importa)</div>
      {_crit_textarea("paso5_sustancia", p5_sust, "Lo grave estructural que merece atención…")}
    </div>
    <div>
      <div style="font-size:11px;font-weight:600;color:#f59e0b;margin-bottom:4px">RUIDO (separado, NO borrado)</div>
      {_crit_textarea("paso5_ruido", p5_ruido, "Lo activo en cobertura pero sin gravedad de fondo — se conserva visible…")}
    </div>
  </div>""")
    paso6 = _card("Paso 7 — Impacto", _crit_badge(), "consecuencias para el decisor",
                  _crit_textarea("paso6_impacto", ultima.get("paso6_impacto"),
                                 "Qué implica todo lo anterior: riesgos, oportunidades, recomendaciones…"))
    paso7 = _card("Paso 8 — Proyecciones", _auto_badge(), f"de Proyección A/B a {rep['paso7']['h_obj']} días", _paso7_html(rep["paso7"]))

    # Historial de versiones
    if versiones:
        filas_v = "".join(
            f'<tr><td style="font-size:12px"><a href="/admin/inteligencia/version/{v["id"]}" '
            f'style="color:var(--accent)">v{v["version"]}</a></td>'
            f'<td style="font-size:11px;color:var(--muted)">{escape((v.get("fecha") or "")[:19].replace("T"," "))}</td>'
            f'<td style="font-size:11px;color:var(--accent)">{escape(v.get("usuario") or "—")}</td></tr>'
            for v in versiones)
        hist = f"""
  <table class="tbl"><thead><tr><th>Versión</th><th>Fecha</th><th>Por</th></tr></thead>
    <tbody>{filas_v}</tbody></table>"""
        ult_nota = (f'Última versión guardada: <b>v{ultima.get("version","?")}</b> '
                    f'({escape((ultima.get("fecha") or "")[:19].replace("T"," "))})')
    else:
        hist = '<p style="font-size:12px;color:var(--muted)">Aún no hay versiones guardadas para este tema.</p>'
        ult_nota = "Sin versiones guardadas todavía — tu criterio parte de la propuesta automática."

    cuerpo = f"""
<div class="card" style="border-left:3px solid #a78bfa">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
    <span style="font-size:11px;font-weight:700;color:#a78bfa;background:#a78bfa22;
                 padding:3px 8px;border-radius:4px">REPORTE B · COMPLETO</span>
    <span style="font-size:12px;color:var(--muted)">
      {titulo_tema} · automáticos generados {escape(rep['hoy'])} · {ult_nota}
    </span>
  </div>
</div>
<form id="intel-form" method="post" action="/admin/inteligencia/guardar">
  <input type="hidden" name="tema" value="{escape(tema_sel)}">
</form>
{paso1}{pasopxi}{paso2}{paso3}{paso4}{paso5}{paso6}{paso7}
<div class="card">
  <div style="display:flex;gap:12px;align-items:center">
    <button type="submit" form="intel-form"
            style="background:var(--accent);color:#000;border:none;border-radius:6px;
                   padding:9px 24px;font-size:14px;font-weight:700;cursor:pointer">
      Guardar versión nueva
    </button>
    <span style="font-size:12px;color:var(--muted)">
      Cada guardado crea una versión fechada y congela la foto automática de este momento.
    </span>
  </div>
</div>
<div class="card">
  <div class="card-title">Historial de versiones — {titulo_tema}</div>
  {hist}
</div>"""

    return HTMLResponse(_page(f"Inteligencia · {titulo_tema}",
                              msg_html + selector + cuerpo, "inteligencia", sesion["username"]))


@router.post("/inteligencia/guardar")
async def admin_inteligencia_guardar(request: Request):
    """Guarda una versión nueva del criterio + congela la foto automática."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import guardar_analisis, LockTimeoutError
    form = await request.form()
    tema = (form.get("tema") or "").strip()
    if tema not in _IMPACTO_TEMA:
        return RedirectResponse("/admin/inteligencia?err=Tema+inválido", status_code=303)
    db = _get_db_path()
    criterio = {
        "paso1_escenario": (form.get("paso1_escenario") or "").strip() or None,
        "paso4_organizacion": (form.get("paso4_organizacion") or "").strip() or None,
        "paso5_sustancia": (form.get("paso5_sustancia") or "").strip() or None,
        "paso5_ruido": (form.get("paso5_ruido") or "").strip() or None,
        "paso6_impacto": (form.get("paso6_impacto") or "").strip() or None,
    }
    # Congela la foto automática (pasos 2,3,7) de este momento
    rep = _reporte_automatico(db, tema)
    snapshot = json.dumps(rep, ensure_ascii=False) if rep else None
    try:
        r = guardar_analisis(db, tema, criterio, snapshot, sesion["username"])
        return RedirectResponse(
            f"/admin/inteligencia?tema={tema}&vista=B&msg=Versión+v{r['version']}+guardada",
            status_code=303)
    except LockTimeoutError:
        return RedirectResponse(
            f"/admin/inteligencia?tema={tema}&vista=B&err=BD+ocupada.+Reintenta.", status_code=303)
    except Exception as e:
        return RedirectResponse(
            f"/admin/inteligencia?tema={tema}&vista=B&err={escape(str(e))}", status_code=303)


@router.get("/inteligencia/version/{version_id:int}", response_class=HTMLResponse)
async def admin_inteligencia_version(request: Request, version_id: int):
    """Vista solo-lectura de una versión histórica: snapshot_auto + criterio de entonces."""
    sesion, err = _admin_guard(request)
    if err:
        return err
    from ..storage.config_loader import obtener_version
    db = _get_db_path()
    v = obtener_version(db, version_id)
    if not v:
        return RedirectResponse("/admin/inteligencia?err=Versión+no+encontrada", status_code=303)
    tema = v["tema"]
    titulo_tema = escape(tema.replace("_", " ").title())

    # Pasos automáticos: del snapshot CONGELADO (datos de entonces), no en vivo
    try:
        rep = json.loads(v["snapshot_auto"]) if v.get("snapshot_auto") else None
    except Exception:
        rep = None

    def _crit_ro(titulo, valor):
        txt = escape(valor) if valor else '<span style="color:var(--muted)">(vacío)</span>'
        return _card(titulo, _crit_badge(), "",
                     f'<div style="white-space:pre-wrap;font-size:13px;line-height:1.5;color:var(--text)">{txt}</div>')

    if rep:
        ppxi = _card("Paso 2 — Factores de riesgo (Matriz P×I)", _auto_badge(), "foto congelada", _pxi_html(rep.get("pxi", [])))
        p2 = _card("Paso 3 — Evento / coyuntura", _auto_badge(), "foto congelada", _paso2_html(rep.get("paso2")))
        p3 = _card("Paso 4 — Actores e intereses", _auto_badge(), "foto congelada", _paso3_html(rep.get("paso3") or []))
        p7 = _card("Paso 8 — Proyecciones", _auto_badge(), "foto congelada", _paso7_html(rep["paso7"]))
    else:
        ppxi = p2 = p3 = p7 = ""
    p5 = _card("Paso 6 — Filtro ruido / sustancia", _crit_badge(), "",
               f"""
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div><div style="font-size:11px;font-weight:600;color:#22c55e;margin-bottom:4px">SUSTANCIA</div>
      <div style="white-space:pre-wrap;font-size:13px;color:var(--text)">{escape(v.get('paso5_sustancia') or '(vacío)')}</div></div>
    <div><div style="font-size:11px;font-weight:600;color:#f59e0b;margin-bottom:4px">RUIDO (conservado)</div>
      <div style="white-space:pre-wrap;font-size:13px;color:var(--text)">{escape(v.get('paso5_ruido') or '(vacío)')}</div></div>
  </div>""")

    cuerpo = f"""
<div class="card" style="border-left:3px solid #64748b">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
    <span style="font-size:11px;font-weight:700;color:#94a3b8;background:#94a3b822;
                 padding:3px 8px;border-radius:4px">VERSIÓN HISTÓRICA · SOLO LECTURA</span>
    <span style="font-size:12px;color:var(--muted)">
      {titulo_tema} · v{v.get('version','?')} · {escape((v.get('fecha') or '')[:19].replace('T',' '))} ·
      por {escape(v.get('usuario') or '—')}
    </span>
    <a href="/admin/inteligencia?tema={escape(tema)}&vista=B" style="margin-left:auto;color:var(--accent);font-size:12px">← Versión actual</a>
  </div>
</div>
{_crit_ro("Paso 1 — Escenario estructural", v.get("paso1_escenario"))}
{ppxi}{p2}{p3}
{_crit_ro("Paso 5 — Organización de actores", v.get("paso4_organizacion"))}
{p5}
{_crit_ro("Paso 7 — Impacto", v.get("paso6_impacto"))}
{p7}"""

    return HTMLResponse(_page(f"Inteligencia · {titulo_tema} · v{v.get('version','?')}",
                              cuerpo, "inteligencia", sesion["username"]))
