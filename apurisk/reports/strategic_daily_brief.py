"""Reporte Diario de Riesgo Político · Perú — Inteligencia Estratégica.

Producto C-level Capa 2 (Strategic Intelligence) — máximo 2 páginas A4.

Enfoque editorial: estrictamente político-institucional.
Incluye: Congreso, JNJ, TC, gobernabilidad, poderes del Estado, +
crimen organizado (corrupción, capacidad de respuesta estatal).
Excluye: logística pura, frontera no-política, conflictos
puramente operacionales sin derivada institucional.

Estructura:
  Página 1 — Diagnóstico
    Header THALOS + tagline · Título · Velocímetro Riesgo Nacional
    + Card EDI compacto · Executive Insight (memo LLM)
  Página 2 — Lectura prospectiva
    Status Nacional (4 dimensiones grid 2x2) · Top 5 amenazas
    políticas filtradas · 6 implicancias operacionales compactas
"""
from __future__ import annotations
import math
from datetime import datetime
from pathlib import Path as PathLib
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, Flowable, Image,
)
from reportlab.graphics.shapes import (
    Drawing, Path, Circle, String, Line, Rect, Group, Polygon, Ellipse,
)
from reportlab.pdfbase.pdfmetrics import stringWidth


# =====================================================================
# UTILIDADES — fecha en español y carga del logo completo
# =====================================================================
DIAS_ES = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
MESES_ES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
            "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]


def _fecha_estilizada(fecha_iso: str) -> tuple:
    """Convierte '2026-06-04' → ('06:00 hrs', 'MARTES', '04 JUNIO 2026')."""
    try:
        dt = datetime.strptime(fecha_iso[:10], "%Y-%m-%d")
        dia = DIAS_ES[dt.weekday()]
        mes = MESES_ES[dt.month - 1]
        return ("06:00 hrs", dia, f"{dt.day:02d} {mes} {dt.year}")
    except Exception:
        return ("06:00 hrs", "—", fecha_iso[:10])


def _cargar_logo_full(target_height_pt: float = 60):
    """Carga el wordmark THALOS completo (texto + globo + tagline).

    Estrategia: PNG primero (predecible en cualquier entorno). svglib quedó
    descartado tras observar en producción que renderizaba sólo el globo y
    desconfiguraba el texto "THALOS".
    """
    base = PathLib(__file__).parent.parent / "static"
    png_path = base / "thalos-full-logo.png"
    if png_path.exists():
        try:
            from PIL import Image as PILImage
            with PILImage.open(str(png_path)) as im:
                w, h = im.size
            aspect = w / h
            return Image(str(png_path), width=target_height_pt * aspect,
                          height=target_height_pt)
        except Exception:
            return Image(str(png_path), width=target_height_pt * 3.5,
                          height=target_height_pt)
    return None


# =====================================================================
# PALETA CORPORATIVA
# =====================================================================
# NAVY (texto body): casi negro, máxima legibilidad
NAVY = colors.HexColor("#0f172a")
# NAVY_BRAND: azul navy reconocible para títulos, líneas y fondos de marca
NAVY_BRAND = colors.HexColor("#1e3a8a")
NAVY_LIGHT = colors.HexColor("#1e293b")
ACCENT = colors.HexColor("#3b82f6")
PROSPECTIVO = colors.HexColor("#a855f7")
TXT_SECONDARY = colors.HexColor("#475569")
TXT_TERTIARY = colors.HexColor("#94a3b8")
BG_LIGHT = colors.HexColor("#f8fafc")
BG_CARD = colors.HexColor("#f1f5f9")
BG_INSIGHT = colors.HexColor("#eff6ff")
BORDER = colors.HexColor("#e2e8f0")

# Bandas semáforo
ESTABLE = colors.HexColor("#22c55e")
BAJO = colors.HexColor("#84cc16")
MODERADO = colors.HexColor("#f59e0b")
ELEVADO = colors.HexColor("#f97316")
CRITICO = colors.HexColor("#ef4444")

COLOR_MAP = {
    "verde": ESTABLE, "verde-amarillo": BAJO, "ambar": MODERADO,
    "naranja": ELEVADO, "rojo": CRITICO,
}


def _color(token: str):
    return COLOR_MAP.get(token or "ambar", MODERADO)


def _fmt_num(v, decimals=1):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


# =====================================================================
# FILTRO POLÍTICO ESTRICTO — palabras clave que pertenecen al recorte
# =====================================================================
CATEGORIAS_POLITICAS = {
    "política", "politica", "institucional", "judicial", "congreso",
    "ejecutivo", "gobernabilidad", "constitucional", "electoral",
    "tc", "jnj", "pj", "jne", "contraloría", "contraloria",
    "corrupción", "corrupcion", "estado", "fiscalía", "fiscalia",
    "crimen organizado", "seguridad institucional", "asesinatos",
    "sicariato", "extorsión política", "extorsion politica",
    "tribunal constitucional", "junta nacional",
}

KEYWORDS_POLITICOS = {
    "jnj", "tc ", "tribunal constitucional", "junta nacional",
    "congreso", "ejecutivo", "ministro", "ministerio",
    "moción", "vacancia", "censura", "interpelación",
    "fiscalía", "fiscalia", "fiscal", "corrupción", "corrupcion",
    "contraloría", "contraloria", "asesinato", "sicariato",
    "extorsión", "extorsion", "crimen organizado", "narco",
    "constitucional", "judicial", "magistrado", "destitución",
    "destitucion", "denuncia constitucional", "lava jato",
    "boluarte", "presidenta", "presidente", "premier",
    "ejecutivo", "poder judicial", "jne", "ministerio público",
    "ministerio publico", "operativo policial", "policía",
    "policia", "gobernabilidad", "gobierno",
}


def _es_amenaza_politica(amenaza: dict) -> bool:
    """Whitelist política estricta sobre nombre + categoría + narrativa."""
    cat = str(amenaza.get("categoria", "")).lower()
    nombre = str(amenaza.get("nombre", "")).lower()
    narr = str(amenaza.get("narrativa", "")).lower()

    # 1) Match directo por categoría
    if any(p in cat for p in CATEGORIAS_POLITICAS):
        return True
    # 2) Match por keyword político en nombre/narrativa
    blob = f"{nombre} {narr}"
    if any(k in blob for k in KEYWORDS_POLITICOS):
        return True
    return False


