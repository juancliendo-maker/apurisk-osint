"""THALOS · Reporte Político por Caso — motor descriptivo (sub-fase 4a).

Genera las secciones DESCRIPTIVAS (I-IV) del Reporte por Caso bajo un arnés de
grounding que impide que el modelo invente. Hermano del arnés del AP24
(analisis_politico.py): MISMO contrato — el modelo INVOCA referencias, no las
crea — extendido a las tres procedencias del expediente.

ARNÉS — "cada pieza es una fuente citable con ID":
  1. Antes de llamar al modelo, cada pieza INCLUIDA y LISTA del expediente se
     numera P1, P2, P3... sin importar su procedencia. Ese ID es la única forma
     de referencia admitida.
  2. El material se envía como  [Pn] (procedencia) título/fuente/fecha — texto.
     El texto de url_externa se limpia de HTML aquí (deuda de la sub-fase 3).
  3. El modelo cita por ID y no puede afirmar nada sin un ID de respaldo (regla
     dura del prompt maestro, config-editable en CASO_PROMPT_MAESTRO).
  4. El arnés VALIDA: todo ID citado debe existir. Los inexistentes se registran
     y NO se renderizan (como los huérfanos del AP24). La ATRIBUCIÓN en prosa la
     pone el SISTEMA desde la pieza, según su procedencia (tres fórmulas). El
     modelo nunca escribe medios, URLs ni nombres de archivo.

SALIDA: objeto de análisis estructurado (secciones I-IV + hechos citados
resueltos + silencios + trazas de validación), persistido en JSON para que el
render (sub-fases 4b/5) no vuelva a llamar al modelo.

Sin API key o si el modelo no responde: NO se inventan secciones — se devuelve
estado 'error' con el detalle (honestidad de datos).
"""
from __future__ import annotations
import re
import logging

from .analisis_politico import _quitar_tildes, _norm_hdr, _sanitizar

log = logging.getLogger("apurisk.caso")

# Secciones descriptivas de esta sub-fase (la V-VII son de 4b/5).
SECCIONES_CASO = [
    "I. SÍNTESIS DEL CASO",
    "II. LA PREGUNTA Y EL MATERIAL",
    "III. DESARROLLO EN LA VENTANA",
    "IV. CONEXIONES Y CONTEXTO",
]

# Firmas tolerantes por sección (sobre la línea normalizada, sin tildes/numeración).
_SIG_CASO = [
    ("I. SÍNTESIS DEL CASO",         r"\bSINTESIS\b"),
    ("II. LA PREGUNTA Y EL MATERIAL", r"\bPREGUNTA\b.*\bMATERIAL\b|\bMATERIAL\b.*\bPREGUNTA\b"),
    ("III. DESARROLLO EN LA VENTANA", r"\bDESARROLLO\b"),
    ("IV. CONEXIONES Y CONTEXTO",     r"\bCONEXION(?:ES)?\b"),
]

SIN_SOPORTE = "Sin material que lo sostenga en el expediente."


# ── Limpieza HTML → texto (deuda de la sub-fase 3) ────────────────────────────
def html_a_texto(crudo: str) -> str:
    """Convierte HTML a texto plano legible. Usa BeautifulSoup (ya presente en
    requirements-server.txt y en uso por rss_media.py — cero dependencias
    nuevas); si no estuviera, cae a un limpiador de la stdlib.

    No modifica lo guardado en BD: se aplica al construir el material del modelo.
    """
    s = crudo or ""
    if "<" not in s:
        return s.strip()
    try:
        from bs4 import BeautifulSoup
        sopa = BeautifulSoup(s, "html.parser")
        for tag in sopa(["script", "style", "noscript", "svg", "nav", "footer"]):
            tag.decompose()
        txt = sopa.get_text(separator=" ")
    except Exception:
        from html.parser import HTMLParser
        from html import unescape

        class _Limpia(HTMLParser):
            def __init__(self):
                super().__init__()
                self.buf, self.saltar = [], False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self.saltar = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript"):
                    self.saltar = False

            def handle_data(self, d):
                if not self.saltar:
                    self.buf.append(d)

        p = _Limpia()
        try:
            p.feed(s)
            txt = unescape(" ".join(p.buf))
        except Exception:
            txt = re.sub(r"<[^>]+>", " ", s)
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


