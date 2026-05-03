"""Clases base para recolectores OSINT."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Article:
    """Artículo / evento normalizado para análisis."""
    source_id: str
    source_name: str
    category: str           # medios | estado | internacional | redes
    title: str
    summary: str = ""
    url: str = ""
    published: Optional[str] = None  # ISO 8601
    region: Optional[str] = None     # departamento/región del Perú si aplica
    criticidad: str = "media"        # baja | media | alta
    raw: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    def hours_ago(self) -> float:
        """Horas transcurridas desde publicación, calculado en hora de Lima (PET)."""
        try:
            from ..utils.timezone_pe import hours_ago_pe
            return hours_ago_pe(self.published)
        except Exception:
            return float("inf")


class BaseCollector:
    """Interfaz mínima de un recolector."""
    source_id: str = "base"
    source_name: str = "Base"
    category: str = "medios"

    def __init__(self, config: dict, demo: bool = True):
        self.config = config
        self.demo = demo

    def collect(self) -> list[Article]:
        raise NotImplementedError

    # utilidades comunes con retry/backoff
    def _safe_get(self, url: str, timeout: int = 15, retries: int = 2) -> str | None:
        try:
            import requests
        except ImportError:
            print(f"  [warn] {self.source_id}: requests no instalado (pip install requests)")
            return None

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; APURISK/1.0; +osint-research)",
            "Accept": "application/rss+xml, application/xml, text/xml, application/json, text/html;q=0.9, */*;q=0.7",
            "Accept-Language": "es-PE,es;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        }
        backoff = 1.5
        last_err = None
        for attempt in range(retries + 1):
            try:
                r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
                if r.status_code == 200:
                    # decodificar correctamente respetando charset
                    if not r.encoding or r.encoding.lower() == "iso-8859-1":
                        r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
                if r.status_code in (301, 302, 303):
                    continue
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    if attempt < retries:
                        import time
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                last_err = f"HTTP {r.status_code}"
                break
            except Exception as e:
                last_err = str(e)
                if attempt < retries:
                    import time
                    time.sleep(backoff)
                    backoff *= 2
                    continue
        print(f"  [warn] {self.source_id}: {last_err}")
        return None
