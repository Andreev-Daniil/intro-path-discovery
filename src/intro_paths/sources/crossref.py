"""Crossref: запасной источник совместных работ по имени автора (без id авторов)."""

from __future__ import annotations

from ..models import Coauthor, Work
from ..names import is_same_person
from .http import Http

URL = "https://api.crossref.org/works"


class CrossrefSource:
    def __init__(self, http: Http, *, mailto: str = "", rows: int = 50) -> None:
        self.http = http
        self.mailto = mailto
        self.rows = rows

    def find_works(self, name: str, hints: set[str]) -> list[Work]:
        params = {"query.author": name, "rows": self.rows}
        if self.mailto:
            params["mailto"] = self.mailto
        items = self.http.get_json(URL, params).get("message", {}).get("items", [])
        works: list[Work] = []
        for item in items:
            authors = item.get("author") or []
            names = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors]
            if not any(is_same_person(name, n) for n in names):
                continue  # Crossref ищет нечётко — оставляем только работы с этим автором
            doi = item.get("DOI", "")
            issued = (item.get("issued") or {}).get("date-parts") or [[None]]
            works.append(
                Work(
                    id=doi,
                    title=(item.get("title") or [""])[0],
                    year=issued[0][0] if issued and issued[0] else None,
                    doi=doi,
                    url=f"https://doi.org/{doi}" if doi else "",
                    coauthors=tuple(
                        Coauthor(
                            name=n,
                            institutions=tuple(
                                aff.get("name", "") for aff in a.get("affiliation", [])
                            ),
                        )
                        for a, n in zip(authors, names)
                        if n and not is_same_person(name, n)
                    ),
                )
            )
        return works
