"""APURISK · web/routes_cases — Análisis de caso y Riesgo Político Minero."""
from __future__ import annotations
import json

from fastapi import APIRouter, HTTPException, Body, Request
from fastapi.responses import HTMLResponse, FileResponse

from .core import (
    OUTPUT_DIR, _ultimo_snapshot_path, _fetch_url_segura, ApuriskArchive,
)

try:
    from ..utils.timezone_pe import now_pe
    from ..analyzers.caso_analyzer import analizar_caso
    from ..analyzers.riesgo_minera import analizar_riesgo_minera
    from ..reports import generar_reporte_caso_pdf
    from ..reports.pdf_minera import generar_reporte_minera_pdf
except ImportError:
    from apurisk.utils.timezone_pe import now_pe
    from apurisk.analyzers.caso_analyzer import analizar_caso
    from apurisk.analyzers.riesgo_minera import analizar_riesgo_minera
    from apurisk.reports import generar_reporte_caso_pdf
    from apurisk.reports.pdf_minera import generar_reporte_minera_pdf

router = APIRouter()



# ======================================================================
# ANÁLISIS DE CASO (input analista → reporte PDF)
# ======================================================================
@router.post("/api/analisis-caso")
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


@router.get("/analisis", response_class=HTMLResponse)
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


@router.post("/api/riesgo-minera/generar")
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


@router.get("/riesgo-minera", response_class=HTMLResponse)
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
