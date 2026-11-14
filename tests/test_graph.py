from datetime import date

import pytest

from intro_paths.graph import build_graph, find_chain_paths, graph_stats
from intro_paths.models import (
    Affiliation, Coauthor, Insider, Interaction, Network, Period, Person, Target, Work,
)
from intro_paths.normalize import normalize_org
from intro_paths.paths import find_intro_paths
from intro_paths.scoring import DEFAULT, ScoringConfig

TODAY = date(2026, 11, 15)


def aff(kind, name, start=None, end=None):
    return Affiliation(kind, name, normalize_org(name), Period(start, end), "")


def person(pid, first, last, *affs, **kw):
    return Person(pid, first, last, affiliations=list(affs), **kw)


@pytest.fixture
def net():
    me = person("me", "Alex", "Demo", aff("education", "Toronto U"))
    contacts = {
        "a": person("a", "Ann", "Lee", aff("position", "Acme Corp"), aff("education", "Toronto U"),
                    interactions=[Interaction("message", date(2026, 10, 1), 5)]),
        "b": person("b", "Bob", "Roe", aff("position", "Acme Corp")),
        "c": person("c", "Cy", "Moe", aff("position", "Solo Inc")),  # одинокая организация
        "d": person("d", "Dan", "Doe", aff("position", "Elsewhere LLC")),
    }
    return Network(me, contacts)


def target():
    return Target(
        "Acme Corp", [aff("position", "Acme Corp")], kind="company",
        insiders=[Insider("Ann Lee", is_officer=True, title="CFO", last_seen=date(2026, 5, 1), source_url="https://sec/1"),
                  Insider("Zed Ghost", is_director=True, last_seen=date(2026, 5, 1), source_url="https://sec/2")],
    )


def test_nodes_and_edges_by_type(net):
    g = build_graph(net, target(), today=TODAY)
    stats = graph_stats(g)
    assert stats["nodes_by_type"]["contact"] == 4
    assert stats["nodes_by_type"]["insider"] == 1  # Zed Ghost — не контакт
    assert stats["nodes_by_type"]["target"] == 1 and stats["nodes_by_type"]["owner"] == 1
    # «Solo Inc» и «Elsewhere» делит один человек — организации-узла нет, «Acme Corp» есть (цель)
    labels = {n.label for n in g.nodes.values() if n.type == "org"}
    assert labels == {"Acme Corp"}
    # вуз делят двое (владелец и Ann)
    assert {n.label for n in g.nodes.values() if n.type == "school"} == {"Toronto U"}
    assert stats["isolated"] == 0
    assert stats["components"] == 1


def test_min_shared_and_layer_toggles(net):
    g = build_graph(net, target(), show_orgs=False, show_schools=False, today=TODAY)
    assert not [n for n in g.nodes.values() if n.type in ("org", "school")]
    g2 = build_graph(net, target(), min_shared=1, today=TODAY)
    assert {n.label for n in g2.nodes.values() if n.type == "org"} >= {"Acme Corp", "Solo Inc"}


def test_insider_edge_carries_signal_weight_and_evidence(net):
    g = build_graph(net, target(), today=TODAY)
    edge = next(e for e in g.edges if e.kind == "insider_of" and e.src == "c:a")
    assert edge.weight == 0.7 and edge.evidence_url == "https://sec/1"


def test_inferred_edges_are_flagged_and_optional(net):
    plain = build_graph(net, target(), today=TODAY)
    assert not [e for e in plain.edges if e.inferred]
    g = build_graph(net, target(), inferred=True, today=TODAY)
    inferred = [e for e in g.edges if e.inferred]
    assert [(e.src, e.dst, e.kind) for e in inferred] == [("c:a", "c:b", "colleague")]


def test_top_paths_are_highlighted(net):
    paths = find_intro_paths(net, target(), today=TODAY, limit=2)
    g = build_graph(net, target(), paths, today=TODAY)
    assert g.top_connectors == [f"c:{p.connector.id}" for p in paths]
    marked = {(g.edges[i].src, g.edges[i].dst) for i in g.highlight}
    assert ("owner", g.top_connectors[0]) in marked
    assert (g.top_connectors[0], "target") in marked or any(d == "o:acme" for _, d in marked)


def test_work_nodes_only_when_a_contact_is_matched(net):
    t = target()
    t.works = [
        Work("W1", "Paper", 2024, url="https://oa/W1", coauthors=(Coauthor("Bob Roe", "A1"),)),
        Work("W2", "Other", 2023, url="https://oa/W2", coauthors=(Coauthor("Nobody Else", "A2"),)),
    ]
    g = build_graph(net, t, today=TODAY)
    assert [n.id for n in g.nodes.values() if n.type == "work"] == ["w:W1"]


def chain_target():
    t = Target("Jane Doe", kind="person")
    t.works = [Work("W1", "Paper", 2024, url="https://oa/W1",
                    coauthors=(Coauthor("Bob Roe", "A1", ("Acme Corp",)),
                               Coauthor("Zed Ghost", "A9", ("Acme Corp", "Hidden Lab")),
                               Coauthor("No Inst", "A8", ())))]
    return t


def test_chain_path_strength_formula(net):
    chains = find_chain_paths(net, chain_target(), today=TODAY, limit=5)
    # Bob — контакт, у No Inst нет организации; мостом остаётся Zed Ghost
    assert [c.bridge_name for c in chains] == ["Zed Ghost"]
    c = chains[0]
    assert c.via.id == "a"  # Ann тёплая (5 сообщений), Bob в сети с перепиской — холодный
    # сила(B→цель)=0.5; связь = same_employer 0.6 (оба периода открыты -> «пересекаются»);
    # ×скидка 0.5; ×множитель тепла Ann = 0.5 + 0.5×0.7
    assert c.bridge_weight == 0.5 and c.link == 0.6
    assert c.strength == round(0.5 * 0.6 * DEFAULT.hop_discount * 0.85, 4)
    assert find_chain_paths(net, chain_target(), today=TODAY, cfg=ScoringConfig(hop_discount=1.0))[0].strength > c.strength


def test_scoring_config_changes_ranking(net):
    t = Target("Acme Corp", [aff("position", "Acme Corp"), aff("education", "Toronto U")])
    base = find_intro_paths(net, t, today=TODAY)
    boosted = find_intro_paths(net, t, today=TODAY, cfg=ScoringConfig(school=0.99))
    assert boosted[0].strength > base[0].strength
    assert find_intro_paths(net, t, today=TODAY, cfg=DEFAULT)[0].strength == base[0].strength
