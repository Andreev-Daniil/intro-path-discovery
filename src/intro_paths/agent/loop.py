"""Цикл агента: модель → вызовы инструментов → результаты → модель, с жёстким лимитом шагов."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from ..store import load_network
from .config import AgentConfig
from .llm import LLM, LLMError, make_llm
from .tools import (
    FlakyEnrichment,
    LiveEnrichment,
    Session,
    SnapshotEnrichment,
    build_tools,
    execute_tool,
)

SYSTEM_PROMPT = """Ты помощник по поиску пути знакомства. Пользователь называет цель — компанию или человека.
Твоя задача: найти среди контактов пользователя тех, кто может его представить, и объяснить почему.

Порядок: сначала enrich_target, затем find_intro_paths. Если enrich_target вернул ошибку сервиса —
повтори вызов один раз; если снова ошибка — продолжай без обогащения и скажи об этом пользователю.
Ничего не выдумывай: называй только контакты и сигналы из результатов инструментов. Для каждого
пути укажи силу и сигналы. request_introduction вызывай только если пользователь явно просит
подготовить запрос и только для connector_id из find_intro_paths; если человек отказал, не
повторяй. В тексте запроса на знакомство пиши только то, что подтверждено результатами инструментов:
не утверждай, что вы вместе работали, учились или переписывались, если этого нет в сигналах;
пиши нейтрально и вежливо. Отвечай кратко на языке пользователя."""


@dataclass
class ToolRecord:
    step: int
    name: str
    input: object
    output: str
    is_error: bool


@dataclass
class AgentResult:
    final_text: str
    stop_reason: str  # end_turn | step_limit | max_tokens | refusal | llm_error
    steps: int
    tool_calls: list[ToolRecord] = field(default_factory=list)


def run_agent(
    user_request: str, *, llm: LLM, session: Session, system: str = SYSTEM_PROMPT,
    on_event: Callable[[str], None] = lambda line: None,
) -> AgentResult:
    cfg = session.cfg
    tools = {t.name: t for t in build_tools()}
    specs = [t.spec() for t in tools.values()]
    messages: list[dict] = [{"role": "user", "content": user_request}]
    records: list[ToolRecord] = []

    for step in range(1, cfg.max_steps + 1):
        try:
            response = llm.create(system=system, messages=messages, tools=specs)
        except LLMError as exc:
            on_event(f"[шаг {step}] ошибка модели: {exc}")
            return AgentResult(f"Не удалось получить ответ модели: {exc}", "llm_error", step - 1, records)

        messages.append(llm.assistant_message(response))  # как есть, без правок
        if response.text:
            on_event(f"[шаг {step}] модель: {response.text}")
        if response.stop_reason == "refusal":
            return AgentResult("Модель отказалась выполнять запрос.", "refusal", step, records)
        if response.stop_reason == "max_tokens":
            return AgentResult(response.text or "Ответ обрезан по max_tokens.", "max_tokens", step, records)
        if not response.tool_calls:
            return AgentResult(response.text, "end_turn", step, records)

        results = []
        for call in response.tool_calls:
            on_event(f"[шаг {step}] вызов {call.name}({call.input})")
            text, is_error = execute_tool(session, tools, call.name, call.input)
            records.append(ToolRecord(step, call.name, call.input, text, is_error))
            on_event(f"[шаг {step}] {'ОШИБКА' if is_error else 'ок'}: {text[:300]}")
            results.append((call.id, text, is_error))
        messages.extend(llm.tool_messages(results))  # формат и группировка — по правилам провайдера

    note = (f"Достигнут лимит шагов ({cfg.max_steps}). Остановлено без финального ответа: "
            f"выполнено вызовов инструментов — {len(records)}.")
    on_event(f"[стоп] {note}")
    return AgentResult(note, "step_limit", cfg.max_steps, records)


def make_session_from_network(cfg: AgentConfig, network, confirm: Callable[[str], bool], *,
                              root: Path | None = None, today: date | None = None) -> Session:
    """Сессия по уже загруженной сети (интерфейс): пути из конфига — относительно root."""
    base = root or Path.cwd()
    snapshots = SnapshotEnrichment(base / cfg.targets_dir)
    if cfg.enrichment == "snapshot":
        inner = snapshots
    else:  # онлайн, а при сбое или пустом ответе — запасной офлайн-снимок
        inner = LiveEnrichment(fallback=snapshots if cfg.fallback_to_snapshot else None)
    enrichment = FlakyEnrichment(inner, cfg.simulate_enrichment_failures) if cfg.simulate_enrichment_failures else inner
    return Session(network=network, cfg=cfg, enrichment=enrichment, confirm=confirm,
                   today=today or date.today())


def make_session(cfg: AgentConfig, confirm: Callable[[str], bool], *, root: Path | None = None,
                 today: date | None = None) -> Session:
    base = root or Path.cwd()
    return make_session_from_network(cfg, load_network(base / cfg.network), confirm, root=base, today=today)


def run_from_config(cfg: AgentConfig, request: str, confirm: Callable[[str], bool],
                    on_event: Callable[[str], None] = print, *, root: Path | None = None,
                    today: date | None = None) -> AgentResult:
    base = root or Path.cwd()
    if cfg.provider == "scripted":
        cfg = cfg if Path(cfg.script).is_absolute() else _with_script(cfg, base / cfg.script)
    return run_agent(request, llm=make_llm(cfg), session=make_session(cfg, confirm, root=base, today=today),
                     on_event=on_event)


def _with_script(cfg: AgentConfig, path: Path) -> AgentConfig:
    from dataclasses import replace
    return replace(cfg, script=str(path))
