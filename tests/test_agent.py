import json
import re
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from intro_paths.agent.config import AgentConfig, load_config
from intro_paths.agent.llm import LLMError, LLMResponse, ScriptedLLM
from intro_paths.agent.loop import SYSTEM_PROMPT, run_agent, run_from_config
from intro_paths.agent.tools import (
    FlakyEnrichment,
    Session,
    SnapshotEnrichment,
    build_tools,
    execute_tool,
    limit_result,
)
from intro_paths.store import load_network

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 15)
TOOLS = {t.name: t for t in build_tools()}


def make_session(tmp_path, *, confirm=lambda p: False, failures=0, **overrides) -> Session:
    cfg = replace(AgentConfig(provider="scripted", script="x", outbox_dir=str(tmp_path / "outbox")), **overrides)
    inner = SnapshotEnrichment(ROOT / "data/demo/targets")
    enrichment = FlakyEnrichment(inner, failures) if failures else inner
    return Session(load_network(ROOT / "data/demo/network.json"), cfg, enrichment, confirm, TODAY)


def call(id_, name, **args):
    return {"id": id_, "name": name, "input": args}


# --- 1. описание инструмента проходит «тест стажёра» ---------------------------------

@pytest.mark.parametrize("name", list(TOOLS))
def test_tool_descriptions_say_when_what_and_errors(name):
    tool = TOOLS[name]
    text = tool.description
    assert len(text) > 250  # не однострочник
    assert "Ошибки" in text or "Ошибок" in text  # что делать при сбое
    assert re.search(r"[Вв]озвраща|ставит в очередь", text)  # что получим
    for prop in tool.input_schema["properties"].values():
        assert prop.get("description") or prop.get("enum") or prop.get("type") == "boolean" or prop.get("type") == "integer"
    assert 2 <= len(TOOLS) <= 3


def test_dangerous_tool_description_warns_about_confirmation():
    assert "подтверждения" in TOOLS["request_introduction"].description
    assert "НЕ отправлено" in TOOLS["request_introduction"].description or "автоматически не отправляется" in TOOLS["request_introduction"].description


# --- 2. аргументы проверяются по схеме, ошибка возвращается модели ---------------------

@pytest.mark.parametrize("args,fragment", [
    ({"kind": "company"}, "target"),
    ({"target": "Apple Inc", "kind": "city"}, "kind"),
    ({"target": "Apple Inc", "kind": "company", "limit": 500}, "limit"),
    ({"target": "Apple Inc", "kind": "company", "limit": "пять"}, "limit"),
    ({"target": "", "kind": "company"}, "target"),
])
def test_invalid_arguments_return_error_and_do_not_run(tmp_path, args, fragment):
    session = make_session(tmp_path)
    text, is_error = execute_tool(session, TOOLS, "find_intro_paths", args)
    assert is_error and "неверные аргументы" in text and fragment in text


def test_unknown_tool_and_non_object_arguments(tmp_path):
    session = make_session(tmp_path)
    assert execute_tool(session, TOOLS, "drop_database", {})[1]
    text, is_error = execute_tool(session, TOOLS, "find_intro_paths", "Apple")
    assert is_error and "JSON-объектом" in text


def test_agent_recovers_from_invalid_arguments(tmp_path):
    session = make_session(tmp_path)
    llm = ScriptedLLM([
        {"tool_calls": [call("a", "find_intro_paths", target="Apple Inc", kind="company", limit=999)]},
        {"tool_calls": [call("b", "find_intro_paths", target="Apple Inc", kind="company", limit=2)]},
        {"text": "готово"},
    ])
    result = run_agent("найди", llm=llm, session=session)
    assert result.stop_reason == "end_turn"
    assert [r.is_error for r in result.tool_calls] == [True, False]
    # ошибка действительно попала модели: tool_result с is_error
    second_call_messages = llm.seen[1]
    block = second_call_messages[-1]["content"][0]
    assert block["type"] == "tool_result" and block["is_error"] is True and "limit" in block["content"]


# --- 3. клиент и данные берутся из сессии, а не от модели ------------------------------