def _filtrar_amenazas_politicas(amenazas: list, n: int = 5) -> list:
    """Devuelve top n amenazas políticas; rellena con el resto si no hay n."""
    pol = [a for a in amenazas if _es_amenaza_politica(a)]
    if len(pol) >= n:
        return pol[:n]
    # Si faltan, completa con las restantes priorizadas (en orden original)
    resto = [a for a in amenazas if a not in pol]
    return (pol + resto)[:n]


# =====================================================================
# ACTORES POLÍTICOS — extracción de amenazas usando whitelist institucional
# =====================================================================
# Diccionario de actores canónicos con sus aliases conocidos.
# El sistema busca cualquier alias en el nombre/narrativa de las amenazas y
# atribuye la mención al actor canónico (dedup).
ACTORES_CANONICOS = [
    # Ejecutivo
    {"canonico": "Dina Boluarte", "rol": "Presidenta de la República",
     "aliases": ["dina boluarte", "boluarte", "presidenta boluarte",
                  "jefa de estado", "presidenta del perú"],
     "tipo": "ejecutivo"},
    {"canonico": "Eduardo Arana", "rol": "Premier",
     "aliases": ["eduardo arana", "arana ysa", "premier arana",
                  "presidente del consejo de ministros"],
     "tipo": "ejecutivo"},
    {"canonico": "Gustavo Adrianzén", "rol": "Ex Premier",
     "aliases": ["adrianzén", "adrianzen", "gustavo adrianzén"],
     "tipo": "ejecutivo"},
    # Congreso
    {"canonico": "Eduardo Salhuana", "rol": "Presidente del Congreso",
     "aliases": ["salhuana", "eduardo salhuana", "presidente del congreso"],
     "tipo": "congreso"},
    {"canonico": "Bancada APP", "rol": "Bancada parlamentaria",
     "aliases": ["alianza para el progreso", "bancada app", "app "],
     "tipo": "congreso"},
    {"canonico": "Bancada Fuerza Popular", "rol": "Bancada parlamentaria",
     "aliases": ["fuerza popular", "fujimorismo", "keiko fujimori"],
     "tipo": "congreso"},
    {"canonico": "Bancada Perú Libre", "rol": "Bancada parlamentaria",
     "aliases": ["perú libre", "peru libre", "cerrón", "cerron"],
     "tipo": "congreso"},
    # Junta Nacional de Justicia
    {"canonico": "Junta Nacional de Justicia",
     "rol": "Órgano constitucional autónomo",
     "aliases": ["jnj", "junta nacional de justicia"],
     "tipo": "jnj"},
    # Tribunal Constitucional
    {"canonico": "Tribunal Constitucional",
     "rol": "Tribunal Constitucional",
     "aliases": ["tc ", "tribunal constitucional", "magistrados del tc"],
     "tipo": "tc"},
    # Fiscalía
    {"canonico": "Delia Espinoza", "rol": "Fiscal de la Nación",
     "aliases": ["delia espinoza", "fiscal de la nación", "fiscalía de la nación"],
     "tipo": "fiscalia"},
    {"canonico": "Patricia Benavides", "rol": "Ex Fiscal de la Nación",
     "aliases": ["patricia benavides", "benavides"],
     "tipo": "fiscalia"},
    # Poder Judicial
    {"canonico": "Poder Judicial", "rol": "Poder Judicial",
     "aliases": ["poder judicial", "corte suprema", "javier arévalo",
                  "presidente del poder judicial"],
     "tipo": "pj"},
    # JNE
    {"canonico": "JNE", "rol": "Jurado Nacional de Elecciones",
     "aliases": ["jne", "jurado nacional de elecciones", "roberto burneo"],
     "tipo": "jne"},
    # Contraloría
    {"canonico": "Contraloría General",
     "rol": "Contraloría General de la República",
     "aliases": ["contraloría", "contraloria", "nelson shack",
                  "contralor general"],
     "tipo": "contraloria"},
    # Ministerios clave
    {"canonico": "Ministerio del Interior", "rol": "Sector Interior",
     "aliases": ["mininter", "ministro del interior", "ministerio del interior"],
     "tipo": "ejecutivo"},
    {"canonico": "Ministerio de Economía", "rol": "Sector Economía",
     "aliases": ["mef", "ministra de economía", "ministro de economía",
                  "ministerio de economía"],
     "tipo": "ejecutivo"},
    {"canonico": "Ministerio de Energía y Minas", "rol": "Sector MINEM",
     "aliases": ["minem", "ministro de energía", "ministra de energía",
                  "ministerio de energía"],
     "tipo": "ejecutivo"},
    # Crimen organizado (institucional)
    {"canonico": "Policía Nacional", "rol": "PNP · Seguridad ciudadana",
     "aliases": ["pnp", "policía nacional", "policia nacional",
                  "comandante general pnp", "operativo policial"],
     "tipo": "seguridad"},
    # Actores genéricos (fallback cuando narrativas no nombran personas)
    {"canonico": "Poder Ejecutivo", "rol": "Gobierno central",
     "aliases": ["ejecutivo", "gobierno central", "gobierno nacional",
                  "consejo de ministros", "palacio de gobierno"],
     "tipo": "ejecutivo"},
    {"canonico": "Congreso de la República", "rol": "Poder Legislativo",
     "aliases": ["congreso de la república", "congreso de la republica",
                  "pleno del congreso", "bancadas opositoras",
                  "mayoría parlamentaria", "mayoria parlamentaria",
                  "comisión permanente", "comision permanente"],
     "tipo": "congreso"},
    {"canonico": "Sector Energía y Minas", "rol": "MINEM · Sector extractivo",
     "aliases": ["sector minero", "sector energético", "sector energetico",
                  "operadores mineros", "concesiones mineras"],
     "tipo": "ejecutivo"},
    {"canonico": "Comunidades del Corredor Sur",
     "rol": "Federaciones campesinas · Sur",
     "aliases": ["comunidades", "federaciones campesinas", "corredor sur",
                  "rondas campesinas", "dirigentes comunales"],
     "tipo": "social"},
    {"canonico": "SUNARP", "rol": "Registro de propiedad",
     "aliases": ["sunarp", "registros públicos", "registros publicos"],
     "tipo": "regulatorio"},
    {"canonico": "Minería ilegal", "rol": "Actor delictivo",
     "aliases": ["minería ilegal", "mineria ilegal", "minería informal",
                  "mineros informales", "minería artesanal"],
     "tipo": "delictivo"},
]

# Orden de severidad para color del badge "Exposición"
NIVEL_ORDEN = {"CRÍTICA": 5, "ALTO": 4, "MEDIO": 3, "BAJO": 2, "": 1}


