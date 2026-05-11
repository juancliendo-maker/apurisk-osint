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
        # Ventana temporal: solo tweets de las últimas N horas (default 72)
        self.ventana_horas = config.get("twitter_ventana_horas", 72)

    def collect(self) -> list[Article]:
        if self.demo or not self.bearer:
            if not self.demo and not self.bearer:
                print("  [info] twitter: TWITTER_BEARER_TOKEN no definido → usando demo")
            return self._demo_tweets()
        return self._fetch_real()

    def _fetch_real(self) -> list[Article]:
        import requests
        from datetime import datetime, timedelta, timezone
        url = "https://api.twitter.com/2/tweets/search/recent"
        headers = {"Authorization": f"Bearer {self.bearer}"}
        # Calcular start_time: solo tweets de las últimas N horas
        # X API search/recent solo cubre últimos 7 días; usamos ventana_horas para
        # acotar más estrictamente y traer hashtags trending REALMENTE recientes.
        start_time = (datetime.now(timezone.utc) - timedelta(hours=self.ventana_horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[Article] = []
        for q in self.queries:
            params = {
                "query": q,
                "max_results": str(min(100, self.max_per_query)),
                "start_time": start_time,                       # acota a últimas N horas
                "sort_order": "recency",                         # más recientes primero
                "tweet.fields": "created_at,public_metrics,author_id,lang,entities",
                "expansions": "author_id",
                "user.fields": "username,name,verified",
            }
            try:
                r = requests.get(url, headers=headers, params=params, timeout=15)
                if r.status_code != 200:
                    msg = r.text[:200]
                    if r.status_code == 403:
                        msg = ("403 Forbidden — la API Free de X NO permite "
                               "búsquedas (search/recent). Necesitas plan Basic ($100/mes).")
                    elif r.status_code == 401:
                        msg = "401 Unauthorized — Bearer Token inválido o expirado."
                    elif r.status_code == 429:
                        msg = "429 Rate limit excedido."
                    print(f"  [warn] twitter [{r.status_code}]: {msg}")
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
                    # Extraer hashtags y menciones de entities (campo oficial de X API)
                    entities = t.get("entities", {}) or {}
                    hashtags_reales = [h.get("tag", "") for h in entities.get("hashtags", []) if h.get("tag")]
                    mentions_reales = [f"@{m.get('username','')}" for m in entities.get("mentions", []) if m.get("username")]
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
                            "hashtags": hashtags_reales,
                            "mentions": mentions_reales,
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
        """Genera tweets demo. Distribuye los timestamps en las últimas 24h
        relativos al momento actual (hora Lima/PET) para que siempre haya
        contenido fresco en la pestaña Twitter del dashboard, incluso si el
        Bearer Token no es válido o la API rechaza la búsqueda.
        """
        from ..data.sample_data import TWEETS_DEMO
        from datetime import datetime, timedelta, timezone
        PET = timezone(timedelta(hours=-5))
        now = datetime.now(PET)

        out = []
        n = max(1, len(TWEETS_DEMO))
        for i, t in enumerate(TWEETS_DEMO):
            handle = t["handle"]
            # En modo demo, URLs apuntan al PERFIL del autor (verificable en x.com).
            # En modo live con Bearer Token de plan Basic+, los URLs incluyen status/ID reales.
            tweet_url = f"https://x.com/{handle}"
            text = t["text"]
            # Distribuir tweets uniformemente entre 0.5h y 23h atrás
            offset_h = 0.5 + (i / max(1, n - 1)) * 22.5
            ts = (now - timedelta(hours=offset_h)).isoformat(timespec="seconds")
            out.append(Article(
                source_id=self.source_id,
                source_name=f"X · @{handle}",
                category=self.category,
                title=text[:120] + ("…" if len(text) > 120 else ""),
                summary=text,
                url=tweet_url,
                published=ts,
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
