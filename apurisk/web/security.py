"""APURISK · web/security — Middleware de acceso y login por usuario/clave."""
from __future__ import annotations
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .core import (
    _json_error, _excede_rate_limit, _es_ruta_publica, _apikey_valida,
    _auth_state, _COOKIE_SESION, _COOKIE_AUTH, SECRET_SESION, SESION_TTL,
    _GET_PROTEGIDOS, _METODOS_ESCRITURA,
)

try:
    from ..utils import auth
except ImportError:
    from apurisk.utils import auth

router = APIRouter()



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
                    httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 4)
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
            httponly=True, secure=True, samesite="lax", max_age=60 * 60 * 4,
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


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/dashboard"):
    if not _auth_state["login_enforce"]:
        return HTMLResponse(_html_login(
            error="El inicio de sesión no está configurado en este servidor."))
    if auth.verificar_token_sesion(request.cookies.get(_COOKIE_SESION, ""), SECRET_SESION):
        return RedirectResponse(_safe_next(next), status_code=302)
    return HTMLResponse(_html_login(next_url=next))


@router.post("/login")
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


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(_COOKIE_SESION)
    return resp