# Mapeo de categoría de amenaza → actores inferidos por defecto
# Si una amenaza pertenece a una categoría conocida, sus actores quedan vinculados
# aunque la narrativa no los nombre explícitamente.
CATEGORIA_A_ACTORES = {
    # Conflictos sociales / comunidades
    "conflictos sociales": ["Comunidades del Corredor Sur", "Sector Energía y Minas"],
    "conflicto social": ["Comunidades del Corredor Sur", "Sector Energía y Minas"],
    "conflictos sociales / corredor sur": ["Comunidades del Corredor Sur"],
    # Estabilidad gubernamental / política
    "estabilidad gubernamental": ["Poder Ejecutivo", "Congreso de la República"],
    "estabilidad gubernamental / seguridad": ["Poder Ejecutivo", "Policía Nacional"],
    "política": ["Poder Ejecutivo", "Congreso de la República"],
    "politica": ["Poder Ejecutivo", "Congreso de la República"],
    "gobernabilidad": ["Poder Ejecutivo", "Congreso de la República"],
    # Conflicto institucional
    "conflicto institucional": ["Congreso de la República", "Poder Ejecutivo"],
    # Seguridad
    "seguridad": ["Policía Nacional", "Ministerio del Interior"],
    "seguridad ciudadana": ["Policía Nacional", "Ministerio del Interior"],
    # Crimen organizado
    "crimen organizado": ["Policía Nacional", "Minería ilegal"],
    "narco": ["Policía Nacional"],
    # Constitucional / Judicial
    "constitucional": ["Tribunal Constitucional", "Congreso de la República"],
    "judicial": ["Junta Nacional de Justicia", "Poder Judicial"],
    # Regulatorio
    "regulatorio": ["SUNARP", "Sector Energía y Minas"],
    # Electoral
    "electoral": ["JNE", "Congreso de la República"],
}


def _actor_canonico_por_nombre(nombre: str) -> dict:
    """Devuelve el dict canónico de ACTORES_CANONICOS por nombre."""
    for ad in ACTORES_CANONICOS:
        if ad["canonico"] == nombre:
            return ad
    return None


def _extraer_actores_politicos(brief: dict, top_n: int = 6) -> list:
    """Extrae actores políticos vinculados a las amenazas del brief.

    Doble estrategia:
      1) Match por alias en nombre+narrativa (preciso — pesa más en ranking)
      2) Match por categoría de la amenaza (inferencia — garantiza cobertura
         cuando las narrativas LLM hablan en abstracto)

    Returns:
        Lista de dicts {canonico, rol, peor_amenaza, peor_nivel, n_menciones}.
    """
    amenazas = brief.get("amenazas_prioritarias", []) or []
    contador = {}

    def _bump(canonico: str, info: dict, nivel: str, nombre_amen: str,
              peso: int = 1):
        if canonico not in contador:
            contador[canonico] = {
                "canonico": canonico,
                "rol": info["rol"] if info else canonico,
                "tipo": info["tipo"] if info else "ejecutivo",
                "n_menciones": 0,
                "peor_nivel": "",
                "peor_amenaza": "",
            }
        contador[canonico]["n_menciones"] += peso
        score = NIVEL_ORDEN.get(nivel, 0)
        if score > NIVEL_ORDEN.get(contador[canonico]["peor_nivel"], 0):
            contador[canonico]["peor_nivel"] = nivel or "MEDIO"
            contador[canonico]["peor_amenaza"] = nombre_amen[:60]

    for a in amenazas:
        nombre = str(a.get("nombre", ""))
        narrativa = str(a.get("narrativa", ""))
        categoria = str(a.get("categoria", "")).lower().strip()
        nivel = str(a.get("nivel", "")).upper()
        blob = f"{nombre} {narrativa}".lower()

        # Estrategia 1 — alias directo (peso 2, más confiable)
        for actor_def in ACTORES_CANONICOS:
            if any(alias in blob for alias in actor_def["aliases"]):
                _bump(actor_def["canonico"], actor_def, nivel, nombre, peso=2)

        # Estrategia 2 — inferencia por categoría (peso 1)
        # Probar match exacto de categoría y también palabras clave en categoría
        cat_matches = []
        if categoria in CATEGORIA_A_ACTORES:
            cat_matches = CATEGORIA_A_ACTORES[categoria]
        else:
            # Match parcial (ej: "Estabilidad gubernamental / Seguridad")
            for key, val in CATEGORIA_A_ACTORES.items():
                if key in categoria:
                    cat_matches.extend(val)
        # Dedup
        for actor_canonico in set(cat_matches):
            info = _actor_canonico_por_nombre(actor_canonico)
            _bump(actor_canonico, info, nivel, nombre, peso=1)

    items = list(contador.values())
    items.sort(
        key=lambda x: (NIVEL_ORDEN.get(x["peor_nivel"], 0), x["n_menciones"]),
        reverse=True,
    )
    return items[:top_n]


