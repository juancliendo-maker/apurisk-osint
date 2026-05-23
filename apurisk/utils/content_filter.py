"""Filtros de contenido para excluir falsos positivos en los clasificadores.

Estos filtros se aplican ANTES de que un artículo pase por los clasificadores
de Conflictos, Legislativo, Crimen Organizado o el motor de riesgo minero.
Si un artículo es contenido deportivo / entretenimiento / espectáculos / clima,
se descarta automáticamente para evitar falsos positivos que degraden la
credibilidad de la plataforma.

Caso reportado: notas de fútbol como "Melgar se enfrentará ante Sport Huancayo"
estaban siendo clasificadas como CONFLICTO porque contienen palabras como
"enfrentamiento" o nombres de ciudades.
"""
from __future__ import annotations


# =====================================================================
# DEPORTES
# =====================================================================

# Patrones de URL que indican contenido deportivo
URL_PATRONES_DEPORTE = [
    "/futbol/", "/futbol-", "/futbol_", "futbol-descentralizado",
    "/deportes/", "/deporte/", "/deporte-", "/sports/", "/sport-",
    "/liga/", "/liga-1/", "/liga1/", "/liga-2/", "/liga-mx",
    "/champions/", "champions-league",
    "/copa-", "/copa_", "copa-libertadores", "copa-sudamericana",
    "copa-america", "copa-del-rey",
    "/mundial/", "mundial-",
    "/seleccion/", "/seleccion-peruana", "seleccion-de-",
    "/bundesliga/", "/laliga/", "la-liga", "/serie-a/", "/serie-b/",
    "/premier-league/", "premier-league", "/ligue-1/",
    "/eurocopa/", "/eurocopa-",
    "/basquet/", "/baloncesto/", "/nba/", "/nba-",
    "/voley/", "/voleibol/", "/voley-femenino",
    "/tenis/", "/tennis/", "/atp/", "/wta/",
    "/golf/", "/atletismo/", "/boxeo/", "/box/",
    "/automovilismo/", "/formula-1/", "/f1/", "/motogp/",
    "/ufc/", "/mma/",
    "/espectaculos/", "/farandula/", "/celebridades/",
    "/cine/", "/series/", "/television-",
]

# Source IDs que son fuentes deportivas
SOURCES_DEPORTE = ["depor", "futbolperuano", "lider", "todosport"]

# Keywords textuales que casi garantizan que el contenido es deportivo
KEYWORDS_DEPORTE_FUERTES = [
    # Competencias
    "liga 1", "liga uno", "liga 2", "descentralizado", "torneo apertura",
    "torneo clausura", "torneo descentralizado",
    "champions league", "uefa champions", "europa league",
    "copa libertadores", "copa sudamericana", "copa américa", "copa america",
    "copa del rey", "copa do brasil",
    "serie a italia", "serie a brasil", "serie b ", "premier league",
    "bundesliga", "la liga", "ligue 1", "ligue 2",
    "eurocopa", "mundial sub", "mundial fifa", "concacaf",
    "primera división", "segunda división",
    # "Fecha N" típico de jornada futbolística
    "fecha 1 del", "fecha 2 del", "fecha 3 del", "fecha 4 del", "fecha 5 del",
    "fecha 6 del", "fecha 7 del", "fecha 8 del", "fecha 9 del", "fecha 10 del",
    "fecha 11 del", "fecha 12 del", "fecha 13 del", "fecha 14 del",
    "fecha 15 del", "fecha 16 del", "fecha 17 del", "fecha 18 del",
    "fecha 19 del", "fecha 20 del", "por la fecha",
    # Clubes peruanos
    "alianza lima", "club universitario", "sporting cristal",
    "melgar fbc", "fbc melgar", "cienciano",
    "césar vallejo", "cesar vallejo",
    "sport huancayo", "sport boys", "sport victoria",
    "alianza atlético", "alianza atletico",
    "deportivo binacional", "carlos mannucci", "carlos a. mannucci",
    "cusco fc", "ayacucho fc", "utc cajamarca", "juan pablo ii",
    "los chankas", "comerciantes unidos", "atlético grau",
    "deportivo municipal", "uc.b", "academia cantolao",
    # Clubes internacionales muy citados
    "real madrid", "fc barcelona", "atlético madrid", "atletico madrid",
    "manchester united", "manchester city", "liverpool fc",
    "chelsea fc", "arsenal fc", "tottenham",
    "bayern münich", "bayern munich", "borussia",
    "juventus fc", "inter de milán", "inter de milan",
    "ac milan", "ac. milan", "as roma", "ss lazio",
    "psg ", "paris saint", "olympique",
    "flamengo", "palmeiras", "fluminense", "corinthians",
    "boca juniors", "river plate", "racing club", "san lorenzo",
    "colo colo", "universidad de chile", "u de chile",
    "u catolica", "universidad católica",
    "nacional uruguay", "peñarol",
    "millonarios", "atlético nacional", "atletico nacional",
    "barcelona sc", "ldu quito", "emelec",
    # Posiciones y términos del fútbol
    "delantero", "mediocampista", "volante", "lateral derecho",
    "lateral izquierdo", "arquero", "portero", "guardameta",
    "central de área", "marcador central",
    "director técnico", "dt nacional", "entrenador nacional",
    # Acciones del juego
    "gol de", "doblete", "hat trick", "hat-trick",
    "tiro libre", "tiro penal", "tiro de esquina",
    "tarjeta amarilla", "tarjeta roja", "expulsado",
    "fuera de juego", "offside", "var ",
    # Estadios
    "estadio monumental", "estadio nacional de lima",
    "estadio alejandro villanueva", "estadio matute",
    "estadio mansiche", "estadio garcilaso", "estadio inca",
    "estadio uno", "estadio iván elías moreno",
    # Otros deportes
    "vóley femenino", "voley femenino", "voleibol nacional",
    "selección de vóley", "seleccion de voley",
    "nba ", "lakers", "warriors", "celtics",
    "atp ", "wta ", "grand slam", "roland garros",
    "wimbledon", "us open",
    "fórmula 1", "formula 1", "gran premio",
    "boxeo nacional", "campeón mundial de boxeo",
    "ufc ", "peleador peruano",
    # Casos específicos del reporte del usuario
    "se enfrentará ante", "se enfrentaran ante",
    "se encuentran en la fecha", "se encontrarán en la fecha",
    "fecha 15 del descentralizado", "fecha 37",
]


