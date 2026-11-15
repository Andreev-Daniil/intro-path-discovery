"""Онлайн-режим включён всегда: живые источники, при сбое — офлайн-снимок. В тестах сеть не используется."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import requests

from fakes import FakeHttp, form4
from intro_paths.agent.tools import FlakyEnrichment, LiveEnrichment, SnapshotEnrichment, ToolError
from intro_paths.config import Services
from intro_paths.models import Target
from intro_paths.online import OFFLINE_ENV, check_online, enrich_online, forced_offline, live_enrich
from intro_paths.sources.edgar import EdgarSource
from intro_paths.sources.http import HttpError
from intro_paths.sources.openalex import OpenAlexSource

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data/demo/targets"


@pytest.fixture(autouse=True)
def online_on(monkeypatch):
    """Эти тесты проверяют онлайн-логику на подставных сервисах: принудительный офлайн снимаем."""
    monkeypatch.delenv(OFFLINE_ENV, raising=False)


def edgar_services():
    base = "https://www.sec.gov/Archives/edgar/data/7/"
    http = FakeHttp({
        "https://www.sec.gov/files/company_tickers.json": {"0": {"cik_str": 7, "ticker": "EXC", "title": "Example Corp"}},
        "https://data.sec.gov/submissions/CIK0000000007.json": {"filings": {"recent": {
            "form": ["4"], "accessionNumber": ["0000000007-25-000001"],
            "primaryDocument": ["f.xml"], "filingDate": ["2025-02-01"]}}},
        base + "000000000725000001/f.xml": form4("DOE JANE", director=1),
    })
    return Services(edgar=EdgarSource(http))


def test_online_is_used_first_and_reported():
    target, notes = enrich_online(Target("Example Corp", kind="company"), "EXC",
                                  services=edgar_services(), snapshot_dir=SNAPSHOTS)
    assert [i.name for i in target.insiders] == ["Jane Doe"]
    assert notes == ["Онлайн: SEC EDGAR — инсайдеров 1, работ 0"]


def test_company_unknown_to_the_service_falls_back_to_the_offline_snapshot():
    # SEC ответил, но такой компании нет в его списке -> берётся снимок, и это сказано явно
    target, notes = enrich_online(Target("Apple Inc", kind="company"), None,
                                  services=edgar_services(), snapshot_dir=SNAPSHOTS)
    assert any("ответил, но данных о цели нет" in n for n in notes)
    assert any("Запасной офлайн-снимок" in n for n in notes)
    assert len(target.insiders) == 16


def test_fictional_demo_targets_never_touch_the_network():
    """Имя вымышленной цели совпало бы с реальным человеком/компанией — онлайн её не ищем."""
    class Boom:
        def get_json(self, *a, **k):
            raise AssertionError("обращение к сети для вымышленной цели")

        get_text = get_json

    services = Services(edgar=EdgarSource(Boom()), publications=[OpenAlexSource(Boom())])
    for name, kind in [("Elena Smirnova", "person"), ("Contoso Labs", "company"), ("Globex Corporation", "company")]:
        target, notes = enrich_online(Target(name, kind=kind), None, services=services, snapshot_dir=SNAPSHOTS)
        assert target.fictional and notes == ["Вымышленная демо-цель: онлайн-источники не запрашиваются, данные из демо-набора"]
    assert any(w.id == "DEMO-W1" for w in
               enrich_online(Target("Elena Smirnova", kind="person"), None, services=services,
                             snapshot_dir=SNAPSHOTS)[0].works)  # свои демо-статьи, а не чужие из OpenAlex


def test_agent_enrichment_also_skips_the_network_for_fictional_targets(monkeypatch):
    import intro_paths.online as online

    monkeypatch.setattr(online, "live_enrich", lambda *a, **k: (_ for _ in ()).throw(AssertionError("сеть")))
    target = LiveEnrichment(fallback=SnapshotEnrichment(SNAPSHOTS)).enrich("Contoso Labs", "company", None)
    assert target.fictional and any(a.kind == "board" for a in target.affiliations)


def test_person_online_data_is_completed_with_snapshot_affiliations():
    authors = {"results": [{"id": "https://openalex.org/A1", "display_name": "Geoffrey Hinton", "works_count": 9}]}
    works = {"results": [{"id": "https://openalex.org/W1", "title": "Deep learning", "publication_year": 2015,
                          "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Geoffrey Hinton"},
                                           "institutions": []},
                                          {"author": {"id": "https://openalex.org/A2", "display_name": "Yann LeCun"},
                                           "institutions": [{"display_name": "NYU"}]}]}], "meta": {"next_cursor": None}}
    http = FakeHttp({"https://api.openalex.org/authors": authors, "https://api.openalex.org/works": works})
    target, notes = enrich_online(Target("Geoffrey Hinton", kind="person"), None,
                                  services=Services(publications=[OpenAlexSource(http)]), snapshot_dir=SNAPSHOTS)
    assert [w.id for w in target.works] == ["W1"]  # работы — живые, не из снимка
    assert {a.name for a in target.affiliations} >= {"University of Toronto", "Google", "Vector Institute"}
    assert any("дополнены из офлайн-снимка" in n for n in notes)


def test_service_failure_falls_back_to_the_snapshot_and_says_so():
    class Down:
        def get_json(self, *a, **k):
            raise HttpError("503 для https://www.sec.gov")

        get_text = get_json

    target, notes = enrich_online(Target("Apple Inc", kind="company"), "AAPL",
                                  services=Services(edgar=EdgarSource(Down())), snapshot_dir=SNAPSHOTS)
    assert any("Онлайн-источник недоступен" in n and "503" in n for n in notes)
    assert len(target.insiders) == 16  # данные из снимка Apple


def test_no_snapshot_and_no_service_keeps_the_plain_target():
    class Down:
        def get_json(self, *a, **k):
            raise requests.ConnectionError("нет сети")

        get_text = get_json

    start = Target("Nobody Corp", kind="company")
    target, notes = enrich_online(start, None, services=Services(edgar=EdgarSource(Down())), snapshot_dir=SNAPSHOTS)
    assert target is start and any("Офлайн-снимка для этой цели нет" in n for n in notes)


def test_person_target_uses_openalex_online():
    authors = {"results": [{"id": "https://openalex.org/A1", "display_name": "Jane Doe", "works_count": 3}]}
    works = {"results": [{"id": "https://openalex.org/W1", "title": "Graphs", "publication_year": 2024,
                          "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Jane Doe"},
                                           "institutions": []}]}], "meta": {"next_cursor": None}}
    http = FakeHttp({"https://api.openalex.org/authors": authors, "https://api.openalex.org/works": works})
    target, notes = enrich_online(Target("Jane Doe", kind="person"), None,
                                  services=Services(publications=[OpenAlexSource(http)]), snapshot_dir=None)
    assert [w.id for w in target.works] == ["W1"] and notes[0].startswith("Онлайн: OpenAlexSource")


def test_forced_offline_switch_disables_the_network(monkeypatch):
    monkeypatch.setenv(OFFLINE_ENV, "1")
    assert forced_offline()
    with pytest.raises(ValueError, match="сеть отключена"):
        live_enrich(Target("Apple Inc", kind="company"), "AAPL", services=edgar_services())
    target, notes = enrich_online(Target("Apple Inc", kind="company"), "AAPL",
                                  services=edgar_services(), snapshot_dir=SNAPSHOTS)
    assert len(target.insiders) == 16 and any("сеть отключена" in n for n in notes)


def test_agent_live_enrichment_falls_back_and_surfaces_timeouts(monkeypatch):
    snap = SnapshotEnrichment(SNAPSHOTS)
    monkeypatch.setenv(OFFLINE_ENV, "1")  # онлайн недоступен -> запасной снимок, агент получает данные
    assert len(LiveEnrichment(fallback=snap).enrich("Apple Inc", "company", "AAPL").insiders) == 16
    with pytest.raises(ToolError, match="онлайн-источник недоступен"):
        LiveEnrichment(fallback=None).enrich("Apple Inc", "company", "AAPL")
    # сбой, который имитирует демо, остаётся таймаутом: так агент видит ошибку и выходит из неё
    with pytest.raises(TimeoutError):
        FlakyEnrichment(LiveEnrichment(fallback=snap), 1).enrich("Apple Inc", "company", None)


# --------------------------------------------------------------------------- статус сервисов

def make_get(statuses):
    def get(url, headers=None, timeout=8.0):
        for key, value in statuses.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return NS(status_code=value)
        raise AssertionError(url)
    return get


def test_status_reports_all_services_without_leaking_secrets():
    env = {"API_KEY": "sk-secret-value-123", "API_LINK": "https://router.example/api/v1",
           "SEC_USER_AGENT": "Jane Doe jane@example.com"}
    items = check_online(env, make_get({"router.example": 200, "sec.gov": 200, "openalex": 200, "crossref": 200}))
    assert [i["name"] for i in items] == ["Модель (OpenAI-совместимый API)", "SEC EDGAR", "OpenAlex", "Crossref"]
    assert all(i["ok"] for i in items)
    assert "sk-secret-value-123" not in json.dumps(items) and "router.example" not in json.dumps(items)


def test_status_explains_what_is_missing_or_broken():
    items = check_online({}, make_get({"openalex": 200, "crossref": requests.ConnectionError("x")}))
    by = {i["name"]: i for i in items}
    assert not by["Модель (OpenAI-совместимый API)"]["ok"] and "API_KEY" in by["Модель (OpenAI-совместимый API)"]["detail"]
    assert not by["SEC EDGAR"]["ok"] and "SEC_USER_AGENT" in by["SEC EDGAR"]["detail"]
    assert by["OpenAlex"]["ok"] and not by["Crossref"]["ok"] and "нет связи" in by["Crossref"]["detail"]
    bad = check_online({"API_KEY": "k", "API_LINK": "https://r.example"}, make_get({"r.example": 401, "openalex": 200,
                                                                                      "crossref": 200}))
    assert "доступ отклонён" in bad[0]["detail"] and not bad[0]["ok"]
    insecure = check_online({"API_KEY": "k", "API_LINK": "http://r.example"}, make_get({"openalex": 200, "crossref": 200}))
    assert "https://" in insecure[0]["detail"]
