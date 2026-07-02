"""Job source scrapers — unified interface for French and Swiss job boards."""

from emploi.sources.apec import ApecOffer, search_apec
from emploi.sources.cadremploi import CadremploiOffer, search_cadremploi
from emploi.sources.comparis import ComparisOffer, search_comparis
from emploi.sources.jobs_ch import JobsChOffer, search_jobs_ch
from emploi.sources.jobup import JobupOffer, search_jobup
from emploi.sources.monster import MonsterOffer, search_monster
from emploi.sources.okjob import OkjobOffer, search_okjob

__all__ = [
    # French sources
    "search_apec",
    "ApecOffer",
    "search_monster",
    "MonsterOffer",
    "search_cadremploi",
    "CadremploiOffer",
    # Swiss sources
    "search_okjob",
    "OkjobOffer",
    "search_jobup",
    "JobupOffer",
    "search_jobs_ch",
    "JobsChOffer",
    "search_comparis",
    "ComparisOffer",
]
