from datetime import date

from fakes import FakeHttp, form4
from intro_paths.sources.crossref import CrossrefSource
from intro_paths.sources.edgar import EdgarSource
from intro_paths.sources.openalex import OpenAlexSource

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1, "ticker": "APLE", "title": "Apple Hospitality REIT, Inc."},
}


def test_edgar_resolve_by_ticker_and_name():
    edgar = EdgarSource(FakeHttp({"https://www.sec.gov/files/company_tickers.json": TICKERS}))
    assert edgar.resolve_company("aapl")["cik"] == 320193
    assert edgar.resolve_company("Apple Inc")["cik"] == 320193
    assert edgar.resolve_company("Несуществующая")  is None


def test_edgar_fetch_insiders_merges_filings():
    submissions = {
        "filings": {
            "recent": {
                "form": ["4", "4", "8-K", "3"],
                "accessionNumber": ["0000000001-24-000003", "0000000001-22-000002",
                                    "0000000001-24-000009", "0000000001-19-000001"],
                "primaryDocument": ["xslF345X05/a.xml", "xslF345X03/b.xml", "x.htm", "c.xml"],
                "filingDate": ["2024-05-01", "2022-03-01", "2024-06-01", "2019-02-01"],
            }
        }
    }
    base = "https://www.sec.gov/Archives/edgar/data/320193/"
    http = FakeHttp(
        {
            "https://data.sec.gov/submissions/CIK0000320193.json": submissions,
            base + "000000000124000003/a.xml": form4("COOK TIMOTHY D", officer=1, title="CEO"),
            base + "000000000122000002/b.xml": form4("COOK TIMOTHY D", director=1),
            base + "000000000119000001/c.xml": form4("LEVINSON ARTHUR D", director=1),
        }
    )
    insiders = {i.name: i for i in EdgarSource(http).fetch_insiders(320193)}
    cook = insiders["Timothy D Cook"]
    assert cook.is_director and cook.is_officer and cook.title == "CEO"
    assert cook.first_seen == date(2022, 3, 1) and cook.last_seen == date(2024, 5, 1)
    assert insiders["Arthur D Levinson"].is_director
    assert len(insiders) == 2  # 8-K пропущен


AUTHORS = {
    "results": [
        {"id": "https://openalex.org/A1", "display_name": "Jane Doe", "works_count": 5,
         "last_known_institutions": [{"display_name": "MIT"}]},
        {"id": "https://openalex.org/A2", "display_name": "Jane Doe", "works_count": 90,
         "last_known_institutions": [{"display_name": "Other University"}]},
        {"id": "https://openalex.org/A3", "display_name": "John Doe", "works_count": 500},
    ]
}
WORKS = {
    "results": [
        {"id": "https://openalex.org/W10", "title": "Graphs", "publication_year": 2021,
         "doi": "https://doi.org/10.1/x",
         "authorships": [
             {"author": {"id": "https://openalex.org/A1", "display_name": "Jane Doe"}, "institutions": []},
             {"author": {"id": "https://openalex.org/A9", "display_name": "Bob Roe"},
              "institutions": [{"display_name": "MIT"}]},
         ]}
    ],
    "meta": {"next_cursor": None},
}


def test_openalex_prefers_candidate_with_matching_institution():
    src = OpenAlexSource(FakeHttp({"https://api.openalex.org/authors": AUTHORS}))
    best = src.resolve_author("Jane Doe", {"mit"})
    assert best == {"id": "A1", "name": "Jane Doe", "corroborated": True}
    # без подсказки побеждает автор с большим числом работ, но без подтверждения
    assert src.resolve_author("Jane Doe", set())["id"] == "A2"
    assert src.resolve_author("Nobody Else", set()) is None


def test_openalex_find_works_excludes_self_and_sends_key():
    http = FakeHttp({"https://api.openalex.org/authors": AUTHORS, "https://api.openalex.org/works": WORKS})
    src = OpenAlexSource(http, api_key="K", mailto="m@example.com")
    works = src.find_works("Jane Doe", {"mit"})
    assert [w.id for w in works] == ["W10"]
    assert works[0].doi == "10.1/x"
    assert [c.name for c in works[0].coauthors] == ["Bob Roe"]
    assert works[0].coauthors[0].institutions == ("MIT",)
    works_call = [p for u, p in http.calls if u.endswith("/works")][0]
    assert works_call["filter"] == "authorships.author.id:A1" and works_call["api_key"] == "K"


def test_crossref_keeps_only_works_with_the_author():
    payload = {"message": {"items": [
        {"DOI": "10.2/y", "title": ["Paper"], "issued": {"date-parts": [[2020]]},
         "author": [{"given": "Jane", "family": "Doe"},
                    {"given": "Bob", "family": "Roe", "affiliation": [{"name": "MIT"}]}]},
        {"DOI": "10.2/z", "title": ["Other"], "author": [{"given": "Alice", "family": "Poe"}]},
    ]}}
    works = CrossrefSource(FakeHttp({"https://api.crossref.org/works": payload})).find_works("Jane Doe", set())
    assert [w.doi for w in works] == ["10.2/y"]
    assert works[0].year == 2020 and [c.name for c in works[0].coauthors] == ["Bob Roe"]
