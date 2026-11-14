"""Обогащение цели внешними источниками (EDGAR, OpenAlex, Crossref).

Запрашивается только цель; контакты сопоставляются локально по именам.
Контакты уходят во внешние сервисы лишь в режиме deep — и только топ-N
кандидатов, у которых уже есть совпадение по имени с соавторами цели.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Sequence

from .models import Affiliation, Network, Period, Target
from .normalize import normalize_org
from .scoring import compute_coauthor_signals
from .sources.base import PublicationSource
from .sources.edgar import EdgarSource

Progress = Callable[[str], None]


def _hints(affiliations: list[Affiliation]) -> set[str]:
    return {a.norm for a in affiliations if a.norm}


def enrich_company(target: Target, edgar: EdgarSource, *, query: str = "", progress: Progress = lambda m: None) -> dict:
    company = edgar.resolve_company(query or target.name)
    if not company:
        progress("EDGAR: компания не найдена")
        return {"company": None, "insiders": 0}
    progress(f"EDGAR: {company['title']} (CIK {company['cik']}), загружаю формы 3/4/5…")
    target.kind = "company"
    if not any(a.kind == "position" for a in target.affiliations):
        for name in {company["title"], target.name}:
            target.affiliations.append(
                Affiliation("position", name, normalize_org(name), Period(), "")
            )
    target.insiders = edgar.fetch_insiders(company["cik"])
    return {"company": company, "insiders": len(target.insiders)}


def enrich_person(
    target: Target,
    sources: Sequence[PublicationSource],
    *,
    progress: Progress = lambda m: None,
) -> dict:
    target.kind = "person"
    hints = _hints(target.affiliations)
    for source in sources:
        name = type(source).__name__
        progress(f"{name}: ищу работы «{target.name}»…")
        works = source.find_works(target.name, hints)
        if works:
            target.works = works
            return {"source": name, "works": len(works)}
    return {"source": None, "works": 0}


def confirm_candidates(
    network: Network,
    target: Target,
    sources: Sequence[PublicationSource],
    *,
    top: int = 20,
    today: date | None = None,
    progress: Progress = lambda m: None,
) -> int:
    """Deep: для контактов, совпавших по имени с соавторами цели, загрузить их работы.

    Общие id работ превращают слабое совпадение по имени в подтверждённое.
    """
    today = today or date.today()
    scored = []
    for contact in network.contacts.values():
        signals = compute_coauthor_signals(contact, target, today)
        if signals:
            scored.append((signals[0].weight, contact))
    scored.sort(key=lambda x: -x[0])
    done = 0
    for _, contact in scored[:top]:
        progress(f"Проверяю работы контакта {contact.full_name}…")
        for source in sources:
            works = source.find_works(contact.full_name, _hints(contact.affiliations))
            if works:
                contact.works = works
                done += 1
                break
    return done