# =====================================================================
# VELOCÍMETRO SEMICIRCULAR — Drawing nativo ReportLab
# =====================================================================
class GaugeRiesgo(Flowable):
    """Velocímetro semicircular sobrio para Score 0-100.

    5 bandas de color (ESTABLE/BAJO/MODERADO/ELEVADO/CRÍTICO),
    aguja al valor actual, label central con valor + etiqueta.
    """

    def __init__(self, score: float, etiqueta: str, ancho: float = 9 * cm,
                 alto: float = 5.6 * cm):
        Flowable.__init__(self)
        self.score = max(0.0, min(100.0, float(score)))
        self.etiqueta = etiqueta
        self.width = ancho
        self.height = alto

    def wrap(self, *_):
        return (self.width, self.height)

    def _polar_xy(self, cx, cy, r, ang_deg):
        rad = math.radians(ang_deg)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)

    def _band_path(self, cx, cy, r_inner, r_outer, ang0, ang1):
        """Path para un anillo entre dos ángulos (en grados, 0=derecha, 180=izq)."""
        p = Path(fillColor=None, strokeColor=None)
        # arco externo de ang0 a ang1, luego arco interno de ang1 a ang0
        steps = 24
        # Punto inicial
        x, y = self._polar_xy(cx, cy, r_outer, ang0)
        p.moveTo(x, y)
        for i in range(1, steps + 1):
            a = ang0 + (ang1 - ang0) * i / steps
            x, y = self._polar_xy(cx, cy, r_outer, a)
            p.lineTo(x, y)
        # cerrar bajando al radio interno
        x, y = self._polar_xy(cx, cy, r_inner, ang1)
        p.lineTo(x, y)
        for i in range(1, steps + 1):
            a = ang1 - (ang1 - ang0) * i / steps
            x, y = self._polar_xy(cx, cy, r_inner, a)
            p.lineTo(x, y)
        p.closePath()
        return p

    def draw(self):
        d = Drawing(self.width, self.height)
        cx = self.width / 2
        # Centro del arco más arriba para dejar espacio al score+etiqueta debajo
        cy = self.height * 0.45
        r_outer = min(self.width / 2 * 0.85, self.height * 0.50)
        r_inner = r_outer * 0.62

        # 5 bandas de 36° cada una (de 180° a 0°)
        bandas = [
            (180, 144, ESTABLE),
            (144, 108, BAJO),
            (108, 72, MODERADO),
            (72, 36, ELEVADO),
            (36, 0, CRITICO),
        ]
        for a0, a1, c in bandas:
            path = self._band_path(cx, cy, r_inner, r_outer, a0, a1)
            path.fillColor = c
            path.strokeColor = colors.white
            path.strokeWidth = 1
            d.add(path)

        # Marcas numéricas (0, 20, 40, 60, 80, 100)
        for i, val in enumerate([0, 20, 40, 60, 80, 100]):
            ang = 180 - (val / 100.0) * 180  # 0→180°, 100→0°
            r_label = r_outer + 0.25 * cm
            lx, ly = self._polar_xy(cx, cy, r_label, ang)
            s = String(lx, ly, str(val), fontName="Helvetica",
                        fontSize=7, fillColor=TXT_TERTIARY,
                        textAnchor="middle")
            d.add(s)

        # Aguja: rectángulo delgado rotado al ángulo del score
        ang_aguja = 180 - (self.score / 100.0) * 180
        # Punto extremo de la aguja
        tip_x, tip_y = self._polar_xy(cx, cy, r_outer * 0.95, ang_aguja)
        # Base de la aguja (triángulo)
        ang_perp = ang_aguja + 90
        base_w = 0.10 * cm
        bx1, by1 = self._polar_xy(cx, cy, base_w, ang_perp)
        bx2, by2 = self._polar_xy(cx, cy, base_w, ang_perp + 180)
        aguja = Polygon(points=[bx1, by1, tip_x, tip_y, bx2, by2],
                          fillColor=NAVY_BRAND, strokeColor=NAVY_BRAND, strokeWidth=0.5)
        d.add(aguja)
        # Hub central
        d.add(Circle(cx, cy, 0.18 * cm, fillColor=NAVY_BRAND,
                       strokeColor=colors.white, strokeWidth=1.2))

        # Score y etiqueta DEBAJO del semicírculo (no encima)
        score_color = self._color_by_score()
        # Score: a 0.65cm desde abajo
        d.add(String(cx, 0.65 * cm, f"{self.score:.0f}",
                       fontName="Helvetica-Bold", fontSize=22,
                       fillColor=score_color, textAnchor="middle"))
        # Etiqueta: en la base, claramente separada
        d.add(String(cx, 0.10 * cm, self.etiqueta.upper(),
                       fontName="Helvetica-Bold", fontSize=8,
                       fillColor=score_color, textAnchor="middle"))

        d.drawOn(self.canv, 0, 0)

    def _color_by_score(self):
        if self.score < 20: return ESTABLE
        if self.score < 40: return BAJO
        if self.score < 60: return MODERADO
        if self.score < 80: return ELEVADO
        return CRITICO


# =====================================================================
# THALOS TEXT LOGO — "THAL[globo]S" con el globo como letra O
# =====================================================================
class ThalosTextLogo(Flowable):
    """Renderiza el wordmark THALOS reemplazando la 'O' por el globo de la marca.

    Composición vectorial:
      [T] [H] [A] [L]  ⊕  [S]
    donde ⊕ es un círculo navy con dos ondas (latitudes) cruzándolo.
    """

    def __init__(self, font_size: float = 18, color=NAVY):
        Flowable.__init__(self)
        self.font_size = font_size
        self.color = color
        self.font_name = "Helvetica-Bold"
        # Letras a renderizar: T H A L  [O=globo]  S
        self.text_left = "THAL"
        self.text_right = "S"
        # Ancho del globo ~ font_size (más algo de aire)
        self.globe_size = font_size * 0.78
        # Gaps
        self.gap = font_size * 0.06
        # Pre-cálculo de anchos
        self.w_left = stringWidth(self.text_left, self.font_name, font_size)
        self.w_right = stringWidth(self.text_right, self.font_name, font_size)
        self.width = (self.w_left + self.gap + self.globe_size
                       + self.gap + self.w_right)
        self.height = font_size * 1.05

    def wrap(self, *_):
        return (self.width, self.height)

    def _try_svglib_drawing(self):
        """Intenta cargar thalos-mark.svg vía svglib. None si no disponible."""
        try:
            from svglib.svglib import svg2rlg
            from pathlib import Path
            svg_path = Path(__file__).parent.parent / "static" / "thalos-mark.svg"
            if svg_path.exists():
                d = svg2rlg(str(svg_path))
                return d
        except Exception:
            return None
        return None

    def draw(self):
        c = self.canv
        baseline = self.font_size * 0.12
        c.saveState()
        c.setFillColor(self.color)
        c.setStrokeColor(self.color)
        c.setFont(self.font_name, self.font_size)

        # Texto izquierdo "THAL"
        c.drawString(0, baseline, self.text_left)

        # Globo en lugar de la O
        gx0 = self.w_left + self.gap
        gy0 = baseline + self.font_size * 0.06
        gs = self.globe_size

        # Intento svglib primero (vector fiel al símbolo oficial THALOS)
        d = self._try_svglib_drawing()
        if d is not None:
            # Escalar el Drawing al tamaño del globo
            sx_factor = gs / d.width if d.width else 1.0
            sy_factor = gs / d.height if d.height else 1.0
            scale = min(sx_factor, sy_factor)
            d.scale(scale, scale)
            d.drawOn(c, gx0, gy0)
        else:
            # Fallback procedural: círculo + 5 ondas horizontales tipo "S tumbada"
            # más fiel al símbolo oficial que las 3 ondas simples
            cx = gx0 + gs / 2
            cy = gy0 + gs / 2
            r = gs / 2
            # Círculo perfil grueso
            c.setLineWidth(self.font_size * 0.085)
            c.circle(cx, cy, r, stroke=1, fill=0)
            # 5 ondas curvas horizontales (latitudes ondulantes)
            c.setLineWidth(self.font_size * 0.05)
            ondulaciones = [
                (cy + r * 0.55, r * 0.50, r * 0.06),   # superior
                (cy + r * 0.25, r * 0.78, r * 0.07),   # alta
                (cy,            r * 0.92, r * 0.08),   # ecuador
                (cy - r * 0.25, r * 0.78, r * 0.07),   # baja
                (cy - r * 0.55, r * 0.50, r * 0.06),   # inferior
            ]
            for y, half_w, amp in ondulaciones:
                # curva tipo S: bezier que sube luego baja
                p = c.beginPath()
                p.moveTo(cx - half_w, y - amp)
                p.curveTo(cx - half_w * 0.4, y + amp * 1.2,
                           cx + half_w * 0.4, y - amp * 1.2,
                           cx + half_w, y + amp)
                c.drawPath(p, stroke=1, fill=0)

        # Texto derecho "S"
        sx = gx0 + gs + self.gap
        c.setFillColor(self.color)
        c.drawString(sx, baseline, self.text_right)

        c.restoreState()


