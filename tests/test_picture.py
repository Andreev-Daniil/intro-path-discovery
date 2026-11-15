import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from intro_paths.graph import build_graph, find_chain_paths
from intro_paths.models import Network, Person, Target
from intro_paths.paths import find_intro_paths
from intro_paths.picture import graph_svg, paths_diagram_svg
from intro_paths.store import load_network, load_target

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 15)


def parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)  # SVG обязан быть корректным XML


def texts(svg: str) -> str:
    return " ".join("".join(t.itertext()) for t in parse(svg).iter() if t.tag.endswith("text"))


def demo(key):
    return load_network(ROOT / "data/demo/network.json"), load_target(ROOT / f"data/demo/targets/{key}.json")


def test_paths_diagram_shows_owner_contacts_signals_and_target():
    net, target = demo("contoso")
    paths = find_intro_paths(net, target, today=TODAY, limit=5)
    svg = paths_diagram_svg("Alex Demo", target.name, paths)
    text = texts(svg)
    assert "Alex Demo" in text and "Contoso Labs" in text
    assert "Ravi Menon" in text and "0.90" in text and "совет директоров" in text
    assert all(p.connector.full_name in text for p in paths[:5])


def test_paths_diagram_marks_chains_as_unconfirmed():
    net, target = demo("northwind")
    paths = find_intro_paths(net, target, today=TODAY, limit=3)
    chains = find_chain_paths(net, target, today=TODAY, limit=2)
    svg = paths_diagram_svg("Alex Demo", target.name, paths, chains)
    assert "Peter Lindqvist" in texts(svg) and "stroke-dasharray" in svg and "не подтверждена" in texts(svg)


def test_empty_result_is_a_clear_message_not_a_blank_image():
    net, target = demo("globex")
    svg = paths_diagram_svg("Alex Demo", target.name, find_intro_paths(net, target, today=TODAY))
    assert "не найдено" in texts(svg) and "Globex Corporation" in texts(svg)


def test_names_are_escaped_in_svg():
    from intro_paths.models import Affiliation, Period
    from intro_paths.normalize import normalize_org

    net = Network(Person("me", "A", "B"), {"x": Person("x", "<script>", "O'Brien & Co", affiliations=[
        Affiliation("position", "Acme", normalize_org("Acme"), Period(), "")])})
    target = Target("Acme", [Affiliation("position", "Acme", normalize_org("Acme"), Period(), "")])
    svg = paths_diagram_svg("A B", "Acme <b>", find_intro_paths(net, target, today=TODAY))
    parse(svg)  # не ломает XML
    assert "<script>" not in svg and "&lt;script&gt;" in svg


def test_graph_map_is_valid_deterministic_and_contains_key_nodes():
    net, target = demo("apple")
    paths = find_intro_paths(net, target, today=TODAY, limit=5)
    graph = build_graph(net, target, paths, today=TODAY)
    svg = graph_svg(graph)
    assert svg == graph_svg(graph)  # детерминированно
    text = texts(svg)
    assert "Apple Inc" in text and "Alex Demo" in text and "John Ternus" in text
    root = parse(svg)
    assert len([e for e in root.iter() if e.tag.endswith("line")]) == len(graph.edges)
    assert "#e74c3c" in svg  # топ-пути выделены красным


def test_graph_map_handles_empty_graph():
    from intro_paths.graph import Graph

    assert "Граф пуст" in texts(graph_svg(Graph()))
