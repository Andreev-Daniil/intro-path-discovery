import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from intro_paths.agent.config import load_config
from intro_paths.agent.llm import ScriptedLLM
from intro_paths.agent.runner import AgentRunner, classify
from intro_paths.store import load_network

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 15)
NET = load_network(ROOT / "data/demo/network.json")


def cfg_for(name, tmp_path):
    cfg = load_config(ROOT / f"config/{name}.toml")
    return replace(cfg, script=str(ROOT / cfg.script) if cfg.script else "", targets_dir=str(ROOT / cfg.targets_dir),
                   outbox_dir=str(tmp_path / "outbox"))


def test_classify_parses_loop_log_lines():
    assert classify("[шаг 2] вызов find_intro_paths({'limit': 50})").kind == "call"
    e = classify("[шаг 2] ОШИБКА: неверные аргументы: limit: 50 is greater than the maximum of 10")
    assert (e.kind, e.step) == ("error", 2) and "maximum of 10" in e.text
    assert classify('[шаг 3] ок: {"found": 10}').kind == "ok"
    assert classify("[шаг 1] модель: привет").text == "привет"
    assert classify("[стоп] Достигнут лимит шагов").kind == "stop"


def test_runner_streams_error_then_fix_and_waits_for_the_human(tmp_path):
    runner = AgentRunner(cfg_for("agent.demo-errors", tmp_path), "Найди пути к Contoso Labs", NET, TODAY).start()
    assert runner.wait_pending(10), "агент должен дойти до вопроса человеку"
    assert runner.running and runner.pending and "Ravi Menon" in runner.pending
    kinds = [(e.kind, e.step) for e in runner.snapshot()]
    assert ("error", 2) in kinds and ("error", 4) in kinds  # обе ошибки уже видны, пока ждём ответ
    assert not (tmp_path / "outbox").exists()  # до ответа ничего не записано
    runner.answer(True)
    runner.join(10)
    assert runner.result and runner.result.stop_reason == "end_turn" and not runner.running
    assert [p.name for p in (tmp_path / "outbox").glob("*.json")] == ["intro_me_1.json"]
    assert runner.pending is None


def test_runner_decline_leaves_no_file(tmp_path):
    runner = AgentRunner(cfg_for("agent.demo-errors", tmp_path), "x", NET, TODAY).start()
    assert runner.wait_pending(10)
    runner.answer(False)
    runner.join(10)
    assert runner.result.tool_calls[-1].is_error and not (tmp_path / "outbox").exists()


def test_confirmation_times_out_as_a_refusal(tmp_path):
    runner = AgentRunner(cfg_for("agent.demo-errors", tmp_path), "x", NET, TODAY, confirm_timeout=0.3).start()
    runner.join(10)
    assert runner.result and runner.result.tool_calls[-1].is_error and not (tmp_path / "outbox").exists()
    assert any("Время ожидания" in e.text for e in runner.snapshot())


def test_answer_without_a_question_is_ignored(tmp_path):
    runner = AgentRunner(cfg_for("agent.demo", tmp_path), "x", NET, TODAY)
    runner.answer(True)  # ничего не ждёт — не падает и не «одобряет заранее»
    assert runner.pending is None and runner._answer is None


def test_config_problem_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_LINK", raising=False)
    runner = AgentRunner(cfg_for("agent", tmp_path), "привет", NET, TODAY).start()
    runner.join(10)
    assert runner.result is None and "API_KEY" in runner.error
    assert any(e.kind == "error" and "API_KEY" in e.text for e in runner.snapshot())


def test_runner_with_injected_llm_and_timeout_recovery(tmp_path):
    cfg = replace(cfg_for("agent.demo", tmp_path), simulate_enrichment_failures=1)
    llm = ScriptedLLM(json.loads((ROOT / "data/demo/agent_script_apple.json").read_text(encoding="utf-8")))
    runner = AgentRunner(cfg, "Apple", NET, TODAY, llm=llm).start()
    assert runner.wait_pending(10)
    runner.answer(False)
    runner.join(10)
    flags = [(r.name, r.is_error) for r in runner.result.tool_calls]
    assert flags[:2] == [("enrich_target", True), ("enrich_target", False)]