# =====================================================================
# ESPECTÁCULOS / FARÁNDULA (también ruidoso para riesgo político)
# =====================================================================
KEYWORDS_ESPECTACULOS = [
    "magaly medina", "magaly tv", "tula rodriguez", "tula rodríguez",
    "rodrigo gonzalez", "rodrigo gonzález",
    "amor amor amor", "amor y fuego", "esto es guerra", "combate",
    "el gran chef famosos", "yo soy", "la voz",
    "reality de", "reality show",
    "chollywood", "miss perú", "miss peru",
    "concierto de", "tour mundial", "fechas del tour",
    "el gran show", "los reyes del show",
    "cómico ambulante", "comico ambulante",
    "vedette peruana",
]


def _texto(art) -> str:
    """Extrae el texto de un artículo dict o Article."""
    if isinstance(art, dict):
        return f"{art.get('title','')} {art.get('summary','')}"
    return f"{getattr(art, 'title', '') or ''} {getattr(art, 'summary', '') or ''}"


def _url(art) -> str:
    if isinstance(art, dict):
        return (art.get("url") or "").lower()
    return (getattr(art, "url", "") or "").lower()


def _source_id(art) -> str:
    if isinstance(art, dict):
        return (art.get("source_id") or "").lower()
    return (getattr(art, "source_id", "") or "").lower()


def es_contenido_deportivo(art) -> bool:
    """Detecta si un artículo es contenido deportivo.

    Multi-capa defensiva:
      1. URL contiene patrones de sección deportiva → True
      2. source_id es una fuente deportiva → True
      3. Texto contiene keywords deportivos fuertes → True

    Si retorna True, el artículo NO debe pasar por los clasificadores
    de Conflictos, Legislativo, Crimen Organizado o Riesgo Minero.
    """
    # Capa 1: URL
    url = _url(art)
    if url:
        for patron in URL_PATRONES_DEPORTE:
            if patron in url:
                return True

    # Capa 2: source_id
    sid = _source_id(art)
    if sid:
        for src in SOURCES_DEPORTE:
            if src in sid:
                return True

    # Capa 3: keywords textuales
    texto = _texto(art).lower()
    if not texto.strip():
        return False
    for kw in KEYWORDS_DEPORTE_FUERTES:
        if kw in texto:
            return True

    return False


def es_contenido_espectaculos(art) -> bool:
    """Detecta si un artículo es contenido de espectáculos / farándula."""
    url = _url(art)
    if any(p in url for p in ["/espectaculos/", "/farandula/", "/celebridades/",
                                "/cine/", "/series/"]):
        return True
    texto = _texto(art).lower()
    if any(kw in texto for kw in KEYWORDS_ESPECTACULOS):
        return True
    return False


