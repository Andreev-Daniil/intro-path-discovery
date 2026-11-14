import json
from pathlib import Path

from intro_paths.cli import main

DATA = Path(__file__).resolve().parents[1] / "data" / "sample"


def test_import_then_paths_json(tmp_path, capsys):
    network_file = tmp_path / "network.json"
    code = main([
        "import",
        "--export-dir", str(DATA / "export"),
        "--enrichment", str(DATA / "enrichment.csv"),
        "--out", str(network_file),
    ])
    assert code == 0
    assert network_file.exists()
    capsys.readouterr()

    code = main([
        "paths",
        "--network", str(network_file),
        "--target", str(DATA / "target_person.json"),
        "--limit", "2",
        "--json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["connector"]["id"] for item in payload] == [
        "pavel-novikov-example",
        "ivan-petrov-example",
    ]
    assert payload[0]["strength"] == 0.9
    assert payload[0]["signals"][0]["code"] == "same_board"


def test_missing_file_returns_error_code(tmp_path, capsys):
    code = main([
        "import",
        "--export-dir", str(tmp_path / "nope"),
        "--out", str(tmp_path / "network.json"),
    ])
    assert code == 2
    assert "Ошибка" in capsys.readouterr().err
