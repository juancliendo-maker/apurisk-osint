"""Recolector GDELT - eventos políticos sobre Perú."""
from __future__ import annotations
import json
from datetime import datetime
from .base import BaseCollector, Article


class GDELTCollector(BaseCollector):
    source_id = "gdelt_peru"
    source_name = "GDELT - Eventos Perú"
    category = "internacional"

    def collect(self) -> list[Article]:
        if self.demo:
            return self._demo_articles()
        url = self.config["internacional"]["gdelt_query"]["url"]
        text = self._safe_get(url)
        if not text:
            return self._demo_articles()
        try:
            data = json.loads(text)
            arts = data.get("articles", [])
            return [
                Article(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    category=self.category,
                    title=a.get("title", ""),
                    summary=a.get("seendate", "") + " " + a.get("domain", ""),
                    url=a.get("url", ""),
                    published=a.get("seendate", ""),
                )
                for a in arts[:50]
            ]
        except Exception:
            return self._demo_articles()

    def _demo_articles(self) -> list[Article]:
        from ..data.sample_data import GDELT_DEMO
        return [
            Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=e["title"],
                summary=e.get("summary", ""),
                url=e.get("url", ""),
                published=e.get("date", datetime.now().isoformat()),
                criticidad=e.get("criticidad", "media"),
                raw=e,
            )
            for e in GDELT_DEMO
        ]