# ── Numeración de piezas citables ─────────────────────────────────────────────
def piezas_citables(piezas: list) -> list:
    """Piezas que pueden citarse: incluidas por el analista Y con extracción
    lista. Devuelve [{id_cita:'P1', pieza:{...}}, ...] en orden estable (por id).
    """
    aptas = [p for p in (piezas or [])
             if p.get("incluido") and p.get("estado_extraccion") == "listo"]
    aptas.sort(key=lambda p: p.get("id") or 0)
    return [{"id_cita": f"P{i}", "pieza": p} for i, p in enumerate(aptas, start=1)]


def _titulo_pieza(p: dict) -> str:
    return (p.get("titulo") or p.get("nombre_archivo") or p.get("url")
            or "(sin título)").strip()


def _fecha_legible(p: dict) -> str:
    f = (p.get("fecha_pieza") or "")[:10]
    return f or ""


def _medio_pieza(p: dict) -> str:
    """Medio de una pieza url_externa: la fuente declarada o, si falta, el
    dominio corto de la URL."""
    fuente = (p.get("fuente") or "").strip()
    if fuente:
        return fuente
    m = re.match(r"https?://([^/]+)", p.get("url") or "")
    host = (m.group(1) if m else "").lower()
    return (host[4:] if host.startswith("www.") else host) or "la fuente externa"


# ── Material para el modelo (con presupuesto declarado) ───────────────────────
def material_caso_para_llm(meta: dict, citables: list, par: dict) -> dict:
    """Arma el material del expediente para el modelo.

    Cada pieza va como  [Pn] (procedencia) título | fuente | fecha — texto.
    El texto se limpia de HTML y se trunca a CASO_MAX_CHARS_PIEZA_ENVIO; el
    material total se corta en CASO_MAX_CHARS_MATERIAL (60 piezas x 40k no cabe
    en la ventana de contexto). Devuelve {texto, enviadas, omitidas} — las
    omitidas se declaran en el reporte, no se ocultan.
    """
    tope_pieza = int(par.get("max_chars_pieza_envio", 6000))
    tope_total = int(par.get("max_chars_material", 220000))
    escenarios = meta.get("escenarios_candidatos") or []
    cab = [
        f"PREGUNTA DEL CASO: {meta.get('pregunta') or '—'}",
        f"VENTANA: últimos {meta.get('ventana_dias') or 7} días.",
        "",
        "ESCENARIOS CANDIDATOS (declarados por el analista; NO los cambies, NO "
        "elijas ganador, NO agregues otros):",
    ]
    if escenarios:
        cab += [f"  - {e}" for e in escenarios]
    else:
        cab.append("  (ninguno declarado)")
    ind = (meta.get("indicaciones_detalle") or "").strip()
    if ind:
        cab += ["", f"INDICACIONES DEL ANALISTA: {ind}"]
    cab += ["", f"=== EXPEDIENTE — {len(citables)} piezas citables ===",
            "Cita SOLO por ID [Pn]. No escribas medios, URLs ni nombres de archivo.",
            ""]
    partes, usados, enviadas, omitidas = ["\n".join(cab)], len("\n".join(cab)), 0, 0
    for c in citables:
        p = c["pieza"]
        texto = p.get("texto_extraido") or ""
        if p.get("procedencia") == "url_externa":
            texto = html_a_texto(texto)
        texto = texto.strip()[:tope_pieza]
        meta_linea = " | ".join(x for x in (
            _titulo_pieza(p), (p.get("fuente") or ""), _fecha_legible(p)) if x)
        bloque = (f"[{c['id_cita']}] ({p.get('procedencia')}) {meta_linea}\n"
                  f"{texto or '(sin texto)'}\n")
        if usados + len(bloque) > tope_total:
            omitidas += 1
            continue
        partes.append(bloque)
        usados += len(bloque)
        enviadas += 1
    if omitidas:
        partes.append(f"\n[NOTA DE MATERIAL] {omitidas} pieza(s) del expediente no "
                      f"caben en el límite de material y no se enviaron.")
    partes.append("\nRedacta las secciones I a IV siguiendo EXACTAMENTE la "
                  "estructura y las reglas del system.")
    return {"texto": "\n".join(partes), "enviadas": enviadas, "omitidas": omitidas}


