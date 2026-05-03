"""Recolector de Twitter/X — API v2 (recent search) + modo demo.

Configuración:
  - Variable de entorno TWITTER_BEARER_TOKEN, o
  - clave 'twitter_bearer_token' en config.yaml (no recomendado por seguridad).

Documentación: https://developer.x.com/en/docs/twitter-api/tweets/search/api-reference/get-tweets-search-recent
"""
from __future__ import annotations
import os
import json
from datetime import datetime
from .base import BaseCollector, Article


# Queries por defecto (se pueden override desde config.yaml)
DEFAULT_QUERIES = [
    "(Peru OR Perú) (Congreso OR vacancia OR ministro) lang:es -is:retweet",
    "(Peru OR Perú) (paro OR bloqueo OR conflicto) lang:es -is:retweet",
    "(Peru OR Perú) (corrupción OR fiscalía OR audios) lang:es -is:retweet",
    "(Peru OR Perú) (riesgo país OR sol peruano OR BCR) lang:es -is:retweet",
]


class TwitterCollector(BaseCollector):
    source_id = "twitter_x"
    source_name = "Twitter / X"
    category = "redes"

    def __init__(self, config: dict, demo: bool = True):
        super().__init__(config, demo=demo)
        self.bearer = os.getenv("TWITTER_BEARER_TOKEN") or config.get("twitter_bearer_token")
        self.queries = config.get("twitter_queries", DEFAULT_QUERIES)
        self.max_per_query = config.get("twitter_max_per_query", 25)

    def collect(self) -> list[Article]:
        if self.demo or not self.bearer:
            if not self.demo and not self.bearer:
                print("  [info] twitter: TWITTER_BEARER_TOKEN no definido → usando demo")
            return self._demo_tweets()
        return self._fetch_real()

    def _fetch_real(self) -> list[Article]:
        import requests
        url = "https://api.twitter.com/2/tweets/search/recent"
        headers = {"Authorization": f"Bearer {self.bearer}"}
        out: list[Article] = []
        for q in self.queries:
            params = {
                "query": q,
                "max_results": str(min(100, self.max_per_query)),
                "tweet.fields": "created_at,public_metrics,author_id,lang",
                "expansions": "author_id",
                "user.fields": "username,name,verified",
            }
            try:
                r = requests.get(url, headers=headers, params=params, timeout=15)
                if r.status_code != 200:
                    print(f"  [warn] twitter [{r.status_code}]: {r.text[:120]}")
                    continue
                payload = r.json()
                users = {u["id"]: u for u in payload.get("includes", {}).get("users", [])}
                for t in payload.get("data", []):
                    user = users.get(t["author_id"], {})
                    handle = user.get("username", "user")
                    name = user.get("name", handle)
                    text = t["text"]
                    metrics = t.get("public_metrics", {})
                    tweet_url = f"https://x.com/{handle}/status/{t['id']}"
                    out.append(Article(
                        source_id=self.source_id,
                        source_name=f"X · @{handle}",
                        category=self.category,
                        title=text[:120] + ("…" if len(text) > 120 else ""),
                        summary=text,
                        url=tweet_url,
                        published=t.get("created_at", ""),
                        criticidad=self._criticidad(metrics, text),
                        raw={
                            "tweet_id": t["id"],
                            "handle": handle,
                            "name": name,
                            "verified": user.get("verified", False),
                            "metrics": metrics,
                            "lang": t.get("lang", "es"),
                            "query": q,
                        },
                    ))
            except Exception as e:
                print(f"  [warn] twitter: {e}")
        if not out:
            return self._demo_tweets()
        return out

    @staticmethod
    def _criticidad(metrics: dict, text: str) -> str:
        rt = metrics.get("retweet_count", 0)
        likes = metrics.get("like_count", 0)
        score = rt * 2 + likes
        critical_kw = ["urgente", "vacancia", "renuncia", "bloqueo", "atentado", "muerto"]
        if score >= 1000 or any(k in text.lower() for k in critical_kw):
            return "alta"
        if score >= 200:
            return "media"
        return "baja"

    def _demo_tweets(self) -> list[Article]:
        from ..data.sample_data import TWEETS_DEMO
        out = []
        for t in TWEETS_DEMO:
            handle = t["handle"]
            # En modo demo no tenemos IDs reales de tweets, así que el URL apunta al PERFIL del autor.
            # Cuando se active el modo live con TWITTER_BEARER_TOKEN, los URLs incluirán status/ID reales.
            tweet_url = f"https://x.com/{handle}"
            text = t["text"]
            out.append(Article(
                source_id=self.source_id,
                source_name=f"X · @{handle}",
                category=self.category,
                title=text[:120] + ("…" if len(text) > 120 else ""),
                summary=text,
                url=tweet_url,
                published=t.get("created_at", datetime.now().isoformat()),
                criticidad=t.get("criticidad", "media"),
                raw={
                    "tweet_id": t.get("id", ""),
                    "handle": handle,
                    "name": t.get("name", handle),
                    "verified": t.get("verified", False),
                    "metrics": t.get("metrics", {}),
                    "hashtags": t.get("hashtags", []),
                    "mentions": t.get("mentions", []),
                },
            ))
        return out
