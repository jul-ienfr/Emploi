"""Job source scrapers — unified interface for multiple French job boards."""

from emploi.sources.apec import ApecOffer, search_apec
from emploi.sources.cadremploi import CadremploiOffer, search_cadremploi
from emploi.sources.monster import MonsterOffer, search_monster

__all__ = [
    "search_apec",
    "ApecOffer",
    "search_monster",
    "MonsterOffer",
    "search_cadremploi",
    "CadremploiOffer",
]