# ── Parseo de secciones (patrón robusto heredado del AP24) ────────────────────
def _detectar_seccion_caso(linea: str):
    norm = _norm_hdr(linea)
    if not norm or len(norm) > 60 or len(norm.split()) > 9:
        return None
    for canon, pat in _SIG_CASO:
        if re.search(pat, norm):
            return canon
    return None


def parsear_secciones_caso(texto: str) -> list:
    """Divide la salida en [(sección_canónica, cuerpo)]. Tolerante a numeración
    romana, tildes y mayúsculas. ANTI-PÉRDIDA: el preámbulo se conserva y, si no
    se detecta ningún encabezado, se vuelca todo bajo un genérico."""
    if not texto or not texto.strip():
        return []
    lineas = texto.split("\n")
    marcas, vistos = [], set()
    for i, ln in enumerate(lineas):
        canon = _detectar_seccion_caso(ln)
        if canon and canon not in vistos:
            vistos.add(canon)
            marcas.append((i, canon))
    if not marcas:
        return [("ANÁLISIS COMPLETO", texto.strip())]
    out = []
    preambulo = "\n".join(lineas[:marcas[0][0]]).strip()
    for j, (idx, canon) in enumerate(marcas):
        fin = marcas[j + 1][0] if j + 1 < len(marcas) else len(lineas)
        cuerpo = "\n".join(lineas[idx + 1:fin]).strip().lstrip("—-:").strip()
        if j == 0 and preambulo:
            cuerpo = (preambulo + "\n" + cuerpo).strip()
        out.append((canon, cuerpo))
    return out


def secciones_faltantes_caso(secciones: list) -> list:
    presentes = {s for s, _ in secciones}
    return [s for s in SECCIONES_CASO if s not in presentes]


# ── Atribución por procedencia (LA PONE EL SISTEMA, no el modelo) ─────────────
def atribucion_pieza(p: dict, ventana_dias: int, agregado_bd: bool = False) -> str:
    """Fórmula introductoria de atribución según la procedencia de la pieza.

    - bd_osint            → "las noticias de los últimos {ventana} días denuncian que"
    - url_externa         → "{medio}, en su edición del {fecha}, consigna que"
    - documento_analista  → "el documento {nombre}, incorporado al expediente por
                             el analista, consigna que"
    """
    proc = p.get("procedencia")
    if proc == "bd_osint":
        return f"las noticias de los últimos {ventana_dias} días denuncian que"
    if proc == "url_externa":
        medio = _medio_pieza(p)
        fecha = _fecha_legible(p)
        if fecha:
            return f"{medio}, en su edición del {fecha}, consigna que"
        return f"{medio} consigna que"
    if proc == "documento_analista":
        nombre = (p.get("nombre_archivo") or _titulo_pieza(p)).strip()
        return (f"el documento {nombre}, incorporado al expediente por el "
                f"analista, consigna que")
    return "el material del expediente consigna que"


def _atribucion_sufijo(p: dict, ventana_dias: int) -> str:
    """Variante corta, para cuando el marcador no abre la oración."""
    proc = p.get("procedencia")
    if proc == "bd_osint":
        return f"noticias de los últimos {ventana_dias} días"
    if proc == "url_externa":
        medio = _medio_pieza(p)
        fecha = _fecha_legible(p)
        return f"{medio}, {fecha}" if fecha else medio
    if proc == "documento_analista":
        return f"documento {(p.get('nombre_archivo') or _titulo_pieza(p)).strip()}"
    return "expediente"


_RE_MARCA = re.compile(r"\[\s*(P\s*\d+(?:\s*,\s*P?\s*\d+)*)\s*\]", re.IGNORECASE)


def _ids_de_marca(bruto: str) -> list:
    """'P1, P4' | 'P1,4' → ['P1','P4'] (normaliza la P opcional del segundo)."""
    out = []
    for tk in re.split(r"\s*,\s*", bruto or ""):
        m = re.match(r"P?\s*(\d+)", tk.strip(), re.IGNORECASE)
        if m:
            out.append("P" + m.group(1))
    return out


