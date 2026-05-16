"""Módulo de recolectores OSINT - APURISK 1.0"""
from .base import BaseCollector, Article
from .rss_media import RSSMediaCollector
from .defensoria import DefensoriaCollector
from .gdelt import GDELTCollector
from .congreso import CongresoCollector
from .twitter import TwitterCollector
from .acled import ACLEDCollector
from .crimen_organizado import CrimenOrganizadoCollector

__all__ = [
    "BaseCollector",
    "Article",
    "RSSMediaCollector",
    "DefensoriaCollector",
    "GDELTCollector",
    "CongresoCollector",
    "TwitterCollector",
    "ACLEDCollector",
    "CrimenOrganizadoCollector",
]
