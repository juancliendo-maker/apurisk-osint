"""THALOS · Reporte por Caso — derivación DETERMINISTA de términos de búsqueda.

Un caso no puede nacer ciego. Antes, los términos los producía únicamente el
modelo (derivación ciega); si la API no respondía —sin saldo, caída, timeout—
la lista quedaba vacía, la búsqueda en BD devolvía cero y el expediente se
reducía a lo que el analista cargara a mano.

Este módulo deriva términos SIN llamar a la API, a partir de lo que el analista
ya escribió (título, pregunta, escenarios) y de los CATÁLOGOS que la plataforma
ya mantiene:

  · config_actores       → nombre + alias de cada actor activo del país
  · config_actor_temas   → temas de interés declarados por actor
  · config_keywords      → palabras clave por factor de riesgo

El modelo, cuando responde, AMPLÍA esta lista; nunca es su única fuente. El
resultado declara siempre su procedencia para que la mesa muestre de dónde salió
cada término (honestidad de datos).

Todo lo calibrable —longitud mínima, tope de términos, vocabulario vacío— vive en
config_parametros; aquí no se hardcodea nada salvo los defaults de arranque.
"""
from __future__ import annotations
import re
import unicodedata

# Palabras vacías del español + ruido propio del dominio (interrogativos y verbos
# de pregunta que aparecen en toda hipótesis y no discriminan nada).
STOPWORDS_BASE = {
    "a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos", "ante",
    "antes", "aquel", "aquella", "aquello", "aqui", "asi", "aun", "aunque", "bajo",
    "bien", "cada", "casi", "como", "con", "contra", "cual", "cuales", "cuando",
    "cuanto", "de", "del", "desde", "donde", "dos", "e", "el", "ella", "ellas",
    "ello", "ellos", "en", "entre", "era", "eran", "es", "esa", "esas", "ese",
    "eso", "esos", "esta", "estan", "estas", "este", "esto", "estos", "ha", "han",
    "hasta", "hay", "la", "las", "le", "les", "lo", "los", "mas", "me", "mi",
    "mientras", "muy", "ni", "no", "nos", "o", "otra", "otras", "otro", "otros",
    "para", "pero", "poco", "por", "porque", "que", "quien", "quienes", "se",
    "segun", "ser", "si", "sin", "sobre", "solo", "son", "su", "sus", "tal",
    "tambien", "tanto", "te", "tiene", "todo", "todos", "tras", "un", "una",
    "uno", "unos", "y", "ya",
    # ruido de formulación de hipótesis
    "sera", "seria", "podria", "puede", "pueden", "habra", "escenario",
    "escenarios", "caso", "pregunta", "hipotesis", "reporte", "analisis",
}


def quitar_tildes(s: str) -> str:
    """'Ántonio' → 'Antonio'. Base de toda comparación insensible a acentos."""
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")


