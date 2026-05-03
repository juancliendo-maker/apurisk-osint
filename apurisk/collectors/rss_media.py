"""Recolector de RSS de medios peruanos."""
from __future__ import annotations
from datetime import datetime
import xml.etree.ElementTree as ET
from .base import BaseCollector, Article


class RSSMediaCollector(BaseCollector):
    category = "medios"

    def __init__(self, feed_cfg: dict, config: dict, demo: bool = True):
        super().__init__(config, demo=demo)
        self.source_id = feed_cfg["id"]
        self.source_name = feed_cfg["nombre"]
        self.url = feed_cfg["url"]

    def collect(self) -> list[Article]:
        if self.demo:
            return self._demo_articles()

        text = self._safe_get(self.url)
        if not text:
            print(f"  [info] {self.source_id}: sin datos en vivo → fallback demo")
            return self._demo_articles()

        # intentamos primero feedparser si está disponible
        try:
            import feedparser
            parsed = feedparser.parse(text)
            articles = []
            for e in parsed.entries[:30]:
                # normalizar published a ISO 8601
                pub_iso = self._normalize_pub(e)
                articles.append(
                    Article(
                        source_id=self.source_id,
                        source_name=self.source_name,
                        category=self.category,
                        title=e.get("title", "").strip(),
                        summary=self._clean_html(e.get("summary", ""))[:600],
                        url=e.get("link", ""),
                        published=pub_iso,
                        criticidad=self._auto_criticidad(e.get("title", "") + " " + e.get("summary", "")),
                    )
                )
            if articles:
                return articles
        except ImportError:
            pass
        # fallback con XML estándar
        return self._parse_xml(text)

    @staticmethod
    def _normalize_pub(entry) -> str:
        """Convierte el published de feedparser a ISO 8601."""
        from datetime import datetime
        # feedparser expone published_parsed (struct_time) si pudo parsear
        st = entry.get("published_parsed") or entry.get("updated_parsed")
        if st:
            try:
                return datetime(*st[:6]).isoformat(timespec="seconds")
            except Exception:
                pass
        return entry.get("published") or entry.get("updated") or ""

    @staticmethod
    def _clean_html(text: str) -> str:
        """Quita tags HTML simples del summary."""
        if not text:
            return ""
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()
        except Exception:
            import re
            return re.sub(r"<[^>]+>", "", text).strip()

    @staticmethod
    def _auto_criticidad(text: str) -> str:
        """Heurística simple para detectar criticidad alta en el titular."""
        t = (text or "").lower()
        crit_kws = ["urgente", "última hora", "ultima hora", "vacancia", "renuncia", "bloqueo",
                    "atentado", "muerto", "fallecido", "crisis", "fiscalía formaliza",
                    "fiscalia formaliza", "denuncia constitucional"]
        if any(k in t for k in crit_kws):
            return "alta"
        return "media"

    def _parse_xml(self, text: str) -> list[Article]:
        articles: list[Article] = []
        try:
            root = ET.fromstring(text)
            # RSS 2.0 (item) y Atom (entry)
            items = list(root.iter("item")) or list(root.iter("{http://www.w3.org/2005/Atom}entry"))
            for item in items:
                title = (item.findtext("title")
                         or item.findtext("{http://www.w3.org/2005/Atom}title")
                         or "").strip()
                link = (item.findtext("link")
                        or "").strip()
                if not link:
                    # Atom: <link href="..." />
                    ln = item.find("{http://www.w3.org/2005/Atom}link")
                    if ln is not None:
                        link = (ln.get("href") or "").strip()
                desc = (item.findtext("description")
                        or item.findtext("{http://www.w3.org/2005/Atom}summary")
                        or "").strip()
                pub_raw = (item.findtext("pubDate")
                           or item.findtext("{http://www.w3.org/2005/Atom}published")
                           or item.findtext("{http://www.w3.org/2005/Atom}updated")
                           or "").strip()
                # normalizar a ISO si es RFC 822
                pub_iso = pub_raw
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_raw)
                    pub_iso = dt.isoformat(timespec="seconds")
                except Exception:
                    pass
                if title:
                    articles.append(
                        Article(
                            source_id=self.source_id,
                            source_name=self.source_name,
                            category=self.category,
                            title=title,
                            summary=self._clean_html(desc)[:600],
                            url=link,
                            published=pub_iso,
                            criticidad=self._auto_criticidad(title + " " + desc),
                        )
                    )
        except ET.ParseError:
            pass
        return articles[:30]

    def _demo_articles(self) -> list[Article]:
        """Datos sintéticos realistas peruanos para modo demo."""
        from ..data.sample_data import MEDIOS_DEMO
        items = MEDIOS_DEMO.get(self.source_id, [])
        out = []
        for it in items:
            out.append(
                Article(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    category=self.category,
                    title=it["title"],
                    summary=it.get("summary", ""),
                    url=it.get("url", ""),
                    published=it.get("published", datetime.now().isoformat()),
                    criticidad=it.get("criticidad", "media"),
                    raw=it,
                )
            )
        return out
