import json
from pathlib import Path

from fakes import FakeHttp, form4
from intro_paths import cli
from intro_paths.config import Services, build_services
from intro_paths.sources.edgar import EdgarSource

DATA = Path(__file__).resolve().parents[1] / "data" / "sample"


def _edgar():
    base = "https://www.sec.gov/Archives/edgar/data/7/"
    return EdgarSource(FakeHttp({
        "https://www.sec.gov/files/company_tickers.json": {"0": {"cik_str": 7, "ticker": "EXC", "title": "Example Corp"}},
        "https://data.sec.gov/submissions/CIK0000000007.json": {"filings": {"recent": {
            "form": ["4"], "accessionNumber": ["0000000007-25-000001"],
            "primaryDocument": ["xslF345X05/f.xml"], "filingDate": ["2025-02-01"]}}},
        base + "000000000725000001/f.xml": form4("НОВИКОВ ПАВЕЛ", director=1),
    }))


def test_enrich_company_then_paths(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "build_services", lambda **kw: Services(edgar=_edgar()))
    out = tmp_path / "target.json"
    assert cli.main(["enrich-target", "--target-company", "Example Corp", "--out-target", str(out)]) == 0
    assert json.loads(out.read_text("utf-8"))["insiders"][0]["name"] == "Павел Новиков"

    net = tmp_path / "net.json"
    assert cli.main(["import", "--export-dir", str(DATA / "export"),
                     "--enrichment", str(DATA / "enrichment.csv"), "--out", str(net)]) == 0
    capsys.readouterr()
    assert cli.main(["paths", "--network", str(net), "--target", str(out), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    pavel = next(r for r in result if r["connector"]["id"] == "pavel-novikov-example")
    assert [s["code"] for s in pavel["signals"]] == ["insider_director"]
    assert pavel["signals"][0]["evidence_url"].startswith("https://www.sec.gov/Archives")


def test_enrich_requires_exactly_one_target(capsys):
    assert cli.main(["enrich-target", "--out-target", "x.json"]) == 2
    assert "ровно одно" in capsys.readouterr().err


def test_edgar_needs_user_agent():
    try:
        build_services(use_edgar=True, use_openalex=False, use_crossref=False, env={})
    except ValueError as exc:
        assert "SEC_USER_AGENT" in str(exc)
    else:
        raise AssertionError("ожидали ValueError")