def normalizar(s: str) -> str:
    """Minúsculas, sin tildes, sin puntuación, espacios colapsados."""
    t = quitar_tildes(s or "").lower()
    t = re.sub(r"[^\w\s-]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _palabras(texto: str, stop: set, min_len: int) -> list:
    """Palabras significativas, en orden de aparición y sin repetir."""
    vistas, out = set(), []
    for w in normalizar(texto).split():
        if len(w) < min_len or w in stop or w.isdigit():
            continue
        if w not in vistas:
            vistas.add(w)
            out.append(w)
    return out


def _ngramas(texto: str, stop: set, min_len: int, n: int = 2) -> list:
    """Bigramas de palabras significativas ADYACENTES en el texto original.

    'corredor minero sur' → ['corredor minero', 'minero sur']. Un bigrama
    discrimina mucho mejor que sus partes: 'corredor' solo trae ruido.
    """
    crudo = [w for w in normalizar(texto).split()
             if len(w) >= min_len and w not in stop and not w.isdigit()]
    return [" ".join(crudo[i:i + n]) for i in range(len(crudo) - n + 1)]


# ── Catálogos de la plataforma ───────────────────────────────────────────────
def terminos_de_catalogos(db_path: str, texto_caso: str, pais: str = "PE",
                          par: dict = None) -> dict:
    """Términos aportados por los catálogos, cruzados contra el texto del caso.

    Un actor entra si su nombre —o cualquiera de sus alias— aparece en el texto
    del caso; entonces se aportan TODAS sus variantes, que es justamente lo que
    el LIKE literal se perdía. Las keywords entran por coincidencia de tema.

    Devuelve {actores: [...], alias: [...], keywords: [...], temas: [...]}
    para que la mesa pueda declarar el origen de cada término.
    """
    par = par or {}
    out = {"actores": [], "alias": [], "keywords": [], "temas": []}
    txt = normalizar(texto_caso)
    if not txt:
        return out
    try:
        from ..storage.config_loader import _conn
    except Exception:
        return out

    def _norm_lista(crudo: str) -> list:
        """El campo alias admite varias formas de separador según quién lo cargó."""
        return [a.strip() for a in re.split(r"[|;,/\n]", crudo or "") if a.strip()]

    try:
        with _conn(db_path) as c:
            try:
                filas = c.execute(
                    "SELECT id, nombre, alias FROM config_actores "
                    "WHERE activo=1 AND (pais=? OR pais IS NULL OR pais='')",
                    (pais,)).fetchall()
            except Exception:
                filas = []

            # Temas declarados por actor: es el vínculo que la plataforma ya
            # modela para relacionar un actor con una materia. Un caso rara vez
            # nombra a sus actores con todas las letras ("corredor minero sur" no
            # dice "Federación Campesina del Sur"), así que el actor entra por
            # DOS vías: se le menciona, o su tema declarado es el del caso.
            temas_por_actor = {}
            try:
                for f in c.execute(
                    "SELECT actor_id, tema FROM config_actor_temas "
                    "WHERE (pais=? OR pais IS NULL OR pais='')", (pais,)).fetchall():
                    tema = (f["tema"] or "").strip()
                    if tema:
                        temas_por_actor.setdefault(f["actor_id"], []).append(tema)
            except Exception:
                pass

            for f in filas:
                nombre = (f["nombre"] or "").strip()
                alias = _norm_lista(f["alias"])
                variantes = ([nombre] if nombre else []) + alias
                mencionado = any(normalizar(v) and normalizar(v) in txt
                                 for v in variantes)
                temas_actor = temas_por_actor.get(f["id"], [])
                por_tema = [t for t in temas_actor
                            if normalizar(t) and normalizar(t) in txt]
                if not (mencionado or por_tema):
                    continue
                if nombre:
                    out["actores"].append(nombre)
                for a in alias:
                    if normalizar(a) != normalizar(nombre):
                        out["alias"].append(a)
                out["temas"] += por_tema

            # ── keywords de factores: entran las que ya aparecen en el caso ──
            try:
                for f in c.execute(
                    "SELECT keyword FROM config_keywords "
                    "WHERE activo=1 AND (pais=? OR pais IS NULL OR pais='')",
                    (pais,)).fetchall():
                    kw = (f["keyword"] or "").strip()
                    if kw and normalizar(kw) and normalizar(kw) in txt:
                        out["keywords"].append(kw)
            except Exception:
                pass
    except Exception as e:
        print(f"[caso_terminos] catálogos no disponibles: {e}")
    for k in out:
        out[k] = _sin_duplicados(out[k])
    return out


def _sin_duplicados(seq: list) -> list:
    """Únicos por forma normalizada, conservando la grafía original y el orden."""
    vistos, out = set(), []
    for s in seq:
        k = normalizar(s)
        if k and k not in vistos:
            vistos.add(k)
            out.append(s.strip())
    return out


# ── Derivación completa ──────────────────────────────────────────────────────
def derivar_terminos(db_path: str, titulo: str = "", pregunta: str = "",
                     escenarios: list = None, par: dict = None,
                     pais: str = "PE") -> dict:
    """Términos de búsqueda derivados SIN API, con su procedencia declarada.

    Orden de aporte (el tope recorta por el final, así que lo más discriminante
    va primero): bigramas del caso → actores y alias → keywords y temas →
    palabras sueltas.

    Devuelve {terminos, origen: {termino: fuente}, detalle: {...}, degradado}.
    """
    par = par or {}
    min_len = int(par.get("terminos_min_long", 4))
    tope = int(par.get("terminos_tope", 24))
    extra = {normalizar(w) for w in (par.get("terminos_stopwords_extra") or [])}
    stop = STOPWORDS_BASE | {w for w in extra if w}

    escenarios = [e for e in (escenarios or []) if (e or "").strip()]
    texto_caso = " . ".join(x for x in ([titulo, pregunta] + escenarios) if x)

    cat = terminos_de_catalogos(db_path, texto_caso, pais=pais, par=par)

    # Bigramas: del título y la pregunta (el eje), luego de cada escenario.
    bigramas = _ngramas(f"{titulo} {pregunta}", stop, min_len)
    for e in escenarios:
        bigramas += _ngramas(e, stop, min_len)

    sueltas = _palabras(texto_caso, stop, min_len)

    origen, terminos = {}, []

    def _add(lista, fuente):
        for t in lista:
            k = normalizar(t)
            if not k or k in origen or len(terminos) >= tope:
                continue
            origen[k] = fuente
            terminos.append(t.strip())

    _add(_sin_duplicados(bigramas), "frase del caso")
    _add(cat["actores"], "catálogo de actores")
    _add(cat["alias"], "alias de actor")
    _add(cat["keywords"], "catálogo de keywords")
    _add(cat["temas"], "tema de actor")
    _add(sueltas, "palabra del caso")

    return {
        "terminos": terminos,
        "origen": origen,
        "detalle": {
            "frases": len([1 for f in origen.values() if f == "frase del caso"]),
            "actores": len(cat["actores"]),
            "alias": len(cat["alias"]),
            "keywords": len(cat["keywords"]),
            "temas": len(cat["temas"]),
            "palabras": len([1 for f in origen.values() if f == "palabra del caso"]),
        },
        # 'degradado' lo marca quien llama, si la IA no pudo ampliar la lista.
        "degradado": False,
    }


def fusionar_terminos(base: list, ampliacion: list, par: dict = None) -> list:
    """Une los términos deterministas con los que aportó el modelo.

    La base manda: si el tope obliga a recortar, se cae la ampliación primero.
    El modelo AMPLÍA, nunca reemplaza.
    """
    par = par or {}
    tope = int((par or {}).get("terminos_tope", 24))
    out = _sin_duplicados(list(base or []) + list(ampliacion or []))
    return out[:tope]


# ── Coincidencia tolerante (punto 3: encontrar, no solo coincidir literal) ────
def variantes_de_termino(t: str, par: dict = None) -> list:
    """Formas alternativas de un término para el LIKE.

    Cubre lo que el literal se perdía: tildes y plurales del castellano. No es
    un stemmer —eso traería falsos positivos— sino las variantes seguras:

      'protesta'  → protesta, protestas
      'paro'      → paro, paros
      'concesión' → concesion, concesiones, concesión
    """
    par = par or {}
    if not (par.get("busqueda_variantes", 1)):
        return [t]
    base = (t or "").strip()
    if not base:
        return []
    formas = {base, normalizar(base)}
    for f in list(formas):
        if len(f) < 4 or " " in f:
            continue
        if f.endswith("es"):
            formas.add(f[:-2])
        elif f.endswith("s"):
            formas.add(f[:-1])
        elif f.endswith(("z",)):
            formas.add(f[:-1] + "ces")
        elif f[-1] in "aeiou":
            formas.add(f + "s")
        else:
            formas.add(f + "es")
    return sorted({f for f in formas if f})
