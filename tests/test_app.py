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


def _mine_flow(kind_label, name, *, ticker=None):
    """Режим «Мои данные»: демо-ZIP, цель по имени, офлайн-снимки, кнопка «Найти пути»."""
    at = run_app()
    at.sidebar.selectbox[0].select("Мои данные (ZIP экспорта LinkedIn)").run()
    assert not at.exception, [e.value for e in at.exception]
    next(c for c in at.sidebar.checkbox if c.label.startswith("Использовать встроенный")).check()
    next(c for c in at.sidebar.checkbox if c.label.startswith("Офлайн-снимки")).check()
    at.sidebar.radio[0].set_value(kind_label)
    at.run()
    at.sidebar.text_input[0].set_value(name)
    if ticker:
        at.sidebar.text_input[1].set_value(ticker)
    at.run()
    go = next(b for b in at.sidebar.button if b.label == "Найти пути")
    assert not go.disabled
    go.click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_upload_flow_company_target_ranks_paths_with_signals():
    at = _mine_flow("Компания", "Apple Inc")
    labels = [e.label for e in at.expander]
    assert labels[0].startswith("1. John Ternus") and "сила 0.96" in labels[0]
    assert [l.split(". ", 1)[1].split(" — ")[0] for l in labels[:3]] == ["John Ternus", "Kevan Parekh", "Deirdre O'Brien"]
    assert any("СИНТЕТИЧЕСКИЕ" in w.value for w in at.warning)
    text = " ".join(m.value for m in at.markdown)
    assert "директор целевой компании (SEC)" in text and "общий работодатель" in text  # перечень сигналов
    assert "noisy-OR" in text  # разбор формулы


def test_upload_flow_person_target_shows_coauthors_and_through_whom():
    at = _mine_flow("Человек", "Geoffrey Hinton")
    labels = [e.label for e in at.expander]
    assert any("Ilya Sutskever" in l for l in labels)
    text = " ".join(m.value for m in at.markdown)
    assert "совместные публикации с целью" in text
    assert any("Через знакомого знакомого" in str(s.value) for s in at.subheader)


def test_real_file_upload_through_the_uploader_widgets():
    """Пользовательский путь без встроенного демо: файлы идут через file_uploader."""
    at = run_app()
    at.sidebar.selectbox[0].select("Мои данные (ZIP экспорта LinkedIn)").run()
    zip_uploader = next(u for u in at.sidebar.file_uploader if u.label.startswith("ZIP экспорта"))
    csv_uploader = next(u for u in at.sidebar.file_uploader if u.label.startswith("enrichment.csv"))
    zip_uploader.set_value(("my_linkedin_export.zip", app.DEMO_ZIP.read_bytes(), "application/zip")).run()
    csv_uploader.set_value(("enrichment.csv", app.DEMO_ENRICHMENT.read_bytes(), "text/csv")).run()
    next(c for c in at.sidebar.checkbox if c.label.startswith("Офлайн-снимки")).check()
    at.sidebar.text_input[0].set_value("Apple Inc")
    at.run()
    go = next(b for b in at.sidebar.button if b.label == "Найти пути")
    assert not go.disabled
    go.click().run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [e.label for e in at.expander]
    assert labels[0].startswith("1. John Ternus") and "сила 0.96" in labels[0]
    assert not any("СИНТЕТИЧЕСКИЕ" in w.value for w in at.warning)  # это «пользовательская» загрузка


def test_button_is_disabled_until_contacts_and_target_are_given():
    at = run_app()
    at.sidebar.selectbox[0].select("Мои данные (ZIP экспорта LinkedIn)").run()
    go = lambda: next(b for b in at.sidebar.button if b.label == "Найти пути")  # noqa: E731
    assert go().disabled  # ни файла, ни цели
    at.sidebar.text_input[0].set_value("Apple Inc").run()
    assert go().disabled  # цель есть, контактов нет
    next(u for u in at.sidebar.file_uploader if u.label.startswith("ZIP экспорта")).set_value(
        ("x.zip", app.DEMO_ZIP.read_bytes(), "application/zip")).run()
    assert not go().disabled


def test_broken_zip_shows_an_error_instead_of_crashing():
    at = run_app()
    at.sidebar.selectbox[0].select("Мои данные (ZIP экспорта LinkedIn)").run()
    next(u for u in at.sidebar.file_uploader if u.label.startswith("ZIP экспорта")).set_value(
        ("broken.zip", b"this is not a zip", "application/zip")).run()
    at.sidebar.text_input[0].set_value("Apple Inc").run()
    next(b for b in at.sidebar.button if b.label == "Найти пути").click().run()
    assert not at.exception and any("Ошибка" in e.value for e in at.error)


def test_upload_flow_unknown_target_explains_missing_snapshot():
    at = _mine_flow("Компания", "Несуществующая Компания")
    assert any("Снимка для цели нет" in str(c.value) for c in at.caption)
    assert not at.exception


def test_demo_zip_goes_through_the_same_loader_as_a_real_upload(tmp_path):
    data = app.DEMO_ZIP.read_bytes()
    net = app.load_network_from_zip(data, app.DEMO_ENRICHMENT.read_bytes(), tmp_path)
    assert len(net.contacts) == 68 and net.owner.full_name == "Alex Demo"
    assert any(c.interactions for c in net.contacts.values())  # сообщения и приглашения прочитаны
    assert any(c.affiliations for c in net.contacts.values())


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
