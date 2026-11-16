"""Цель уже среди контактов: реальный случай с живой модели («John Ternus» — контакт, а система отвечала «путей нет»)."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from intro_paths.agent.config import AgentConfig
from intro_paths.agent.tools import Session, SnapshotEnrichment, build_tools, execute_tool
from intro_paths.agent.loop import SYSTEM_PROMPT
from intro_paths.models import Target
from intro_paths.paths import direct_contacts, find_intro_paths, with_direct_contacts
from intro_paths.store import load_network

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 15)
NET = load_network(ROOT / "data/demo/network.json")
TOOLS = {t.name: t for t in build_tools()}


def test_target_who_is_already_a_contact_is_detected():
    target = Target("John Ternus", kind="person")
    assert [c.id for c in direct_contacts(NET, target)] == ["demo-john-ternus"]
    assert direct_contacts(NET, Target("John Ternus", kind="company")) == []  # только для цели-человека
    assert direct_contacts(NET, Target("Nobody Here", kind="person")) == []


def test_known_affiliations_make_colleagues_the_intermediaries_and_the_target_never_introduces_himself():
    target = Target("John Ternus", kind="person")
    enriched, direct = with_direct_contacts(NET, target)
    assert target.affiliations == [] and any(a.name == "Apple Inc" for a in enriched.affiliations)  # исходная цель цела
    assert find_intro_paths(NET, target, today=TODAY) == []  # раньше именно так: «путей нет»
    paths = find_intro_paths(NET, enriched, today=TODAY, limit=5)
    assert paths and all(p.connector.id != "demo-john-ternus" for p in paths)
    assert all(any(s.code == "same_employer" for s in p.signals) for p in paths)  # коллеги по Apple


def session(tmp_path):
    cfg = replace(AgentConfig(provider="scripted", script="x", outbox_dir=str(tmp_path / "o")), enrichment="snapshot")
    return Session(NET, cfg, SnapshotEnrichment(ROOT / "data/demo/targets"), lambda p: False, TODAY)


def test_tool_tells_the_model_that_the_target_is_already_a_contact(tmp_path):
    text, is_error = execute_tool(session(tmp_path), TOOLS, "find_intro_paths",
                                  {"target": "John Ternus", "kind": "person", "limit": 3})
    assert not is_error
    out = json.loads(text)
    assert out["found"] == 3 and out["paths"][0]["connector_id"] != "demo-john-ternus"
    me = out["target_is_your_contact"][0]
    assert me["connector_id"] == "demo-john-ternus" and me["messages"] == 8 and "Apple Inc" in me["known_employers"]
    assert "писать напрямую" in out["hint"]


def test_tool_output_is_unchanged_for_ordinary_targets(tmp_path):
    out = json.loads(execute_tool(session(tmp_path), TOOLS, "find_intro_paths",
                                  {"target": "Apple Inc", "kind": "company"})[0])
    assert "target_is_your_contact" not in out


def test_system_prompt_tells_the_model_to_report_it_first():
    assert "target_is_your_contact" in SYSTEM_PROMPT and "скажи" in SYSTEM_PROMPT
