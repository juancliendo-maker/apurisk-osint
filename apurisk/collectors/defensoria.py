"""Recolector de Conflictos Sociales - Defensoría del Pueblo."""
from __future__ import annotations
from datetime import datetime
from .base import BaseCollector, Article


class DefensoriaCollector(BaseCollector):
    source_id = "defensoria_conflictos"
    source_name = "Defensoría del Pueblo - Conflictos Sociales"
    category = "estado"

    def collect(self) -> list[Article]:
        if self.demo:
            return self._demo_articles()

        url = self.config["estado"]["defensoria_conflictos"]["url"]
        text = self._safe_get(url)
        if not text:
            return self._demo_articles()
        # En producción: parser BeautifulSoup específico al PDF/HTML del reporte mensual.
        # Para MVP retornamos el demo si no podemos parsear el formato.
        return self._demo_articles()

    def _demo_articles(self) -> list[Article]:
        from ..data.sample_data import CONFLICTOS_DEMO
        return [
            Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=c["titulo"],
                summary=c["descripcion"],
                region=c.get("region"),
                published=c.get("fecha", datetime.now().isoformat()),
                url=c.get("url", ""),
                criticidad=c.get("severidad", "media"),
                raw=c,
            )
            for c in CONFLICTOS_DEMO
        ]