def resolver_atribuciones(texto: str, mapa: dict, ventana_dias: int) -> dict:
    """Sustituye cada marcador [Pn] por la ATRIBUCIÓN que corresponde a su pieza.

    - Marcador que ABRE la afirmación (inicio de línea o tras punto) → fórmula
      introductoria, capitalizada: "Las noticias ... denuncian que <afirmación>".
    - Marcador a mitad de frase → cita sufijo "(según ...)" — degradación grácil,
      gramaticalmente segura si el modelo no respetó la posición.
    - IDs inexistentes → NO se renderizan (se eliminan) y se registran.
    - Varias piezas: las bd_osint se agregan en una sola fórmula; si quedan
      varias fórmulas, la primera abre y el resto va como cita sufijo.

    Devuelve {texto, ids_usados, ids_invalidos}.
    """
    usados, invalidos, descartadas = [], [], []
    src = texto or ""
    out, cursor = [], 0

    for m in _RE_MARCA.finditer(src):
        if m.start() < cursor:      # ya consumido por un descarte previo
            continue
        out.append(src[cursor:m.start()])
        cursor = m.end()
        ids = _ids_de_marca(m.group(1))
        validos, faltan = [], []
        for i in ids:
            (validos if i in mapa else faltan).append(i)
        for i in faltan:
            if i not in invalidos:
                invalidos.append(i)
        if not validos:
            # Marcador huérfano. Si ABRÍA la afirmación, esa afirmación no tiene
            # NINGÚN respaldo: la regla dura del arnés prohíbe publicarla. Se
            # suprime entera y se DECLARA (no se oculta). Si el marcador iba a
            # mitad de frase, la afirmación ya tenía su atribución: solo se quita
            # el marcador.
            previo_h = "".join(out)
            if (not previo_h.strip()) or re.search(r"[.\n;:]\s*$", previo_h):
                fin = src.find(".", cursor)
                corte = (fin + 1) if fin != -1 else len(src)
                frag = src[cursor:corte].strip()
                if frag:
                    descartadas.append(frag)
                cursor = corte
            continue
        for i in validos:
            if i not in usados:
                usados.append(i)
        # agrupar: las bd_osint colapsan en UNA sola fórmula agregada
        formulas, vistas_bd = [], False
        for i in validos:
            p = mapa[i]
            if p.get("procedencia") == "bd_osint":
                if vistas_bd:
                    continue
                vistas_bd = True
            formulas.append((i, p))
        previo = "".join(out)
        abre = (not previo.strip()) or bool(re.search(r"[.\n;:]\s*$", previo))
        principal = atribucion_pieza(formulas[0][1], ventana_dias)
        extras = [_atribucion_sufijo(p, ventana_dias) for _, p in formulas[1:]]
        if abre:
            out.append(principal[0].upper() + principal[1:] + " ")
            if extras:
                # La atribución de las piezas adicionales NO se pierde: se cierra
                # al final de la afirmación (el prefijo ya abrió la oración).
                fin = src.find(".", cursor)
                corte = fin if fin != -1 else len(src)
                out.append(src[cursor:corte])
                out.append(f" (además, según {'; '.join(extras)})")
                cursor = corte
        else:
            sufijo = _atribucion_sufijo(formulas[0][1], ventana_dias)
            out.append(f" (según {'; '.join([sufijo] + extras)})")
    out.append(src[cursor:])

    nuevo = "".join(out)
    # el marcador que abría deja la afirmación en minúscula: correcto.
    nuevo = re.sub(r"[ \t]{2,}", " ", nuevo)
    nuevo = re.sub(r"\s+([.,;:])", r"\1", nuevo)
    nuevo = re.sub(r"(?m)^[ \t]+", "", nuevo)
    nuevo = re.sub(r"\n{3,}", "\n\n", nuevo)
    return {"texto": nuevo.strip(), "ids_usados": usados,
            "ids_invalidos": invalidos, "descartadas": descartadas}