def test_schemas_have_no_owner_or_path_fields_and_reject_extras(tmp_path):
    forbidden = {"owner", "owner_id", "user", "user_id", "client", "client_id", "account", "network", "path", "file"}
    for tool in TOOLS.values():
        assert tool.input_schema["additionalProperties"] is False
        assert not forbidden & set(tool.input_schema["properties"])
    session = make_session(tmp_path)
    text, is_error = execute_tool(session, TOOLS, "find_intro_paths",
                                  {"target": "Apple Inc", "kind": "company", "owner_id": "someone-else"})
    assert is_error and "owner_id" in text  # подмена владельца отклонена


def test_owner_in_outbox_comes_from_session(tmp_path):
    session = make_session(tmp_path, confirm=lambda p: True)
    text, is_error = execute_tool(session, TOOLS, "request_introduction", {
        "connector_id": "demo-john-ternus", "target": "Apple Inc", "message": "Познакомьте меня, пожалуйста."})
    assert not is_error
    record = json.loads(next((tmp_path / "outbox").glob("*.json")).read_text(encoding="utf-8"))
    assert record["owner"] == session.network.owner.id and record["status"] == "queued_for_manual_send"


def test_unknown_connector_is_rejected_before_confirmation(tmp_path):
    asked = []
    session = make_session(tmp_path, confirm=lambda p: asked.append(p) or True)
    text, is_error = execute_tool(session, TOOLS, "request_introduction", {
        "connector_id": "ghost", "target": "Apple Inc", "message": "Познакомьте меня, пожалуйста."})
    assert is_error and "нет в сети" in text and not asked


# --- 4. таймаут сервиса не роняет агента ---------------------------------------------

def test_service_timeout_becomes_tool_error(tmp_path):
    session = make_session(tmp_path, failures=1)
    text, is_error = execute_tool(session, TOOLS, "enrich_target", {"target": "Apple Inc", "kind": "company"})
    assert is_error and "таймаут внешнего сервиса" in text
    text, is_error = execute_tool(session, TOOLS, "enrich_target", {"target": "Apple Inc", "kind": "company"})
    assert not is_error and '"insiders_found": 16' in text


def test_slow_tool_hits_timeout_without_crashing(tmp_path):
    class Slow:
        def enrich(self, *a):
            time.sleep(1.0)

    session = make_session(tmp_path, tool_timeout_s=0.2)
    session.enrichment = Slow()
    start = time.monotonic()
    text, is_error = execute_tool(session, TOOLS, "enrich_target", {"target": "Apple Inc", "kind": "company"})
    assert is_error and "не уложился" in text and time.monotonic() - start < 0.9


def test_hung_tool_does_not_block_process_exit():
    """Зависший вызов не должен держать процесс после ответа (поток-демон, а не пул)."""
    import subprocess
    import sys

    code = (
        "import time\n"
        "from dataclasses import replace\n"
        "from intro_paths.agent.config import AgentConfig\n"
        "from intro_paths.agent.tools import Session, build_tools, execute_tool\n"
        "from intro_paths.store import load_network\n"
        "class Hung:\n"
        "    def enrich(self, *a): time.sleep(30)\n"
        "s = Session(load_network('data/demo/network.json'), replace(AgentConfig(), tool_timeout_s=0.3), Hung())\n"
        "text, err = execute_tool(s, {t.name: t for t in build_tools()}, 'enrich_target', {'target': 'Apple Inc', 'kind': 'company'})\n"
        "print(err, 'не уложился' in text)\n"
    )
    start = time.monotonic()
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=20)
    assert done.stdout.strip() == "True True" and time.monotonic() - start < 8


def test_unexpected_exception_is_reported_not_raised(tmp_path):
    class Broken:
        def enrich(self, *a):
            raise RuntimeError("boom")

    session = make_session(tmp_path)
    session.enrichment = Broken()
    text, is_error = execute_tool(session, TOOLS, "enrich_target", {"target": "Apple Inc", "kind": "company"})
    assert is_error and "RuntimeError" in text


def test_llm_failure_ends_run_gracefully(tmp_path):
    class Down:
        def create(self, **kw):
            raise LLMError("нет сети до API")

    result = run_agent("привет", llm=Down(), session=make_session(tmp_path))
    assert result.stop_reason == "llm_error" and "нет сети" in result.final_text


