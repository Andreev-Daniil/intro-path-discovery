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


def test_ui_command_starts_streamlit_with_the_current_interpreter(monkeypatch):
    import subprocess
    import sys

    seen = {}
    monkeypatch.setattr(subprocess, "call", lambda cmd: seen.setdefault("cmd", cmd) and 0)
    assert main(["ui", "--port", "8600", "--no-browser"]) == 0
    cmd = seen["cmd"]
    assert cmd[0] == sys.executable and cmd[1:4] == ["-m", "streamlit", "run"]
    assert cmd[4].endswith("app.py") and "8600" in cmd and "--server.headless" in cmd


def test_missing_file_returns_error_code(tmp_path, capsys):
    code = main([
        "import",
        "--export-dir", str(tmp_path / "nope"),
        "--out", str(tmp_path / "network.json"),
    ])
    assert code == 2
    assert "Ошибка" in capsys.readouterr().err
