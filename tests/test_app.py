import io
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pyvis")

from streamlit.testing.v1 import AppTest  # noqa: E402

from intro_paths import app  # noqa: E402
from intro_paths.graph import build_graph  # noqa: E402
from intro_paths.store import load_network, load_target  # noqa: E402
from intro_paths.ui_graph import render_graph_html  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data" / "sample"
DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"
APP = Path(app.__file__)


def run_app():
    return AppTest.from_file(str(APP), default_timeout=60).run()


def test_default_dataset_renders_all_tabs_without_errors():
    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs] == ["Обзор", "Данные", "Граф", "Пути", "Как считается",
                                          "Источники и приватность"]
    assert any("СИНТЕТИЧЕСКИЕ" in w.value for w in at.warning)
    labels = [e.label for e in at.expander]
    assert labels and "John Ternus" in labels[0]  # Apple: первым идёт CEO из SEC


def test_switching_to_hinton_dataset():
    at = run_app()
    at.sidebar.selectbox[0].select("Синтетика: Geoffrey Hinton (OpenAlex)").run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [e.label for e in at.expander]
    assert labels and "Max Marlowe" in labels[0]  # Hinton: общие работодатели + тёплая связь
    assert any("Ilya Sutskever" in l for l in labels)


def test_weight_slider_changes_ranking():
    at = run_app()
    before = [e.label for e in at.expander][:3]
    next(s for s in at.slider if s.key == "w_dir").set_value(0.0)
    next(s for s in at.slider if s.key == "w_off").set_value(0.0)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    after = [e.label for e in at.expander][:3]
    assert before != after


def test_mine_dataset_waits_for_upload():
    at = run_app()
    at.sidebar.selectbox[0].select("Мои данные (ZIP экспорта LinkedIn)").run()
    assert not at.exception
    assert any("Загрузите ZIP" in i.value for i in at.info)


def test_graph_html_is_self_contained_and_labelled():
    from datetime import date
    from intro_paths.paths import find_intro_paths
    net, tgt = load_network(DEMO / "network.json"), load_target(DEMO / "targets" / "apple.json")
    paths = find_intro_paths(net, tgt, today=date(2026, 11, 15), limit=3)
    html = render_graph_html(build_graph(net, tgt, paths, today=date(2026, 11, 15)), search="ternus")
    assert "John Ternus" in html and "Apple Inc" in html
    assert "cdn.jsdelivr.net" not in html and "unpkg.com" not in html
    only = render_graph_html(build_graph(net, tgt, paths, today=date(2026, 11, 15)), only_highlight=True)
    assert len(only) < len(html) or "John Ternus" in only


def test_explain_formula_matches_strength():
    from datetime import date
    from intro_paths.paths import find_intro_paths
    net, tgt = load_network(DEMO / "network.json"), load_target(DEMO / "targets" / "apple.json")
    today = date(2026, 11, 15)
    path = find_intro_paths(net, tgt, today=today, limit=1)[0]
    text = "\n".join(app.explain(path, today, True))
    assert f"**{path.strength:.2f}**" in text and "noisy-OR" in text


def test_zip_loader_finds_nested_export(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in (DATA / "export").iterdir():
            zf.write(f, f"Basic_LinkedInDataExport/{f.name}")
    net = app.load_network_from_zip(buf.getvalue(), None, tmp_path)
    assert len(net.contacts) == 5


def test_zip_without_connections_is_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(ValueError, match="Connections.csv"):
        app.load_network_from_zip(buf.getvalue(), None, tmp_path)


def test_zip_path_traversal_is_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.csv", "x")
    with pytest.raises(ValueError, match="подозрительный"):
        app.load_network_from_zip(buf.getvalue(), None, tmp_path)