# ── Silencios: escenarios sin ninguna pieza que los sostenga ──────────────────
def detectar_silencios(cuerpo_seccion_ii: str, escenarios: list) -> list:
    """Escenarios candidatos SIN soporte en el expediente.

    Subproducto mecánico del arnés (un hallazgo, no un vacío que ocultar): se
    considera en silencio el escenario cuyo bloque en la sección II no cita
    ningún ID, o que declara explícitamente la fórmula de 'sin material'.
    """
    silencios = []
    cuerpo = cuerpo_seccion_ii or ""
    # cortar el cuerpo en bloques por escenario (por aparición de su enunciado)
    posiciones = []
    for e in (escenarios or []):
        enun = (e or "").strip()
        if not enun:
            continue
        idx = cuerpo.lower().find(enun.lower()[:60])
        posiciones.append((idx if idx >= 0 else len(cuerpo) + 1, enun))
    posiciones.sort()
    for k, (ini, enun) in enumerate(posiciones):
        if ini > len(cuerpo):          # el modelo ni lo mencionó → silencio
            silencios.append(enun)
            continue
        fin = posiciones[k + 1][0] if k + 1 < len(posiciones) else len(cuerpo)
        bloque = cuerpo[ini:fin]
        sin_ids = not _RE_MARCA.search(bloque)
        declarado = _quitar_tildes(SIN_SOPORTE).lower()[:28] in _quitar_tildes(bloque).lower()
        if sin_ids or declarado:
            silencios.append(enun)
    return silencios


# ── Motor ─────────────────────────────────────────────────────────────────────
def generar_analisis_caso(db_path: str, reporte_id: int) -> dict:
    """Genera el análisis descriptivo (I-IV) del caso y lo PERSISTE.

    Devuelve el objeto de análisis: {estado, secciones, hechos_citados,
    silencios, ids_invalidos, material, generado_en, ...}. Si no hay API key o
    el modelo no responde, estado='error' y NO se inventan secciones.
    """
    from ..storage.config_loader import (
        obtener_caso_meta, listar_piezas_caso, cargar_parametros_caso,
        guardar_analisis_caso,
    )
    from ..utils.llm_client import redactar_con_sistema
    from ..utils.timezone_pe import now_pe_iso

    meta = obtener_caso_meta(db_path, reporte_id)
    if not meta:
        return {"estado": "error", "nota": "El caso no tiene metadatos"}
    par = cargar_parametros_caso(db_path)
    piezas = listar_piezas_caso(db_path, reporte_id)
    citables = piezas_citables(piezas)
    ventana = meta.get("ventana_dias") or 7
    escenarios = meta.get("escenarios_candidatos") or []

    if not citables:
        return {"estado": "error",
                "nota": "El expediente no tiene piezas citables (incluidas y con "
                        "extracción lista). Añade material antes de analizar."}

    mat = material_caso_para_llm(meta, citables, par)
    prompt = (par.get("prompt_maestro") or "").strip()
    if not prompt:
        return {"estado": "error",
                "nota": "CASO_PROMPT_MAESTRO no configurado"}

    salida, err = redactar_con_sistema(
        prompt, mat["texto"], max_tokens=par.get("max_tokens", 4000),
        model=par.get("modelo", "claude-sonnet-4-6"), reintentos=1,
        timeout_s=par.get("timeout_s", 180))
    if not salida:
        # Honestidad: sin respuesta del modelo NO se generan secciones inventadas.
        return {"estado": "error",
                "nota": f"El modelo no respondió ({err}). No se generaron "
                        f"secciones: el reporte no inventa contenido.",
                "material": {"piezas_citables": len(citables), **{
                    k: mat[k] for k in ("enviadas", "omitidas")}}}

    mapa = {c["id_cita"]: c["pieza"] for c in citables}
    secciones_brutas = parsear_secciones_caso(_sanitizar(salida))
    faltan = secciones_faltantes_caso(secciones_brutas)
    if faltan:
        log.warning("CASO %s: secciones no detectadas: %s", reporte_id, ", ".join(faltan))

    cuerpo_ii = next((c for (s, c) in secciones_brutas
                      if s == "II. LA PREGUNTA Y EL MATERIAL"), "")
    silencios = detectar_silencios(cuerpo_ii, escenarios)

    secciones, ids_usados, ids_invalidos, descartadas = [], [], [], []
    for nombre, cuerpo in secciones_brutas:
        res = resolver_atribuciones(cuerpo, mapa, ventana)
        secciones.append({"nombre": nombre, "cuerpo": res["texto"]})
        for i in res["ids_usados"]:
            if i not in ids_usados:
                ids_usados.append(i)
        for i in res["ids_invalidos"]:
            if i not in ids_invalidos:
                ids_invalidos.append(i)
        for f in res.get("descartadas", []):
            descartadas.append({"seccion": nombre, "texto": f})

    hechos = []
    for c in citables:
        if c["id_cita"] not in ids_usados:
            continue
        p = c["pieza"]
        hechos.append({
            "id_cita": c["id_cita"],
            "titulo": _titulo_pieza(p),
            "procedencia": p.get("procedencia"),
            "fuente": p.get("fuente") or "",
            "fecha": _fecha_legible(p),
            "url": p.get("url") or "",
            "nombre_archivo": p.get("nombre_archivo") or "",
            "atribucion": atribucion_pieza(p, ventana),
        })

    analisis = {
        "estado": "ok",
        "version_arnes": "caso.4a",
        "pregunta": meta.get("pregunta"),
        "ventana_dias": ventana,
        "escenarios": escenarios,
        "secciones": secciones,
        "hechos_citados": hechos,
        "silencios": silencios,
        "ids_invalidos": ids_invalidos,
        "afirmaciones_descartadas": descartadas,
        "secciones_faltantes": faltan,
        "material": {"piezas_citables": len(citables),
                     "enviadas": mat["enviadas"], "omitidas": mat["omitidas"],
                     "modelo": par.get("modelo")},
        "generado_en": now_pe_iso(),
    }
    guardar_analisis_caso(db_path, reporte_id, analisis)
    return analisis