# --- 5. результат ограничен по размеру -----------------------------------------------

def test_result_is_truncated_with_marker(tmp_path):
    session = make_session(tmp_path, result_max_chars=500)
    text, is_error = execute_tool(session, TOOLS, "find_intro_paths",
                                  {"target": "Apple Inc", "kind": "company", "limit": 10, "include_chains": True})
    assert not is_error and len(text) <= 500 and "результат обрезан" in text
    assert limit_result("x" * 10, 500) == "x" * 10


# --- 6. есть лимит шагов -------------------------------------------------------------

def test_step_limit_stops_an_endless_agent(tmp_path):
    session = make_session(tmp_path, max_steps=3)
    endless = ScriptedLLM([{"tool_calls": [call(f"c{i}", "find_intro_paths", target="Apple Inc", kind="company")]}
                           for i in range(10)])
    result = run_agent("найди", llm=endless, session=session)
    assert result.stop_reason == "step_limit" and result.steps == 3
    assert endless.i == 3 and "лимит шагов" in result.final_text


# --- 7. действие только после подтверждения человеком ---------------------------------

REQUEST = {"connector_id": "demo-john-ternus", "target": "Apple Inc", "message": "Познакомьте меня, пожалуйста."}


def test_declined_action_is_not_executed(tmp_path):
    session = make_session(tmp_path, confirm=lambda p: False)
    text, is_error = execute_tool(session, TOOLS, "request_introduction", REQUEST)
    assert is_error and "отклонил" in text
    assert not (tmp_path / "outbox").exists() and session.intro_requests == []


def test_declined_action_cannot_be_retried_and_the_human_is_not_asked_again(tmp_path):
    """Модель, получив отказ, повторила вызов (реальный случай на живой модели): код блокирует повтор."""
    asked = []
    session = make_session(tmp_path, confirm=lambda p: asked.append(p) or False)
    first, first_error = execute_tool(session, TOOLS, "request_introduction", REQUEST)
    second, second_error = execute_tool(session, TOOLS, "request_introduction", {**REQUEST, "message": "Другой текст запроса."})
    assert first_error and "отклонил" in first
    assert second_error and "уже отклонил" in second and "Повтор запрещён" in second
    assert len(asked) == 1  # человека не беспокоят вторым вопросом
    assert not (tmp_path / "outbox").exists()


def test_a_new_request_starts_with_a_clean_slate(tmp_path):
    """Отказ действует в рамках одного запроса: в новой сессии (новый запрос пользователя) действие снова доступно."""
    declined = make_session(tmp_path, confirm=lambda p: False)
    execute_tool(declined, TOOLS, "request_introduction", REQUEST)
    fresh = make_session(tmp_path, confirm=lambda p: True)
    assert not execute_tool(fresh, TOOLS, "request_introduction", REQUEST)[1]


def test_system_prompt_separates_contact_target_link_from_user_contact_link():
    assert "связь КОНТАКТА с ЦЕЛЬЮ" in SYSTEM_PROMPT and "не пиши «мы с вами в одном совете»" in SYSTEM_PROMPT


def test_default_confirmation_is_denial(tmp_path):
    session = make_session(tmp_path)  # confirm по умолчанию
    assert execute_tool(session, TOOLS, "request_introduction", REQUEST)[1]


def test_confirmation_question_shows_who_and_what(tmp_path):
    seen = []
    session = make_session(tmp_path, confirm=lambda p: seen.append(p) or True)
    assert not execute_tool(session, TOOLS, "request_introduction", REQUEST)[1]
    assert "John Ternus" in seen[0] and "Apple Inc" in seen[0] and "Познакомьте" in seen[0]


def test_read_only_tools_do_not_ask_for_confirmation(tmp_path):
    asked = []
    session = make_session(tmp_path, confirm=lambda p: asked.append(p) or True)
    execute_tool(session, TOOLS, "find_intro_paths", {"target": "Apple Inc", "kind": "company"})
    assert asked == []