# =====================================================================
# ESTILOS DE PÁRRAFO
# =====================================================================
def _build_styles():
    base = getSampleStyleSheet()
    return {
        "report_title": ParagraphStyle(
            "report_title", parent=base["Title"],
            fontSize=19, leading=22, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=1,
        ),
        # Subtítulo del producto ("Inteligencia Estratégica · Producto C-Level")
        # ahora en NAVY_BRAND (#1e3a8a) — azul navy reconocible
        "report_subtitle": ParagraphStyle(
            "report_subtitle", parent=base["Normal"],
            fontSize=12, leading=14, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=0,
        ),
        "report_meta": ParagraphStyle(
            "report_meta", parent=base["Normal"],
            fontSize=8.5, leading=10, textColor=TXT_TERTIARY,
            fontName="Helvetica", alignment=TA_LEFT, spaceAfter=8,
        ),
        # Etiqueta pequeña que va sobre los títulos de sección — NAVY_BRAND
        "section_label": ParagraphStyle(
            "section_label", parent=base["Normal"],
            fontSize=9, leading=11, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=2,
            spaceBefore=8,
        ),
        # TÍTULOS DE SECCIÓN — 13pt NAVY_BRAND (azul navy visible)
        "section_title": ParagraphStyle(
            "section_title", parent=base["Heading2"],
            fontSize=13, leading=15, textColor=NAVY_BRAND,
            fontName="Helvetica-Bold", alignment=TA_LEFT, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontSize=9.5, leading=12.5, textColor=NAVY,
            fontName="Helvetica", alignment=TA_JUSTIFY, spaceAfter=4,
        ),
        "insight_text": ParagraphStyle(
            "insight_text", fontSize=11, leading=15.5, textColor=NAVY,
            fontName="Helvetica", alignment=TA_JUSTIFY,
            leftIndent=10, rightIndent=10,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"],
            fontSize=8, leading=10, textColor=TXT_TERTIARY,
            fontName="Helvetica", alignment=TA_LEFT,
        ),
    }


