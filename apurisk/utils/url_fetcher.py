"""APURISK · utils/url_fetcher — Extrae título y texto de una URL web para ingesta manual."""
from __future__ import annotations
import re


def _strip_tags(html: str) -> str:
    """Elimina tags HTML dejando texto plano."""
    return re.sub(r"<[^>]+>", " ", html)


def _clean(text: str, maxlen: int = 2000) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:maxlen]


def fetch_articulo(url: str, timeout: int = 15) -> dict:
    """Descarga url y extrae {titulo, resumen, fuente} para ingesta manual.

    Devuelve siempre un dict aunque falle (titulo vacío indica error).
    Nunca lanza excepción — el caller decide cómo manejar un resultado vacío.
    """
    try:
        import requests
    except ImportError:
        return {"titulo": "", "resumen": "", "fuente": "", "error": "requests no instalado"}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    }
    try:
        r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        if r.status_code != 200:
            return {"titulo": "", "resumen": "", "fuente": "",
                    "error": f"HTTP {r.status_code}"}
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
    except Exception as e:
        return {"titulo": "", "resumen": "", "fuente": "", "error": str(e)}

    # Extraer dominio como fuente
    from urllib.parse import urlparse
    fuente = urlparse(url).netloc.replace("www.", "")

    # Título: <title> o <h1>
    titulo = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        titulo = _clean(_strip_tags(m.group(1)), 300)
    if not titulo:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        if m:
            titulo = _clean(_strip_tags(m.group(1)), 300)

    # Resumen: <meta name="description"> o primeros párrafos
    resumen = ""
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
                  html, re.IGNORECASE)
    if not m:
        m = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
                      html, re.IGNORECASE)
    if m:
        resumen = _clean(_strip_tags(m.group(1)), 500)

    if not resumen:
        # Primeros 3 párrafos con algo de texto
        parrafos = re.findall(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)
        textos = [_clean(_strip_tags(p)) for p in parrafos if len(_strip_tags(p).strip()) > 80]
        resumen = " ".join(textos[:3])[:500]

    return {"titulo": titulo, "resumen": resumen, "fuente": fuente, "error": ""}