def es_contenido_irrelevante(art) -> bool:
    """Combina filtros de contenido no relevante para riesgo político Perú.

    Si retorna True, descartar:
      - Contenido deportivo
      - Espectáculos / farándula
      - Eventos de otros países LATAM que NO mencionan Perú
    """
    return (es_contenido_deportivo(art) or
            es_contenido_espectaculos(art) or
            es_contenido_otro_pais_latam(art))


# =====================================================================
# FILTRO DE PAÍS: rechazar eventos de otros países LATAM
# =====================================================================
#
# Problema: las fuentes internacionales vía Google News a veces traen
# noticias de Bolivia, Argentina, Chile, etc. que mencionan Perú
# tangencialmente. Estas contaminan los mapas y el análisis.
#
# Lógica: si el título sugiere foco geográfico en otro país LATAM Y el
# texto NO menciona "Perú" o ciudades peruanas específicas, descartar.

OTROS_PAISES_LATAM = {
    "bolivia": ["bolivia", "boliviano", "boliviana", "la paz bolivia", "santa cruz bolivia",
                "cochabamba", "sucre bolivia", "el alto bolivia", "morales bolivia",
                "evo morales", "luis arce", "rodríguez veltzé"],
    "argentina": ["argentina", "argentino", "argentina ", "buenos aires", "córdoba argentina",
                  "rosario argentina", "milei", "cristina kirchner", "macri argentina",
                  "casa rosada"],
    "chile": ["chile ", "chileno", "chilena", "santiago de chile", "valparaíso",
              "valparaiso chile", "boric", "kast chile",
              "antofagasta chile", "iquique chile",
              "frontera con perú chile"],  # solo si es foco chileno
    "colombia": ["colombia ", "colombiano", "colombiana", "bogotá", "bogota colombia",
                 "medellín colombia", "petro colombia", "uribe colombia",
                 "farc", "eln colombia"],
    "ecuador": ["ecuador ", "ecuatoriano", "ecuatoriana", "quito ecuador", "guayaquil",
                "noboa ecuador", "lasso ecuador", "correa ecuador"],
    "venezuela": ["venezuela", "venezolano", "venezolana", "caracas",
                  "maduro", "chávez", "chavez venezuela", "guaidó"],
    "brasil": ["brasil ", "brasileño", "brasileña", "lula", "bolsonaro",
                "rio de janeiro", "sao paulo", "brasília"],
    "mexico": ["méxico", "mexico ", "mexicano", "mexicana", "amlo",
                "ciudad de méxico", "ciudad de mexico", "sheinbaum"],
    "uruguay": ["uruguay", "uruguayo", "uruguaya", "montevideo", "lacalle pou"],
    "paraguay": ["paraguay", "paraguayo", "paraguaya", "asunción paraguay", "peña paraguay"],
}

# Marcadores fuertes de "esto SÍ es de Perú" — si aparecen claramente,
# NO descartar aunque se mencionen otros países.
# IMPORTANTE: NO incluir "peruano" suelto (matchea con "El Peruano" diario
# que escribe sobre Bolivia/etc.). Solo geografía y figuras inequívocas.
MARCADORES_PERU_FUERTES = [
    # País
    "perú", "peru ",
    # Departamentos / ciudades peruanas
    "lima ", "callao", "arequipa", "cusco", "trujillo",
    "chiclayo", "iquitos", "huancayo", "tacna ", "puno peru",
    "ica peru", " ica.", "piura", "cajamarca", "ayacucho", "huaraz",
    "apurímac", "apurimac", "huancavelica", "junín peru",
    "huánuco", "huanuco", "moquegua", "tumbes",
    "loreto peru", "madre de dios", "ucayali", "lambayeque",
    "san martín peru", "san martin peru",
    # Sitios mineros peruanos
    "vraem", "las bambas", "antamina", "yanacocha", "cerro verde",
    "tía maría", "tia maria", "toquepala", "cuajone", "quellaveco",
    "conga peru", "constancia peru", "cerro de pasco",
    # Instituciones peruanas inequívocas
    "minem", "minam ", "mininter peru", "mef peru", "bcrp",
    "snmpe", "sociedad nacional minería", "sociedad nacional mineria",
    "onpe", "jne peru", "defensoría peru", "defensoria peru",
    "congreso de la república", "congreso peruano",
    "tribunal constitucional peru",
    "ana peru", "oefa peru",
    # Frases que vinculan inequívocamente a Perú
    "presidente del perú", "presidente del peru",
    "presidente peruano", "presidenta peruana",
    "premier peruano", "primer ministro peruano",
    "gobierno peruano", "gobierno del perú", "gobierno del peru",
    # Figuras políticas peruanas inequívocas
    "boluarte", "balcázar", "balcazar",
    "fujimori", "vizcarra",
    "rafael lópez aliaga", "rafael lopez aliaga", "lópez aliaga",
    "pedro castillo peru", "castillo terrones",
    "tren de aragua peru",  # criminal venezolano operando en Perú
    "sol peruano", "tipo de cambio sol",
]


