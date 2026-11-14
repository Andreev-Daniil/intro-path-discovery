from pathlib import Path

import pytest

from intro_paths.network import build_network

DATA = Path(__file__).resolve().parents[1] / "data" / "sample"


def test_build_network_from_sample():
    net = build_network(DATA / "export", DATA / "enrichment.csv")

    assert net.owner.full_name == "Анна Тестова"
    assert len(net.contacts) == 5
    assert "ivan-petrov-example" in net.contacts

    ivan = net.contacts["ivan-petrov-example"]
    assert sorted(a.kind for a in ivan.affiliations) == ["education", "position"]


def test_multiline_description_does_not_break_rows():
    net = build_network(DATA / "export")
    positions = [a.norm for a in net.owner.affiliations if a.kind == "position"]
    assert positions == ["example corp", "ромашка"]


def test_connection_without_company_has_no_position():
    net = build_network(DATA / "export", DATA / "enrichment.csv")
    pavel = net.contacts["pavel-novikov-example"]
    assert [a.kind for a in pavel.affiliations] == ["board"]


def test_connection_ids_are_unique(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    (export / "Connections.csv").write_text(
        "First Name,Last Name,URL,Company,Position,Connected On\n"
        "Иван,Петров,,,,01 Jan 2020\n"
        "Иван,Петров,,,,02 Jan 2020\n",
        encoding="utf-8",
    )
    net = build_network(export)
    assert sorted(net.contacts) == ["иван-петров", "иван-петров-2"]


def test_unknown_person_in_enrichment_raises(tmp_path):
    enrichment = tmp_path / "enrichment.csv"
    enrichment.write_text(
        "person_id,kind,name,start,end,role\n"
        "ivan-petrvo,education,MSU,2010,2014,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="неизвестный person_id"):
        build_network(DATA / "export", enrichment)


def test_missing_connections_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_network(tmp_path)