def test_intro_requests_are_capped_per_session(tmp_path):
    session = make_session(tmp_path, confirm=lambda p: True, max_intro_requests=1)
    assert not execute_tool(session, TOOLS, "request_introduction", REQUEST)[1]
    text, is_error = execute_tool(session, TOOLS, "request_introduction", REQUEST)
    assert is_error and "лимит" in text


# --- 8. провайдер в конфиге, ключей в репозитории нет ---------------------------------

def test_provider_and_model_come_from_config():
    cfg = load_config(ROOT / "config/agent.toml")
    assert (cfg.provider, cfg.model) == ("openai_compatible", "qwen/qwen3.8-27b")
    assert (cfg.api_key_env, cfg.base_url_env) == ("API_KEY", "API_LINK")  # имена переменных, не значения
    claude = load_config(ROOT / "config/agent.anthropic.toml")
    assert (claude.provider, claude.model) == ("anthropic", "claude-opus-5-5")
    assert load_config(ROOT / "config/agent.demo.toml").provider == "scripted"


def test_config_rejects_bad_values(tmp_path):
    for body, fragment in [('provider = "gpt"', "provider"), ("max_steps = 0", "max_steps"),
                           ('surprise = 1', "неизвестные"), ('effort = "turbo"', "effort")]:
        path = tmp_path / "c.toml"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match=fragment):
            load_config(path)


