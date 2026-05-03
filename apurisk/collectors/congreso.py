"""Recolector de proyectos de ley del Congreso del Perú."""
from __future__ import annotations
from datetime import datetime
from .base import BaseCollector, Article


class CongresoCollector(BaseCollector):
    source_id = "congreso_proyectos"
    source_name = "Congreso del Perú - Proyectos de Ley"
    category = "estado"

    def collect(self) -> list[Article]:
        # El portal del Congreso usa SPA con API JSON; en producción
        # consultar https://wb2server.congreso.gob.pe/spley-portal/api ...
        if self.demo:
            return self._demo_articles()
        return self._demo_articles()

    def _demo_articles(self) -> list[Article]:
        from ..data.sample_data import PROYECTOS_LEY_DEMO
        return [
            Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=p["titulo"],
                summary=p["resumen"],
                published=p.get("fecha", datetime.now().isoformat()),
                url=p.get("url", ""),
                raw=p,
            )
            for p in PROYECTOS_LEY_DEMO
        ]