# =====================================================================
# HEADER COMPACTO — Logo THALOS completo izquierda + fecha estilizada derecha
# =====================================================================
def _header_compacto(styles, fecha_iso: str, etiqueta_corte: str = "FECHA DE CORTE"):
    """Logo full a la izquierda; fecha estilizada en card navy a la derecha.

    Header balanceado: logo y card de fecha tienen altura visual equivalente
    (~70pt = ~2.5 cm), alineados al centro vertical, con tamaños proporcionados.

    Args:
        etiqueta_corte: label superior del card de fecha. Por defecto "FECHA DE CORTE"
                       (reporte 06:00 AM). El reporte 24h on-demand usa "GENERADO".
    """
    # Logo: altura 70pt — proporcionada con la card de fecha
    logo = _cargar_logo_full(target_height_pt=70)
    if logo is None:
        logo = ThalosTextLogo(font_size=24, color=NAVY)

    # Fecha estilizada en card navy — alturas alineadas con el logo
    hora, dia, fecha_larga = _fecha_estilizada(fecha_iso)
    fecha_card = Table(
        [
            [Paragraph(
                f"<font color='#cbd5e1'><b>{etiqueta_corte}</b></font>",
                ParagraphStyle("fc1", fontSize=7, leading=9, alignment=TA_CENTER)
            )],
            [Paragraph(
                f"<font color='#ffffff'><b>{hora}</b></font>  "
                f"<font color='#cbd5e1'>· {dia}</font>",
                ParagraphStyle("fc2", fontSize=13, leading=15, alignment=TA_CENTER)
            )],
            [Paragraph(
                f"<font color='#ffffff'><b>{fecha_larga}</b></font>",
                ParagraphStyle("fc4", fontSize=11, leading=13, alignment=TA_CENTER)
            )],
        ],
        colWidths=[6 * cm],
        rowHeights=[0.55 * cm, 0.7 * cm, 0.65 * cm],  # alto total ~1.9cm ≈ 70pt
    )
    fecha_card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY_BRAND),
        ("BOX", (0, 0), (-1, -1), 0.5, NAVY_BRAND),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    # Header: ambos elementos centrados verticalmente al medio del rowHeight
    header = Table(
        [[logo, fecha_card]],
        colWidths=[10.5 * cm, 6.5 * cm],
        rowHeights=[2.0 * cm],   # forzar misma altura visual para ambos
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


# =====================================================================
# PÁGINA 1 — DIAGNÓSTICO
# =====================================================================
def _pagina_1_diagnostico(brief, styles, modo: str = "diario"):
    """Página 1 del reporte — usa componentes de Plantilla Madre v1.0.

    Args:
        modo: "diario" (Reporte 06:00 — header dice FECHA DE CORTE) o
              "on_demand_24h" (Reporte 24h — header dice GENERADO + hora real)
    """
    # Import de la Plantilla Madre (componentes canónicos)
    try:
        from .plantilla_madre import (
            header_canonico, linea_separadora_navy,
            bloque_visual_riesgo, card_insight as pm_card_insight,
            estilos_canonicos, label_bloque_visual, color_por_token,
        )
    except ImportError:
        from apurisk.reports.plantilla_madre import (
            header_canonico, linea_separadora_navy,
            bloque_visual_riesgo, card_insight as pm_card_insight,
            estilos_canonicos, label_bloque_visual, color_por_token,
        )

    elems = []
    fecha = (brief.get("generado_en", "") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    pm_styles = estilos_canonicos()

    # ============== HEADER CANÓNICO ==============
    etiqueta = "GENERADO" if modo == "on_demand_24h" else "FECHA DE CORTE"
    elems.append(header_canonico(fecha, etiqueta_corte=etiqueta))
    elems.append(Spacer(1, 0.15 * cm))
    elems.append(linea_separadora_navy(thickness=1.5,
                                          space_before=0, space_after=4))

    # ============== TÍTULO H1 + SUBTÍTULO H2 ==============
    if modo == "on_demand_24h":
        titulo = "Reporte 24h de Riesgo Político · Perú"
        subtitulo = "Inteligencia Estratégica · On Demand"
    else:
        titulo = "Reporte Diario de Riesgo Político · Perú"
        subtitulo = "Inteligencia Estratégica · Producto C-Level"
    elems.append(Paragraph(titulo, pm_styles["h1"]))
    elems.append(Paragraph(subtitulo, pm_styles["h2"]))
    elems.append(Spacer(1, 0.2 * cm))

    # ============== ESCENARIO DE RIESGO — Bloque Visual ==============
    # SIEMPRE va antes del Insight: el cliente VE antes de LEER
    producto = "on_demand_24h" if modo == "on_demand_24h" else "daily"
    elems.append(Paragraph(
        f"◇ {label_bloque_visual(producto)}",
        pm_styles["label"]
    ))
    elems.append(Paragraph("Visualización ejecutiva del ciclo actual",
                             pm_styles["h3"]))

    # Extraer datos del brief
    status = brief.get("status_nacional", {}) or {}
    op = status.get("operacional_nacional", {}) or {}
    score = float(op.get("score") or 0)
    score_etiq = str(op.get("etiqueta", "—"))
    score_color = color_por_token(op.get("color"))

    tp = status.get("tendencia_pais", {}) or {}
    edi = brief.get("edi", {}) or {}
    edi_t = edi.get("tendencia", {}) or {}

    elems.append(bloque_visual_riesgo(
        score=score, etiqueta_riesgo=score_etiq, color_riesgo=score_color,
        tendencia_arrow=str(tp.get("arrow", "→")),
        tendencia_delta=tp.get("delta", 0) or 0,
        tendencia_label=str(tp.get("etiqueta", "—")),
        tendencia_color=color_por_token(tp.get("color")),
        edi_score=int(edi.get("edi") or 0),
        edi_etiqueta=str(edi.get("etiqueta", "—")),
        edi_arrow=str(edi_t.get("arrow", "→")),
        edi_delta=float(edi_t.get("delta_7d") or 0),
        edi_color=color_por_token(edi.get("color")),
    ))
    elems.append(Spacer(1, 0.4 * cm))

    # ============== LECTURA ESTRATÉGICA — Card Insight canónico ==============
    elems.append(Paragraph("⚡ LECTURA ESTRATÉGICA", pm_styles["label"]))
    elems.append(Paragraph("Insight del día", pm_styles["h3"]))

    insight = brief.get("executive_insight", {}) or {}
    texto = insight.get("insight", "")
    if not texto:
        texto = ("Sin insight estratégico destacado en el ciclo actual. "
                 "El cuadro político-institucional se mantiene dentro del "
                 "baseline operativo de las últimas semanas.")
    elems.append(pm_card_insight(texto))

    # Chips de señales analíticas (compacto)
    cats = insight.get("categorias_detectadas", []) or []
    if cats:
        chips = "  ".join(
            f"<font color='{NAVY_BRAND.hexval()}' size='7.5'>● {c}</font>"
            for c in cats[:6]
        )
        elems.append(Spacer(1, 0.1 * cm))
        elems.append(Paragraph(
            f"<font size='7' color='#94a3b8'><b>SEÑALES ANALÍTICAS</b></font>  {chips}",
            styles["meta"]
        ))

    # ========== TABLA ACTORES POLÍTICOS (24h) ==========
    # Usamos KeepTogether para que el bloque completo (label+título+tabla)
    # no se rompa entre páginas dejando filas huérfanas.
    from reportlab.platypus import KeepTogether
    actores = _extraer_actores_politicos(brief, top_n=5)
    elems.append(Spacer(1, 0.2 * cm))
    actores_block = []
    actores_block.append(Paragraph("ACTORES POLÍTICOS EN RIESGO", styles["section_label"]))
    actores_block.append(Paragraph(
        "Principales actores vinculados a las amenazas de las últimas 24 h",
        styles["section_title"]
    ))
    if actores:

        nivel_color = {
            "CRÍTICA": CRITICO, "ALTO": ELEVADO,
            "MEDIO": MODERADO, "BAJO": BAJO,
        }

        # Header — tamaños y wordings que no se parten en 2 líneas
        header_row = [
            Paragraph("<font color='#94a3b8' size='6.5'><b>ACTOR</b></font>",
                       ParagraphStyle("h1", fontSize=6.5, alignment=TA_LEFT)),
            Paragraph("<font color='#94a3b8' size='6.5'><b>ROL</b></font>",
                       ParagraphStyle("h2", fontSize=6.5, alignment=TA_LEFT)),
            Paragraph("<font color='#94a3b8' size='6.5'><b>VINCULADO A</b></font>",
                       ParagraphStyle("h3", fontSize=6.5, alignment=TA_LEFT)),
            Paragraph("<font color='#94a3b8' size='6.5'><b>RIESGO</b></font>",
                       ParagraphStyle("h4", fontSize=6.5, alignment=TA_CENTER)),
        ]
        rows = [header_row]
        for a in actores:
            nivel = a.get("peor_nivel", "MEDIO") or "MEDIO"
            col = nivel_color.get(nivel, MODERADO)
            rows.append([
                Paragraph(
                    f"<font color='#0f172a' size='9'><b>{a['canonico']}</b></font>",
                    ParagraphStyle("a1", fontSize=9, leading=11, alignment=TA_LEFT)
                ),
                Paragraph(
                    f"<font color='#475569' size='8.5'>{a['rol']}</font>",
                    ParagraphStyle("a2", fontSize=8.5, leading=10.5, alignment=TA_LEFT)
                ),
                Paragraph(
                    f"<font color='#475569' size='8.5'>{a['peor_amenaza']}</font>",
                    ParagraphStyle("a3", fontSize=8.5, leading=10.5, alignment=TA_LEFT)
                ),
                Paragraph(
                    f"<font color='{col.hexval()}' size='9'><b>{nivel}</b></font>",
                    ParagraphStyle("a4", fontSize=9, alignment=TA_CENTER)
                ),
            ])

        actores_tbl = Table(
            rows,
            colWidths=[3.9 * cm, 4.2 * cm, 6.4 * cm, 2.5 * cm],
        )
        actores_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BG_CARD),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BG_LIGHT, colors.white]),
            ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, NAVY_BRAND),
            ("LINEBELOW", (0, 1), (-1, -2), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        actores_block.append(actores_tbl)
    else:
        # Sin matches concretos — mensaje contextual
        msg_card = Table(
            [[Paragraph(
                "<i>Sin actores nominales destacados en el ciclo de 24 h. "
                "Las amenazas activas no identifican individuos concretos; "
                "el análisis sigue centrado en dinámicas institucionales y "
                "estructurales (ver Top 5 amenazas en página 2).</i>",
                ParagraphStyle("noact", fontSize=9, leading=12,
                                 textColor=TXT_SECONDARY, alignment=TA_LEFT)
            )]],
            colWidths=[17 * cm],
        )
        msg_card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.3, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        actores_block.append(msg_card)

    # KeepTogether garantiza que el bloque label+título+tabla no se parte
    elems.append(KeepTogether(actores_block))

    elems.append(PageBreak())
    return elems


# =====================================================================
# PÁGINA 2 — LECTURA PROSPECTIVA
# =====================================================================
def _pagina_2_lectura(brief, styles):
    elems = []

    # Mini-header de continuidad
    fecha = (brief.get("generado_en", "") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    elems.append(Paragraph(
        f"<font color='#94a3b8' size='8'><b>REPORTE DIARIO RIESGO POLÍTICO · PERÚ</b> · {fecha} · pág. 2</font>",
        styles["meta"]
    ))
    elems.append(HRFlowable(width="100%", color=BORDER, thickness=0.6,
                              spaceBefore=2, spaceAfter=8))

    # ========== STATUS NACIONAL — 4 DIMENSIONES (grid 2x2) ==========
    elems.append(Paragraph("◇ STATUS NACIONAL", styles["section_label"]))
    elems.append(Paragraph("Las 4 dimensiones del riesgo", styles["section_title"]))

    status = brief.get("status_nacional", {}) or {}

    def _dim_card(label, data, accent=ACCENT):
        """Card de dimensión — usa tabla interna para evitar overlap entre el
        número grande y los textos circundantes."""
        sc = _fmt_num(data.get("score"), 1)
        et = data.get("etiqueta", "—")
        col = _color(data.get("color"))
        sub = data.get("sublabel", "")

        # Tabla interna 3 filas verticales con leading propio
        rows = [
            [Paragraph(
                f"<font color='#475569'><b>{label.upper()}</b></font>",
                ParagraphStyle("dl1", fontSize=7, leading=9, alignment=TA_LEFT)
            )],
            # Fila valor: número grande + etiqueta lado a lado en sub-tabla
            [Table(
                [[
                    Paragraph(
                        f"<font color='{col.hexval()}'><b>{sc}</b></font>",
                        ParagraphStyle("dl2a", fontSize=18, leading=20, alignment=TA_LEFT)
                    ),
                    Paragraph(
                        f"<font color='{col.hexval()}'><b>{et}</b></font>",
                        ParagraphStyle("dl2b", fontSize=10, leading=12, alignment=TA_LEFT)
                    ),
                ]],
                colWidths=[1.7 * cm, 4.5 * cm],
                style=TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ])
            )],
            [Paragraph(
                f"<font color='#94a3b8'>{sub}</font>",
                ParagraphStyle("dl3", fontSize=7, leading=9, alignment=TA_LEFT)
            )],
        ]
        return Table(rows, colWidths=[6.3 * cm], style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (0, 0), 0),
            ("BOTTOMPADDING", (0, 0), (0, 0), 0),
            ("TOPPADDING", (0, 1), (0, 1), 1),
            ("BOTTOMPADDING", (0, 1), (0, 1), 1),
        ]))

    op = status.get("operacional_nacional", {}) or {}
    minero = status.get("minero", {}) or {}
    corr = status.get("corredor_sur", {}) or {}
    crim = status.get("criminal", {}) or {}

    grid = Table(
        [
            [_dim_card("Operacional Nacional", op),
             _dim_card("Sector Minero", minero)],
            [_dim_card("Corredor Sur", corr),
             _dim_card("Criminal · Seguridad", crim)],
        ],
        colWidths=[8.5 * cm, 8.5 * cm],
        rowHeights=[2.2 * cm, 2.2 * cm],
    )
    grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(grid)

    # ========== TOP 5 AMENAZAS POLÍTICAS ==========
    elems.append(Spacer(1, 0.3 * cm))
    elems.append(Paragraph("🎯 AMENAZAS POLÍTICAS PRIORITARIAS", styles["section_label"]))
    elems.append(Paragraph("Top 5 · Filtro político-institucional", styles["section_title"]))

    amenazas_all = brief.get("amenazas_prioritarias", []) or []
    amenazas = _filtrar_amenazas_politicas(amenazas_all, n=5)

    nivel_color = {
        "CRÍTICA": CRITICO, "ALTO": ELEVADO,
        "MEDIO": MODERADO, "BAJO": BAJO,
    }

    rows = []
    for i, a in enumerate(amenazas, 1):
        nivel = str(a.get("nivel", ""))
        col = nivel_color.get(nivel, MODERADO)
        score = _fmt_num(a.get("score"), 1)
        nombre = str(a.get("nombre", ""))
        narr = str(a.get("narrativa", ""))
        # Truncar narrativa para mantener altura uniforme
        if len(narr) > 150:
            narr = narr[:147] + "…"

        rows.append([
            Paragraph(
                f"<font color='#94a3b8' size='12'><b>{i}</b></font>",
                ParagraphStyle("rank", alignment=TA_CENTER, fontSize=12)
            ),
            Paragraph(
                f"<font color='#0f172a' size='10'><b>{nombre}</b></font><br/>"
                f"<font color='{col.hexval()}' size='7'><b>{nivel}</b></font>  "
                f"<font color='#94a3b8' size='7'>· {a.get('categoria', '')}</font><br/>"
                f"<font color='#475569' size='8.5'>{narr}</font>",
                ParagraphStyle("a_body", fontSize=8.5, leading=11)
            ),
            # Score: nowrap garantiza que entero + decimal van juntos
            Paragraph(
                f"<font color='{col.hexval()}' size='13'><b>{score}</b></font>",
                ParagraphStyle("a_score", alignment=TA_CENTER,
                                 fontSize=13, leading=15,
                                 wordWrap=None, allowOrphans=1,
                                 allowWidows=1)
            ),
        ])

    if rows:
        # Columna score más ancha (2.2cm) — clave para que el decimal no salte
        amen_tbl = Table(rows, colWidths=[0.9 * cm, 13.9 * cm, 2.2 * cm])
        amen_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [BG_LIGHT, colors.white]),
            ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        elems.append(amen_tbl)
    else:
        elems.append(Paragraph(
            "Sin amenazas políticas destacadas en el ciclo actual.",
            styles["body"]
        ))

    # ========== IMPLICANCIAS OPERACIONALES (compacto, 6 categorías) ==========
    elems.append(Spacer(1, 0.3 * cm))
    elems.append(Paragraph("⚙ IMPLICANCIAS OPERACIONALES", styles["section_label"]))
    elems.append(Paragraph("Impacto en las 6 dimensiones del negocio", styles["section_title"]))

    impl = brief.get("implicancias_operacionales", {}) or {}
    estado_color = {
        "ESTABLE": ESTABLE, "MONITOREO": BAJO,
        "ATENCIÓN": MODERADO, "ALERTA": ELEVADO,
    }
    icons = {
        "logistica": "🛣", "esg": "🌱", "regulatorio": "⚖",
        "reputacional": "📣", "fuerza_laboral": "👥", "continuidad": "⚙",
    }

    # Grid 3x2 compacto
    keys = ["logistica", "esg", "regulatorio",
            "reputacional", "fuerza_laboral", "continuidad"]
    cells = []
    for k in keys:
        data = impl.get(k, {}) or {}
        estado = data.get("estado", "ESTABLE")
        col = estado_color.get(estado, ESTABLE)
        label = data.get("label", k)
        n = data.get("n_amenazas", 0)
        cells.append(Paragraph(
            f"<font size='12'>{icons.get(k, '•')}</font>  "
            f"<font color='#0f172a' size='8.5'><b>{label}</b></font><br/>"
            f"<font color='{col.hexval()}' size='8'><b>{estado}</b></font>  "
            f"<font color='#94a3b8' size='7'>· {n} amenaza{'s' if n != 1 else ''}</font>",
            ParagraphStyle(f"impl_{k}", fontSize=8.5, leading=11, alignment=TA_LEFT)
        ))

    impl_grid = Table(
        [
            [cells[0], cells[1], cells[2]],
            [cells[3], cells[4], cells[5]],
        ],
        colWidths=[5.67 * cm, 5.67 * cm, 5.66 * cm],
        rowHeights=[1.4 * cm, 1.4 * cm],
    )
    impl_grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems.append(impl_grid)

    return elems


