import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import openai
import pytest

from intro_paths.agent.config import AgentConfig
from intro_paths.agent.llm import OpenAICompatLLM, make_llm
from intro_paths.agent.loop import run_agent
from intro_paths.agent.tools import Session, SnapshotEnrichment
from intro_paths.store import load_network

ROOT = Path(__file__).resolve().parents[1]
CFG = AgentConfig(provider="openai_compatible", model="test-model")


def completion(content=None, calls=(), finish="stop"):
    tool_calls = [NS(id=i, type="function", function=NS(name=n, arguments=a)) for i, n, a in calls] or None
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls), finish_reason=finish)])


class FakeClient:
    """Подмена openai.OpenAI: отдаёт заготовленные ответы и запоминает запросы."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def session(tmp_path):
    cfg = replace(CFG, outbox_dir=str(tmp_path / "outbox"))
    return Session(load_network(ROOT / "data/demo/network.json"), cfg,
                   SnapshotEnrichment(ROOT / "data/demo/targets"), lambda p: False, date(2026, 11, 15))


def test_tools_are_sent_in_openai_format_with_system_prompt(tmp_path):
    client = FakeClient([completion("привет")])
    result = run_agent("привет", llm=OpenAICompatLLM(CFG, client), session=session(tmp_path))
    req = client.requests[0]
    assert req["model"] == "test-model" and req["messages"][0]["role"] == "system"
    names = {t["function"]["name"] for t in req["tools"]}
    assert names == {"enrich_target", "find_intro_paths", "request_introduction"}
    assert req["tools"][0]["function"]["parameters"]["additionalProperties"] is False
    assert result.stop_reason == "end_turn" and result.final_text == "привет"


def test_tool_roundtrip_uses_tool_role_and_marks_errors(tmp_path):
    bad = json.dumps({"target": "Apple Inc", "kind": "company", "limit": 999})
    good = json.dumps({"target": "Apple Inc", "kind": "company", "limit": 2})
    client = FakeClient([
        completion("", [("call_1", "find_intro_paths", bad)], "tool_calls"),
        completion("", [("call_2", "find_intro_paths", good)], "tool_calls"),
        completion("итог"),
    ])
    result = run_agent("найди", llm=OpenAICompatLLM(CFG, client), session=session(tmp_path))
    assert [r.is_error for r in result.tool_calls] == [True, False] and result.stop_reason == "end_turn"
    second = client.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "call_1"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    assert second[-1]["content"].startswith("ОШИБКА ИНСТРУМЕНТА: неверные аргументы") and "limit" in second[-1]["content"]
    assert not client.requests[2]["messages"][-1]["content"].startswith("ОШИБКА")


def test_invalid_json_arguments_become_tool_error(tmp_path):
    client = FakeClient([completion("", [("c", "find_intro_paths", "{не json")], "tool_calls"), completion("ок")])
    result = run_agent("найди", llm=OpenAICompatLLM(CFG, client), session=session(tmp_path))
    assert result.tool_calls[0].is_error and "JSON-объектом" in result.tool_calls[0].output
    assert result.stop_reason == "end_turn"


def test_finish_reasons_are_mapped(tmp_path):
    llm = OpenAICompatLLM(CFG, FakeClient([completion("обрезано", finish="length")]))
    assert run_agent("x", llm=llm, session=session(tmp_path)).stop_reason == "max_tokens"
    llm = OpenAICompatLLM(CFG, FakeClient([completion("", finish="content_filter")]))
    assert run_agent("x", llm=llm, session=session(tmp_path)).stop_reason == "refusal"


def _status_error(cls, code):
    return cls("boom", response=NS(status_code=code, headers={}, request=None), body=None)


@pytest.mark.parametrize("error,fragment", [
    (_status_error(openai.AuthenticationError, 401), "нет доступа"),
    (_status_error(openai.RateLimitError, 429), "лимит запросов"),
    (openai.APITimeoutError(request=NS()), "не ответил вовремя"),
    (openai.APIConnectionError(request=NS()), "нет сети"),
    (_status_error(openai.InternalServerError, 500), "ошибка API 500"),
])
def test_api_failures_end_the_run_gracefully(tmp_path, error, fragment):
    result = run_agent("привет", llm=OpenAICompatLLM(CFG, FakeClient([error])), session=session(tmp_path))
    assert result.stop_reason == "llm_error" and fragment in result.final_text


def test_missing_key_or_link_is_a_clear_config_error(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_LINK", raising=False)
    with pytest.raises(ValueError, match="API_KEY"):
        make_llm(CFG)
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("API_LINK", "http://insecure.example/api")
    with pytest.raises(ValueError, match="https"):
        make_llm(CFG)


def test_client_is_built_from_env_names_in_config(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret-value")
    monkeypatch.setenv("MY_URL", "https://router.example/api/v1")
    llm = make_llm(replace(CFG, api_key_env="MY_KEY", base_url_env="MY_URL"))
    assert str(llm.client.base_url).startswith("https://router.example/api/v1")
    assert llm.client.api_key == "secret-value"