# ── Dump legible (validación sin PDF) ─────────────────────────────────────────
def dump_legible(analisis: dict) -> str:
    """Vuelca el análisis como texto plano legible, para que el Coronel valide
    las 4 secciones, cada atribución y los silencios SIN diseño THALOS."""
    if not analisis:
        return "(sin análisis generado)"
    if analisis.get("estado") != "ok":
        return (f"ESTADO: {analisis.get('estado')}\n"
                f"{analisis.get('nota') or ''}\n")
    L = []
    L.append("=" * 74)
    L.append("REPORTE POLÍTICO POR CASO — ANÁLISIS DESCRIPTIVO (secciones I-IV)")
    L.append("=" * 74)
    L.append(f"Pregunta : {analisis.get('pregunta')}")
    L.append(f"Ventana  : {analisis.get('ventana_dias')} días")
    m = analisis.get("material", {})
    L.append(f"Material : {m.get('piezas_citables')} piezas citables · "
             f"{m.get('enviadas')} enviadas · {m.get('omitidas')} omitidas por tope "
             f"· modelo {m.get('modelo')}")
    L.append(f"Generado : {analisis.get('generado_en')} (America/Lima)")
    L.append("")
    for s in analisis.get("secciones", []):
        L.append("-" * 74)
        L.append(s["nombre"])
        L.append("-" * 74)
        L.append(s["cuerpo"] or "(vacío)")
        L.append("")
    L.append("=" * 74)
    L.append("SILENCIOS (escenarios sin ninguna pieza que los sostenga)")
    L.append("=" * 74)
    sil = analisis.get("silencios") or []
    L += [f"  · {s}" for s in sil] if sil else ["  (ninguno: todos los escenarios "
                                                "tienen material que los sostiene)"]
    L.append("")
    L.append("=" * 74)
    L.append("HECHOS CITADOS (resueltos por ID, con su atribución del sistema)")
    L.append("=" * 74)
    for h in analisis.get("hechos_citados", []):
        ref = h.get("url") or h.get("nombre_archivo") or "—"
        L.append(f"[{h['id_cita']}] ({h['procedencia']}) {h['titulo']}")
        L.append(f"      atribución: \"{h['atribucion']}...\"")
        L.append(f"      referencia: {ref}")
    if not analisis.get("hechos_citados"):
        L.append("  (ninguno)")
    inval = analisis.get("ids_invalidos") or []
    desc = analisis.get("afirmaciones_descartadas") or []
    if inval or desc:
        L.append("")
        L.append("=" * 74)
        L.append("CONTROL DEL ARNÉS")
        L.append("=" * 74)
    if inval:
        L.append(f"IDs INVENTADOS POR EL MODELO (descartados): {', '.join(inval)}")
    if desc:
        L.append("AFIRMACIONES SUPRIMIDAS por quedarse sin respaldo (su único ID "
                 "era inventado):")
        for d in desc:
            L.append(f"  · [{d['seccion']}] {d['texto'][:150]}")
    falt = analisis.get("secciones_faltantes") or []
    if falt:
        L.append(f"SECCIONES NO DETECTADAS: {', '.join(falt)}")
    return "\n".join(L)
