from datetime import date
from pathlib import Path

import pytest

from intro_paths.network import build_network
from intro_paths.paths import find_intro_paths
from intro_paths.scoring import Signal, combine_strength
from intro_paths.store import load_target

DATA = Path(__file__).resolve().parents[1] / "data" / "sample"
TODAY = date(2026, 11, 15)


@pytest.fixture(scope="module")
def net():
    return build_network(DATA / "export", DATA / "enrichment.csv")


def test_company_target_ranks_current_employees(net):
    target = load_target(DATA / "target_company.json")
    paths = find_intro_paths(net, target, today=TODAY)

    assert [p.connector.full_name for p in paths] == ["Иван Петров", "Олег Смирнов"]
    assert all(p.strength == pytest.approx(0.6) for p in paths)


def test_person_target_ranks_board_then_school_then_employer(net):
    target = load_target(DATA / "target_person.json")
    paths = find_intro_paths(net, target, today=TODAY)

    assert [p.connector.full_name for p in paths] == ["Павел Новиков", "Иван Петров", "Олег Смирнов"]
    strengths = {p.connector.full_name: p.strength for p in paths}
    assert strengths["Павел Новиков"] == pytest.approx(0.9)
    assert strengths["Иван Петров"] == pytest.approx(0.65)  # 1 - (1-0.3)*(1-0.5)
    assert strengths["Олег Смирнов"] == pytest.approx(0.3)  # даты неизвестны


def test_signals_explain_the_path(net):
    target = load_target(DATA / "target_person.json")
    ivan = find_intro_paths(net, target, today=TODAY)[1]
    codes = {s.code for s in ivan.signals}
    assert codes == {"same_employer", "same_school"}
    assert any("даты неизвестны" in s.detail for s in ivan.signals)


def test_min_strength_and_limit(net):
    target = load_target(DATA / "target_person.json")
    strong = find_intro_paths(net, target, today=TODAY, min_strength=0.5)
    assert [p.connector.full_name for p in strong] == ["Павел Новиков", "Иван Петров"]

    limited = find_intro_paths(net, target, today=TODAY, limit=1)
    assert len(limited) == 1


def test_combine_strength_is_noisy_or():
    signals = [Signal("a", "", 0.6), Signal("b", "", 0.5)]
    assert combine_strength(signals) == pytest.approx(0.8)
    assert combine_strength([]) == 0.0