def test_changing_model_needs_no_code_change(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('model = "claude-sonnet-5-5"\nfallbacks = false\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.model == "claude-sonnet-5-5" and cfg.fallbacks is False


EIGHT_FIELDS = ["Имя", "Описание", "Аргументы", "Что не от модели", "Результат", "Ошибки", "Тип", "Отказ"]


@pytest.mark.parametrize("name", list(TOOLS))
def test_spec_describes_every_tool_with_eight_fields_matching_the_code(name):
    spec = (ROOT / "docs/SPEC.md").read_text(encoding="utf-8")
    start = spec.index(f"`{name}`\n\n| Поле")
    end = spec.find("\n### ", start)
    card = spec[start: end if end != -1 else spec.index("\n## ", start)]
    for field in EIGHT_FIELDS:
        assert f"| **{field}** |" in card, f"{name}: нет поля «{field}»"
    schema = TOOLS[name].input_schema
    for arg in schema["properties"]:
        assert f"`{arg}`" in card, f"{name}: аргумент {arg} не описан в спецификации"
    for arg in schema["required"]:
        assert re.search(rf"`{arg}`[^;.]*обязательно", card), f"{name}: {arg} не помечен обязательным"


def test_spec_mentions_defaults_that_exist_in_code():
    spec = (ROOT / "docs/SPEC.md").read_text(encoding="utf-8")
    assert "по умолчанию 5" in spec and AgentConfig().max_steps == 8 and "по умолчанию 8" in spec
    assert AgentConfig().max_intro_requests == 3 and "по умолчанию 3" in spec


def test_no_secrets_in_repository_files():
    secret = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|ANTHROPIC_API_KEY\s*=\s*\S{10,}|li_at\s*=\s*\S{20,}|JSESSIONID\s*=\s*\"?ajax:\d{6,}")
    scanned = 0
    for pattern in ("src/**/*.py", "tests/**/*.py", "config/*", "docs/*.md", "README.md", ".env.example",
                    "pyproject.toml", "scripts/*.py", "data/demo/**/*.json"):
        for path in ROOT.glob(pattern):
            if path.is_file() and ".cache" not in path.parts:
                scanned += 1
                assert not secret.search(path.read_text(encoding="utf-8", errors="ignore")), path
    assert scanned > 20


def test_provider_code_reads_key_only_from_environment():
    src = (ROOT / "src/intro_paths/agent/llm.py").read_text(encoding="utf-8")
    assert not re.search(r"""api_key\s*=\s*["']""", src)  # никаких ключей в литералах
    assert "os.environ.get(cfg.api_key_env" in src and "os.environ.get(cfg.base_url_env" in src
    assert not re.search(r"""https?://[a-z0-9.-]+/api""", src)  # адрес API тоже не зашит в код


# --- 9. демо: ошибка инструмента и выход из неё --------------------------------------

def test_demo_scenario_shows_tool_error_and_recovery(tmp_path):
    cfg = replace(load_config(ROOT / "config/agent.demo.toml"), outbox_dir=str(tmp_path / "outbox"))
    events: list[str] = []
    answers: list[str] = []

    def approve(prompt: str) -> bool:
        answers.append(prompt)
        return True

    result = run_from_config(cfg, "Найди пути к Apple Inc", approve, events.append, root=ROOT, today=TODAY)
    assert result.stop_reason == "end_turn" and result.steps == 5
    flags = [(r.name, r.is_error) for r in result.tool_calls]
    assert flags == [("enrich_target", True), ("enrich_target", False),
                     ("find_intro_paths", False), ("request_introduction", False)]
    assert any("таймаут внешнего сервиса" in e for e in events)  # ошибка видна на демо
    assert len(answers) == 1 and (tmp_path / "outbox").exists()  # действие — только после подтверждения
    top = json.loads(result.tool_calls[2].output)["paths"][0]
    assert top["connector_id"] == "demo-john-ternus" and top["strength"] == 0.96


def test_error_demo_fixes_schema_error_and_wrong_connector_id(tmp_path):
    """Пример «ошибка → исправление»: limit=50 нарушает схему, а connector_id передан именем."""
    cfg = replace(load_config(ROOT / "config/agent.demo-errors.toml"), outbox_dir=str(tmp_path / "outbox"))
    events: list[str] = []
    result = run_from_config(cfg, "Найди пути к Contoso Labs", lambda p: True, events.append, root=ROOT, today=TODAY)
    assert result.stop_reason == "end_turn" and result.steps == 6
    assert [(r.name, r.is_error) for r in result.tool_calls] == [
        ("enrich_target", False), ("find_intro_paths", True), ("find_intro_paths", False),
        ("request_introduction", True), ("request_introduction", False)]
    schema_error, id_error = result.tool_calls[1].output, result.tool_calls[3].output
    assert "limit" in schema_error and "maximum of 10" in schema_error
    assert "'Ravi Menon' нет в сети" in id_error and "find_intro_paths" in id_error
    # исправление действительно меняет вызов: 50 -> 10, имя -> идентификатор из результата
    assert result.tool_calls[1].input["limit"] == 50 and result.tool_calls[2].input["limit"] == 10
    assert result.tool_calls[3].input["connector_id"] == "Ravi Menon"
    assert result.tool_calls[4].input["connector_id"] == "demo-ravi-menon"
    top = json.loads(result.tool_calls[2].output)["paths"][0]
    assert top["connector_id"] == "demo-ravi-menon" and top["strength"] == 0.9  # id взят из результата поиска
    # ошибочные вызовы ничего не записали: в очереди ровно одна запись, от исправленного вызова
    assert [p.name for p in (tmp_path / "outbox").glob("*.json")] == ["intro_me_1.json"]


def test_demo_scenario_when_human_declines(tmp_path):
    cfg = replace(load_config(ROOT / "config/agent.demo.toml"), outbox_dir=str(tmp_path / "outbox"))
    result = run_from_config(cfg, "Найди пути к Apple Inc", lambda p: False, lambda e: None, root=ROOT, today=TODAY)
    assert result.tool_calls[-1].is_error and not (tmp_path / "outbox").exists()
    assert result.stop_reason == "end_turn"


def test_system_prompt_forbids_inventing_and_requires_explicit_request():
    assert "Ничего не выдумывай" in SYSTEM_PROMPT and "явно просит" in SYSTEM_PROMPT
    assert "не утверждай, что вы вместе работали" in SYSTEM_PROMPT


def test_assistant_blocks_are_appended_unchanged(tmp_path):
    session = make_session(tmp_path)
    llm = ScriptedLLM([{"text": "t", "tool_calls": [call("a", "find_intro_paths", target="Apple Inc", kind="company")]},
                       {"text": "ok"}])
    run_agent("найди", llm=llm, session=session)
    assistant = llm.seen[1][1]
    assert assistant["role"] == "assistant" and assistant["content"][1]["type"] == "tool_use"
    assert llm.seen[1][2]["role"] == "user" and llm.seen[1][2]["content"][0]["tool_use_id"] == "a"
