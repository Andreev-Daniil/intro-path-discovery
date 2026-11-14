"""SEC EDGAR: директора и офицеры публичной компании по формам 3/4/5.

Покрывает только компании, подающие отчётность в SEC. Периоды приблизительные:
берутся даты подачи форм (первая и последняя), а не фактические сроки полномочий.
Требуется User-Agent с контактом (политика SEC); лимит — не более 10 запросов/с.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

from ..models import Insider
from ..names import from_sec_name
from ..normalize import normalize_org
from .http import Http, HttpError

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INSIDER_FORMS = {"3", "4", "5", "3/A", "4/A", "5/A"}


def _flag(node: ET.Element | None, tag: str) -> bool:
    text = (node.findtext(tag) if node is not None else "") or ""
    return text.strip().lower() in {"1", "true"}


def parse_form_xml(xml_text: str) -> list[dict]:
    """Владельцы, указанные в форме 3/4/5: имя, роль, должность."""
    root = ET.fromstring(xml_text)
    result = []
    for owner in root.findall("reportingOwner"):
        name = (owner.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        rel = owner.find("reportingOwnerRelationship")
        if not name:
            continue
        result.append(
            {
                "name": name,
                "is_director": _flag(rel, "isDirector"),
                "is_officer": _flag(rel, "isOfficer"),
                "title": ((rel.findtext("officerTitle") if rel is not None else "") or "").strip(),
            }
        )
    return result


class EdgarSource:
    def __init__(self, http: Http, *, max_filings: int = 60) -> None:
        self.http = http
        self.max_filings = max_filings

    def resolve_company(self, query: str) -> dict | None:
        """Найти компанию по тикеру или названию -> {cik, ticker, title}."""
        data = self.http.get_json(TICKERS_URL)
        rows = list(data.values()) if isinstance(data, dict) else list(data)
        q = query.strip()
        for row in rows:
            if row.get("ticker", "").lower() == q.lower():
                return {"cik": int(row["cik_str"]), "ticker": row["ticker"], "title": row["title"]}
        qn = normalize_org(q)
        if not qn:
            return None
        exact = [r for r in rows if normalize_org(r.get("title")) == qn]
        loose = [r for r in rows if qn in normalize_org(r.get("title"))]
        pool = exact or sorted(loose, key=lambda r: len(r.get("title", "")))
        if not pool:
            return None
        row = pool[0]
        return {"cik": int(row["cik_str"]), "ticker": row.get("ticker", ""), "title": row["title"]}

    def fetch_insiders(self, cik: int) -> list[Insider]:
        data = self.http.get_json(SUBMISSIONS_URL.format(cik=cik))
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        picked = [
            i for i, form in enumerate(forms) if form in INSIDER_FORMS
        ][: self.max_filings]

        merged: dict[str, dict] = {}
        for i in picked:
            acc = recent["accessionNumber"][i].replace("-", "")
            doc = recent["primaryDocument"][i].split("/")[-1]  # без xsl-префикса — «сырой» XML
            url = ARCHIVE_URL.format(cik=cik, acc=acc, doc=doc)
            try:
                owners = parse_form_xml(self.http.get_text(url))
            except (HttpError, ET.ParseError):
                continue
            filed = date.fromisoformat(recent["filingDate"][i])
            for owner in owners:
                key = from_sec_name(owner["name"]).lower()
                m = merged.setdefault(
                    key,
                    {"name": from_sec_name(owner["name"]), "director": False, "officer": False,
                     "title": "", "first": filed, "last": filed, "url": url},
                )
                m["director"] |= owner["is_director"]
                m["officer"] |= owner["is_officer"]
                if owner["title"] and filed >= m["last"]:
                    m["title"] = owner["title"]
                if filed < m["first"]:
                    m["first"] = filed
                if filed >= m["last"]:
                    m["last"], m["url"] = filed, url
        return [
            Insider(
                name=m["name"],
                is_director=m["director"],
                is_officer=m["officer"],
                title=m["title"],
                first_seen=m["first"],
                last_seen=m["last"],
                source_url=m["url"],
            )
            for m in merged.values()
        ]