# =====================================================================
# FUNCIÓN PRINCIPAL
# =====================================================================
def generar_strategic_daily_brief_pdf(
    output_path: str,
    brief: dict,
    modo: str = "diario",
) -> str:
    """Genera el Reporte de Riesgo Político · Perú (2 páginas A4).

    Args:
        output_path: ruta absoluta del PDF de salida.
        brief: dict del Executive Brief (sintetizar_executive_brief).
        modo: "diario" — Reporte 06:00 AM (default)
              "on_demand_24h" — Reporte 24h on-demand (etiqueta GENERADO + hora)

    Returns:
        output_path para encadenar.
    """
    generado_en = brief.get("generado_en", "") or datetime.now().isoformat()
    fecha = generado_en[:10] or datetime.now().strftime("%Y-%m-%d")

    # Hora real para el footer (solo se usa cuando es on_demand_24h)
    try:
        hora_real = datetime.fromisoformat(generado_en.replace("Z", "")).strftime("%H:%M")
    except Exception:
        hora_real = datetime.now().strftime("%H:%M")

    try:
        from .branding import thalos_pdf_metadata, BRAND_COMPANY, BRAND_TAGLINE
    except ImportError:
        from apurisk.reports.branding import thalos_pdf_metadata, BRAND_COMPANY, BRAND_TAGLINE

    if modo == "on_demand_24h":
        titulo_pdf = f"Reporte 24h de Riesgo Político · Perú · {fecha} {hora_real}"
        subject_pdf = f"Inteligencia Estratégica On-Demand — {fecha} {hora_real}"
        footer_marca = "REPORTE 24H ON-DEMAND"
    else:
        titulo_pdf = f"Reporte Diario de Riesgo Político · Perú · {fecha}"
        subject_pdf = f"Inteligencia Estratégica — {fecha}"
        footer_marca = "REPORTE DIARIO 06:00"

    meta = thalos_pdf_metadata()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.6 * cm, bottomMargin=1.4 * cm,
        title=titulo_pdf,
        author=meta["author"],
        creator=meta["creator"],
        subject=subject_pdf,
    )

    styles = _build_styles()
    story = []
    story.extend(_pagina_1_diagnostico(brief, styles, modo=modo))
    story.extend(_pagina_2_lectura(brief, styles))

    # ===== FOOTER CORPORATIVO FIJO =====
    # Mismo footer en TODAS las páginas — identifica producto, marca y confidencialidad
    def _on_page(canvas, doc):
        canvas.saveState()
        # Línea separadora superior del footer
        canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
        canvas.setLineWidth(0.4)
        canvas.line(2 * cm, 1.25 * cm, A4[0] - 2 * cm, 1.25 * cm)
        # Texto del footer en dos líneas para mejor legibilidad
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(TXT_SECONDARY)
        canvas.drawCentredString(
            A4[0] / 2, 1.0 * cm,
            f"THALOS · Strategic Intelligence for Complex Decisions"
        )
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(TXT_TERTIARY)
        if modo == "on_demand_24h":
            linea_inf = (
                f"{footer_marca}  ·  Página {doc.page} de 2  ·  "
                f"Generado {fecha} {hora_real} hrs  ·  CONFIDENCIAL · USO INTERNO"
            )
        else:
            linea_inf = (
                f"{footer_marca}  ·  Página {doc.page} de 2  ·  "
                f"Generado {fecha}  ·  CONFIDENCIAL · USO INTERNO"
            )
        canvas.drawCentredString(A4[0] / 2, 0.55 * cm, linea_inf)
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return output_path
