"""Провайдеры модели. Цикл агента видит только `LLM.create` -> `LLMResponse`.

- `openai_compatible` — любая модель за OpenAI-совместимым API (routerai.ru, Qwen и т.п):
  адрес и ключ — из переменных окружения, имена которых заданы в конфиге;
- `anthropic` — Claude через официальный SDK (ручной цикл, без beta-раннера);
- `scripted` — детерминированный офлайн-провайдер для демо и тестов (ответы модели из JSON).

Формат сообщений у провайдеров разный, поэтому историю собирает сам провайдер
(`assistant_message`, `tool_messages`). Блоки ответа Claude кладутся в историю как есть:
блоки thinking нельзя править или выбрасывать.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import AgentConfig


class LLMError(RuntimeError):
    """Сбой провайдера: сеть, лимиты, 4xx/5xx. Агент завершает работу аккуратно, а не падает."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass
class LLMResponse:
    content: Any  # кладётся в messages как есть
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal | ...
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


ToolResult = tuple[str, str, bool]  # (tool_call_id, текст, is_error)


class LLM(Protocol):
    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...

    def assistant_message(self, response: LLMResponse) -> dict:
        """Сообщение ассистента для истории."""
        ...

    def tool_messages(self, results: list[ToolResult]) -> list[dict]:
        """Результаты инструментов в формате провайдера (все результаты хода — вместе)."""
        ...


class _ContentBlocksMixin:
    """Формат Anthropic: ассистент — блоки как есть, результаты — один user-ход с tool_result."""

    def assistant_message(self, response: LLMResponse) -> dict:
        return {"role": "assistant", "content": response.content}

    def tool_messages(self, results: list[ToolResult]) -> list[dict]:
        blocks = []
        for call_id, text, is_error in results:
            block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call_id, "content": text}
            if is_error:
                block["is_error"] = True
            blocks.append(block)
        return [{"role": "user", "content": blocks}]


class AnthropicLLM(_ContentBlocksMixin):
    def __init__(self, cfg: AgentConfig) -> None:
        import anthropic

        self._anthropic = anthropic
        self.cfg = cfg
        # ключ берётся из окружения / профиля `ant auth login`, в конфиге и коде его нет
        self.client = anthropic.Anthropic(timeout=cfg.request_timeout_s, max_retries=2)

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        a = self._anthropic
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model, max_tokens=self.cfg.max_tokens, system=system, tools=tools,
            messages=messages, output_config={"effort": self.cfg.effort},
        )
        try:
            if self.cfg.fallbacks:
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.AuthenticationError as exc:
            raise LLMError("нет доступа к API: задайте ANTHROPIC_API_KEY или выполните `ant auth login`") from exc
        except a.RateLimitError as exc:
            raise LLMError("превышен лимит запросов API, повторите позже") from exc
        except a.APITimeoutError as exc:
            raise LLMError("API не ответил вовремя") from exc
        except a.APIConnectionError as exc:
            raise LLMError("нет сети до API") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"ошибка API {exc.status_code}: {exc.message}") from exc
        calls = [ToolCall(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"]
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResponse(resp.content, resp.stop_reason, text, calls)


class OpenAICompatLLM:
    """Модель за OpenAI-совместимым API (chat.completions + tools). Ключ и адрес — из окружения."""

    def __init__(self, cfg: AgentConfig, client: Any = None) -> None:
        import openai

        self._openai = openai
        self.cfg = cfg
        if client is None:
            key = os.environ.get(cfg.api_key_env, "").strip()
            url = os.environ.get(cfg.base_url_env, "").strip()
            if not key or not url:
                raise ValueError(
                    f"задайте {cfg.api_key_env} (ключ) и {cfg.base_url_env} (адрес API) в .env или окружении")
            if not url.startswith("https://"):
                raise ValueError(f"{cfg.base_url_env} должен начинаться с https://")
            client = openai.OpenAI(api_key=key, base_url=url, timeout=cfg.request_timeout_s, max_retries=2)
        self.client = client

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        o = self._openai
        oa_tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        try:
            resp = self.client.chat.completions.create(
                model=self.cfg.model, max_tokens=self.cfg.max_tokens, tools=oa_tools,
                messages=[{"role": "system", "content": system}, *messages])
        except o.AuthenticationError as exc:
            raise LLMError(f"нет доступа к API: проверьте {self.cfg.api_key_env}") from exc
        except o.RateLimitError as exc:
            raise LLMError("превышен лимит запросов API, повторите позже") from exc
        except o.APITimeoutError as exc:
            raise LLMError("API не ответил вовремя") from exc
        except o.APIConnectionError as exc:
            raise LLMError("нет сети до API") from exc
        except o.APIStatusError as exc:
            raise LLMError(f"ошибка API {exc.status_code}: {str(exc)[:300]}") from exc
        if not resp.choices:
            raise LLMError("API вернул пустой ответ")
        choice = resp.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        raw_calls: list[dict] = []
        for tc in message.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args: Any = json.loads(raw)
            except json.JSONDecodeError:
                args = raw  # невалидный JSON дойдёт до проверки схемы и вернётся модели как ошибка
            calls.append(ToolCall(tc.id, tc.function.name, args))
            raw_calls.append({"id": tc.id, "type": "function",
                              "function": {"name": tc.function.name, "arguments": raw}})
        text = message.content or ""
        content: dict[str, Any] = {"role": "assistant", "content": text}
        if raw_calls:
            content["tool_calls"] = raw_calls
        reason = {"length": "max_tokens", "content_filter": "refusal"}.get(choice.finish_reason or "", "end_turn")
        return LLMResponse(content, "tool_use" if calls else reason, text, calls)

    def assistant_message(self, response: LLMResponse) -> dict:
        return response.content

    def tool_messages(self, results: list[ToolResult]) -> list[dict]:
        # у OpenAI нет is_error: ошибку помечаем в тексте, чтобы модель отличила её от результата
        return [{"role": "tool", "tool_call_id": call_id,
                 "content": f"ОШИБКА ИНСТРУМЕНТА: {text}" if is_error else text}
                for call_id, text, is_error in results]


class ScriptedLLM(_ContentBlocksMixin):
    """Воспроизводит заранее записанные ответы модели: шаг за шагом, без сети."""

    def __init__(self, steps: list[dict]) -> None:
        self.steps = steps
        self.i = 0
        self.seen: list[list[dict]] = []  # снимки messages — для проверок в тестах

    @classmethod
    def from_file(cls, path: str | Path) -> "ScriptedLLM":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        if self.i >= len(self.steps):
            raise LLMError("сценарий закончился, а агент продолжает вызывать модель")
        step = self.steps[self.i]
        self.i += 1
        blocks: list[dict] = []
        if step.get("text"):
            blocks.append({"type": "text", "text": step["text"]})
        calls = []
        for c in step.get("tool_calls", []):
            blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]})
            calls.append(ToolCall(c["id"], c["name"], c["input"]))
        return LLMResponse(blocks, "tool_use" if calls else "end_turn", step.get("text", ""), calls)


def make_llm(cfg: AgentConfig) -> LLM:
    if cfg.provider == "openai_compatible":
        return OpenAICompatLLM(cfg)
    if cfg.provider == "anthropic":
        return AnthropicLLM(cfg)
    if cfg.provider == "scripted":
        return ScriptedLLM.from_file(cfg.script)
    raise ValueError(f"неизвестный provider {cfg.provider!r}")
