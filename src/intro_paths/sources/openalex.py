"""OpenAlex: поиск автора, его работ и соавторов (https://api.openalex.org)."""

from __future__ import annotations

from ..models import Coauthor, Work
from ..names import is_same_person
from ..normalize import normalize_org
from .http import Http, HttpError

BASE = "https://api.openalex.org"
SELECT = "id,title,publication_year,doi,authorships"


def _short(openalex_id: str) -> str:
    return openalex_id.rsplit("/", 1)[-1] if openalex_id else ""


class OpenAlexSource:
    def __init__(self, http: Http, *, api_key: str = "", mailto: str = "", max_pages: int = 2) -> None:
        self.http = http
        self.api_key = api_key
        self.mailto = mailto
        self.max_pages = max_pages

    def _params(self, **extra) -> dict:
        params = dict(extra)
        if self.api_key:
            params["api_key"] = self.api_key
        if self.mailto:
            params["mailto"] = self.mailto
        return params

    def resolve_author(self, name: str, hints: set[str] | None = None) -> dict | None:
        """Лучший кандидат -> {id, name, corroborated}; corroborated = совпала организация."""
        hints = hints or set()
        data = self.http.get_json(f"{BASE}/authors", self._params(search=name, per_page=10))
        candidates = []
        for row in data.get("results", []):
            if not is_same_person(name, row.get("display_name", "")):
                continue
            orgs = {
                normalize_org(i.get("display_name"))
                for i in row.get("last_known_institutions") or []
            }
            for aff in row.get("affiliations") or []:
                orgs.add(normalize_org((aff.get("institution") or {}).get("display_name")))
            candidates.append(
                {
                    "id": _short(row["id"]),
                    "name": row["display_name"],
                    "corroborated": bool(hints & orgs),
                    "works_count": row.get("works_count", 0),
                }
            )
        if not candidates:
            return None
        candidates.sort(key=lambda c: (not c["corroborated"], -c["works_count"]))
        best = candidates[0]
        return {"id": best["id"], "name": best["name"], "corroborated": best["corroborated"]}

    def fetch_works(self, author_id: str, max_works: int = 200) -> list[Work]:
        works: list[Work] = []
        cursor = "*"
        for page in range(self.max_pages):
            try:
                data = self.http.get_json(
                    f"{BASE}/works",
                    self._params(
                        filter=f"authorships.author.id:{author_id}",
                        per_page=100,
                        select=SELECT,
                        cursor=cursor,
                    ),
                )
            except HttpError:
                if page == 0:
                    raise
                break  # без API-ключа OpenAlex отдаёт только первую страницу (cursor=…)
            for row in data.get("results", []):
                coauthors = []
                for a in row.get("authorships", []):
                    author = a.get("author") or {}
                    if _short(author.get("id", "")) == author_id:
                        continue
                    coauthors.append(
                        Coauthor(
                            name=author.get("display_name", ""),
                            openalex_id=_short(author.get("id", "")),
                            institutions=tuple(
                                i.get("display_name", "") for i in a.get("institutions", [])
                            ),
                        )
                    )
                doi = (row.get("doi") or "").replace("https://doi.org/", "")
                works.append(
                    Work(
                        id=_short(row["id"]),
                        title=row.get("title") or "",
                        year=row.get("publication_year"),
                        doi=doi,
                        url=row["id"],
                        coauthors=tuple(coauthors),
                    )
                )
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor or len(works) >= max_works:
                break
        return works[:max_works]

    def find_works(self, name: str, hints: set[str]) -> list[Work]:
        author = self.resolve_author(name, hints)
        return self.fetch_works(author["id"]) if author else []
