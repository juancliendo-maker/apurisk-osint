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

Arquitectura (refactor #6 — modular):
  Este archivo es solo el punto de ENSAMBLE. La lógica vive en el paquete
  `apurisk.web`:
    - web/core            configuración y utilidades compartidas
    - web/security        middleware de acceso + login
    - web/schedulers      tareas de fondo y arranque
    - web/routes_dashboard, routes_intelligence, routes_diagnostics,
      routes_reports, routes_cases   endpoints por tema
  El símbolo público `app` (que usa `uvicorn apurisk.server:app`) NO cambia.
"""
from __future__ import annotations
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

try:
    from .web.core import REFRESH_SECONDS, SERVER_VERSION, PORT, OUTPUT_DIR
    from .web.security import router as security_router, _guardia_acceso
    from .web.schedulers import _startup
    from .web.routes_dashboard import router as dashboard_router
    from .web.routes_intelligence import router as intelligence_router
    from .web.routes_diagnostics import router as diagnostics_router
    from .web.routes_reports import router as reports_router
    from .web.routes_cases import router as cases_router
except ImportError:  # ejecución como script suelto (sin contexto de paquete)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apurisk.web.core import REFRESH_SECONDS, SERVER_VERSION, PORT, OUTPUT_DIR
    from apurisk.web.security import router as security_router, _guardia_acceso
    from apurisk.web.schedulers import _startup
    from apurisk.web.routes_dashboard import router as dashboard_router
    from apurisk.web.routes_intelligence import router as intelligence_router
    from apurisk.web.routes_diagnostics import router as diagnostics_router
    from apurisk.web.routes_reports import router as reports_router
    from apurisk.web.routes_cases import router as cases_router


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


# ----------------------------------------------------------------------
# Middleware de acceso, arranque (schedulers + auth) y routers temáticos.
# El orden de include_router preserva la precedencia original de rutas.
# ----------------------------------------------------------------------
app.middleware("http")(_guardia_acceso)
app.on_event("startup")(_startup)

app.include_router(security_router)
app.include_router(dashboard_router)
app.include_router(intelligence_router)
app.include_router(diagnostics_router)
app.include_router(reports_router)
app.include_router(cases_router)


# Para correr local con: python -m apurisk.server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apurisk.server:app", host="0.0.0.0", port=PORT, reload=False)