def es_contenido_otro_pais_latam(art) -> bool:
    """Detecta si el artículo es de otro país LATAM (no Perú).

    Lógica ESTRICTA (mayo 2026):
      1. Si el TÍTULO menciona explícitamente otro país LATAM Y el título NO
         menciona Perú explícitamente → DESCARTAR (sin importar el cuerpo).
      2. Si el cuerpo menciona OTRO país 3+ veces y NO tiene marcadores
         peruanos fuertes → descartar.
      3. La URL/source con "peruano" o "el peruano" NO es exención —
         medios peruanos también cubren noticias de otros países.
      4. La exención solo aplica si el contenido sustantivo (título o
         cuerpo) menciona claramente Perú/instituciones peruanas.
    """
    texto_full = _texto(art).lower()
    titulo = (art.get("title", "") if isinstance(art, dict)
              else getattr(art, "title", "")).lower()
    if not texto_full.strip():
        return False

    # Detectar si título o cuerpo mencionan Perú explícitamente.
    # IMPORTANTE: NO usar "peruano" como marcador único porque medios
    # peruanos como "El Peruano" escriben sobre Bolivia/Venezuela/etc.
    # Solo geografía y figuras inequívocamente peruanas.
    PERU_EN_TITULO = [
        # Nombre del país (no "peruano" solo)
        "perú", "peru ", " peru.", " peru,",
        # Departamentos / ciudades clave
        "lima ", "callao", "arequipa", "cusco", "trujillo", "chiclayo",
        "iquitos", "huancayo", "tacna ", "puno ", " ica ", "piura",
        "cajamarca", "ayacucho", "apurímac", "apurimac", "huancavelica",
        "huánuco", "huanuco", "tumbes", "moquegua", "junín peru",
        "loreto", "madre de dios", "ucayali", "lambayeque",
        # Sitios y zonas
        "vraem", "las bambas", "antamina", "yanacocha", "tía maría", "tia maria",
        "cerro verde", "toquepala", "cuajone", "quellaveco",
        # Figuras políticas peruanas inequívocas
        "boluarte", "balcázar", "balcazar",
        "fujimori", "vizcarra",
        "rafael lópez aliaga", "rafael lopez aliaga", "lópez aliaga",
        "pedro castillo peru", "castillo terrones",
        "roberto sánchez peru", "roberto sanchez peru",
        # Instituciones inequívocamente peruanas
        "minem", "minam", "mininter peru", "mininter ",
        "bcrp", "bcr peru", "sunat ", "snmpe",
        "onpe", "jne peru", "tribunal constitucional peru",
        "congreso de la república", "congreso peru",
        "defensoría peru", "defensoria peru",
        "cgtp", "conacami",
    ]
    peru_en_titulo = any(m in titulo for m in PERU_EN_TITULO)
    peru_en_cuerpo = any(m in texto_full for m in MARCADORES_PERU_FUERTES)

    # REGLA 1 (estricta): si el TÍTULO menciona otro país LATAM y NO
    # menciona Perú en título → descartar inmediatamente.
    # Ejemplo: "Protestas masivas en Bolivia - CNN" → descartar.
    # Ejemplo: "Gobierno de Bolivia anuncia... - El Peruano" → descartar.
    for pais, keywords in OTROS_PAISES_LATAM.items():
        en_titulo = sum(1 for kw in keywords if kw in titulo)
        if en_titulo >= 1 and not peru_en_titulo:
            return True

    # REGLA 2: si menciona Perú en cualquier lado, mantener
    # (ej: "Perú y Bolivia firman acuerdo")
    if peru_en_titulo or peru_en_cuerpo:
        return False

    # REGLA 3: cuerpo menciona país 3+ veces sin Perú → descartar
    for pais, keywords in OTROS_PAISES_LATAM.items():
        en_texto = sum(1 for kw in keywords if kw in texto_full)
        if en_texto >= 3:
            return True

    return False
