import json
from datetime import date
from pathlib import Path

import pytest

from intro_paths.graph import build_graph, find_chain_paths, graph_stats
from intro_paths.network import build_network
from intro_paths.paths import find_intro_paths
from intro_paths.store import load_network, load_target
from intro_paths.synthetic import generate

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"
TODAY = date(2026, 11, 15)
SCENARIOS = json.loads((DEMO / "scenarios.json").read_text(encoding="utf-8"))
DEMO_CONTACTS = len(SCENARIOS)  # по записи на контакт


@pytest.fixture(scope="module")
def net():
    return load_network(DEMO / "network.json")


@pytest.fixture(scope="module")
def apple():
    return load_target(DEMO / "targets" / "apple.json")


@pytest.fixture(scope="module")
def hinton():
    return load_target(DEMO / "targets" / "hinton.json")


def ranked(net, target):
    return {p.connector.id: p for p in find_intro_paths(net, target, today=TODAY, limit=1000)}


def signal(path, code):
    return next(s for s in path.signals if s.code == code)


def test_generation_is_deterministic_and_matches_committed(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(a, seed=7, snapshots_dir=DEMO / "snapshots")
    generate(b, seed=7, snapshots_dir=DEMO / "snapshots")
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files and files == sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    for rel in files:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel
        committed = DEMO / rel  # закоммиченный набор не должен отставать от генератора
        assert committed.exists() and committed.read_bytes() == (a / rel).read_bytes(), rel


def test_different_seed_changes_filler_only(tmp_path):
    generate(tmp_path, seed=8, snapshots_dir=DEMO / "snapshots")
    other = load_network(tmp_path / "network.json")
    assert other.contacts["demo-kevan-parekh"].full_name == "Kevan Parekh"
    assert (tmp_path / "export" / "Connections.csv").read_bytes() != (DEMO / "export" / "Connections.csv").read_bytes()


def test_export_goes_through_regular_import(tmp_path):
    network = build_network(DEMO / "export", DEMO / "enrichment.csv")
    assert len(network.contacts) == DEMO_CONTACTS >= 100
    assert network.owner.full_name == "Alex Demo"
    assert any(c.interactions for c in network.contacts.values())
    assert all(c.profile_url.startswith("https://www.linkedin.com/in/demo-") for c in network.contacts.values())
    assert "вымышлены" in (DEMO / "DISCLAIMER.md").read_text(encoding="utf-8")


def test_apple_officers_corroborated_beat_directors_name_only(net, apple):
    paths = ranked(net, apple)
    corroborated = signal(paths["demo-sabih-khan"], "insider_officer")
    homonym = signal(paths["demo-sabih-khan-2"], "insider_officer")
    assert corroborated.weight == 0.7 and "подтверждено" in corroborated.detail
    assert homonym.weight == 0.35 and "только имя" in homonym.detail  # ложное совпадение ×0.5
    levinson = signal(paths["demo-arthur-levinson"], "insider_director")
    assert levinson.weight == 0.45 and "только имя" in levinson.detail
    assert signal(paths["demo-timothy-cook"], "insider_director").weight == 0.9
    assert paths["demo-sabih-khan"].strength > paths["demo-sabih-khan-2"].strength
    assert all(s.evidence_url.startswith("https://www.sec.gov/") for p in (paths["demo-sabih-khan"],)
               for s in p.signals if s.code.startswith("insider"))


def test_apple_top_ranking_and_apostrophe_name(net, apple):
    top = [p.connector.id for p in find_intro_paths(net, apple, today=TODAY, limit=4)]
    assert top == ["demo-john-ternus", "demo-kevan-parekh", "demo-deirdre-obrien", "demo-sabih-khan"]


def test_cold_contact_is_penalised_when_dataset_has_messages(net, apple):
    paths = ranked(net, apple)
    cook = paths["demo-timothy-cook"]  # ни одного сообщения, только старое приглашение
    assert not [i for i in net.contacts["demo-timothy-cook"].interactions if i.kind == "message"]
    assert signal(cook, "interaction").detail.endswith("0.09")
    # директор (0.9) + работодатель (0.6) -> 0.96 до множителя; ×(0.5 + 0.5×0.09)
    assert cook.strength == round(0.96 * (0.5 + 0.5 * 0.09), 4)
    assert cook.strength < paths["demo-sabih-khan"].strength  # слабее, чем у тёплого офицера


def test_hinton_confirmed_vs_name_only_coauthors(net, hinton):
    paths = ranked(net, hinton)
    sutskever = signal(paths["demo-ilya-sutskever"], "coauthor")
    mohamed = signal(paths["demo-abdelrahman-mohamed"], "coauthor")
    salakhutdinov = signal(paths["demo-ruslan-salakhutdinov"], "coauthor")
    assert "подтверждено" in sutskever.detail and sutskever.weight == 0.595
    assert "только имя" in mohamed.detail and mohamed.weight == 0.2975
    # без общих работ в network.json Salakhutdinov остался бы «только имя»
    assert "подтверждено" in salakhutdinov.detail and salakhutdinov.weight > mohamed.weight
    assert sutskever.evidence_url.startswith("https://openalex.org/")
    assert paths["demo-ilya-sutskever"].strength > 0.8


def test_hinton_same_employer_signals_from_public_affiliations(net, hinton):
    paths = ranked(net, hinton)
    codes = {s.code for s in paths["demo-max-marlowe"].signals}
    assert "same_employer" in codes
    employers = [s.detail for s in paths["demo-max-marlowe"].signals if s.code == "same_employer"]
    assert any("Google" in d for d in employers) and any("University of Toronto" in d for d in employers)


def _target(key):
    return load_target(DEMO / "targets" / f"{key}.json")


@pytest.mark.parametrize("scenario", json.loads((DEMO / "demo_scenarios.json").read_text(encoding="utf-8")),
                         ids=lambda s: s["id"])
def test_scenario_table_matches_what_the_engine_returns(net, scenario):
    """Таблица «очевидных сценариев» в интерфейсе и README не может врать: сверяем с движком."""
    paths = list(ranked(net, _target(scenario["id"])).values())
    paths.sort(key=lambda p: (-p.strength, p.connector.full_name))
    if "found" in scenario:
        assert len(paths) == scenario["found"]
        return
    if "top" in scenario:
        best = paths[0]
        assert best.connector.full_name == scenario["top"], [p.connector.full_name for p in paths[:3]]
    else:
        name, rank = scenario["in_top"]
        best = next(p for p in paths[:rank] if p.connector.full_name == name)
    codes = {s.code for s in best.signals}
    assert set(scenario["codes"]) <= codes, (best.connector.full_name, codes)


def test_demo_volume_and_obvious_tags():
    tags = {v["tag"] for v in SCENARIOS.values()}
    assert DEMO_CONTACTS >= 100
    assert {"obvious_insider_current", "obvious_current_colleague", "obvious_former_colleague",
            "obvious_cold_colleague", "obvious_board_member", "obvious_former_board",
            "obvious_classmate_coauthor", "obvious_colleague_coauthor", "obvious_classmate",
            "obvious_name_only", "obvious_coauthor_name_only"} <= tags
    assert {p.stem for p in (DEMO / "targets").glob("*.json")} >= {
        "apple", "hinton", "northwind", "contoso", "smirnova", "globex"}


def test_obvious_scenarios_in_detail(net):
    nw = ranked(net, _target("northwind"))
    assert signal(nw["demo-marina-kovalenko"], "insider_officer").weight == 0.7  # руководитель в контактах
    assert nw["demo-dmitri-sokolov"].strength > nw["demo-sven-aldrich"].strength  # тёплый коллега > холодного
    assert signal(nw["demo-olga-volkova"], "same_employer").weight == 0.6  # бывший коллега
    assert "только имя" in signal(nw["demo-irina-belova"], "insider_director").detail  # тёзка директора

    co = ranked(net, _target("contoso"))
    assert signal(co["demo-ravi-menon"], "same_board").weight == 0.9 and co["demo-ravi-menon"].strength == 0.9
    assert signal(co["demo-hannah-brooks"], "same_board").weight == 0.9  # бывший член совета

    sm = ranked(net, _target("smirnova"))
    assert {s.code for s in sm["demo-anna-orlova"].signals} >= {"same_school", "coauthor"}
    assert "подтверждено" in signal(sm["demo-anna-orlova"], "coauthor").detail
    assert "только имя" in signal(sm["demo-leo-brandt"], "coauthor").detail
    assert signal(sm["demo-victor-hale"], "coauthor").weight == 0.6  # подтверждено общими работами

    assert ranked(net, _target("globex")) == {}  # путей нет — честный пустой результат


def test_chain_through_acquaintance_for_new_targets(net):
    nw = find_chain_paths(net, _target("northwind"), today=TODAY, limit=3)
    assert nw and nw[0].bridge_name == "Peter Lindqvist" and nw[0].via.full_name == "Dmitri Sokolov"
    sm = find_chain_paths(net, _target("smirnova"), today=TODAY, limit=3)
    assert any(c.bridge_name == "Samuel Okoye" for c in sm)  # соавтор цели, которого нет в контактах


@pytest.mark.parametrize("name", ["apple", "hinton"])
def test_graph_for_demo_is_connected_and_has_chains(net, apple, hinton, name):
    target = apple if name == "apple" else hinton
    paths = find_intro_paths(net, target, today=TODAY, limit=5)
    chains = find_chain_paths(net, target, today=TODAY, limit=5)
    g = build_graph(net, target, paths, chains, today=TODAY)
    stats = graph_stats(g)
    assert stats["nodes_by_type"]["contact"] == DEMO_CONTACTS
    assert stats["isolated"] == 0 and stats["components"] == 1
    assert chains and chains[0].strength > 0 and stats["nodes_by_type"].get("bridge", 0) >= 1
    assert g.highlight
