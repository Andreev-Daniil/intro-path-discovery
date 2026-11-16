"""Три инструмента агента, их контракты и безопасное исполнение.

Принципы:
- схема аргументов — JSON Schema с `additionalProperties: false`; невалидный вызов не исполняется,
  а возвращается модели как `is_error` с понятным текстом;
- владелец, его контакты и исходящая очередь берутся из `Session`, а не от модели:
  в схемах нет ни owner_id, ни пути к данным, лишние поля отклоняются;
- вызов инструмента ограничен таймаутом, исключение любого типа превращается в ошибку для модели;
- результат ограничен по размеру (`result_max_chars`);
- опасное действие (`request_introduction`) выполняется только после подтверждения человеком.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Protocol

from jsonschema import Draft202012Validator

from ..graph import find_chain_paths
from ..models import Affiliation, Network, Period, Target
from ..normalize import normalize_org
from ..paths import find_intro_paths
from ..sources.http import HttpError
from ..store import load_target
from .config import AgentConfig


class ToolError(Exception):
    """Ожидаемая ошибка инструмента: текст возвращается модели, чтобы она могла выбрать другой путь."""


class ServiceTimeout(ToolError):
    """Внешний сервис не ответил вовремя (отличается от превышения лимита самого вызова)."""


# --------------------------------------------------------------------- обогащение цели

class EnrichmentProvider(Protocol):
    def enrich(self, name: str, kind: str, ticker: str | None) -> Target: ...


class SnapshotEnrichment:
    """Офлайн-провайдер: готовые снимки целей (SEC EDGAR / OpenAlex) из папки."""

    def __init__(self, targets_dir: str | Path) -> None:
        self.dir = Path(targets_dir)

    def enrich(self, name: str, kind: str, ticker: str | None) -> Target:
        want = normalize_org(name)
        for path in sorted(self.dir.glob("*.json")):
            target = load_target(path)
            have = normalize_org(target.name)
            if want and (want == have or want in have or have in want):
                return target
        raise ToolError(f"для «{name}» нет данных обогащения (снимков: "
                        f"{len(list(self.dir.glob('*.json')))}). Продолжайте без обогащения.")


class LiveEnrichment:
    """Онлайн: SEC EDGAR для компаний и OpenAlex/Crossref для людей; ключи — из окружения.

    Если сервис недоступен или цели в нём нет, берётся запасной офлайн-снимок (если задан).
    Без запасного варианта сбой возвращается модели как ошибка инструмента.
    """

    def __init__(self, fallback: "SnapshotEnrichment | None" = None) -> None:
        self.fallback = fallback

    def enrich(self, name: str, kind: str, ticker: str | None) -> Target:
        from ..online import LIVE_ERRORS, live_enrich, merge_affiliations

        target = Target(name=name, kind=kind)
        problem: Exception | None = None
        snap: Target | None = None
        if self.fallback is not None:
            try:
                snap = self.fallback.enrich(name, kind, ticker)
            except ToolError:
                snap = None
        if snap is not None and snap.fictional:  # вымышленная демо-цель: онлайн не запрашиваем
            return snap
        try:
            report = live_enrich(target, ticker)
            if report["found"] and (report["insiders"] or report["works"]):
                if snap is not None:
                    merge_affiliations(target, snap)
                return target
        except LIVE_ERRORS as exc:
            problem = exc
        if snap is not None:  # онлайн не дал данных — запасной офлайн-снимок
            return snap
        if isinstance(problem, TimeoutError):
            raise problem  # покажется модели как «таймаут внешнего сервиса»
        if problem is not None:
            raise ToolError(f"онлайн-источник недоступен: {problem}") from problem
        raise ToolError(f"для «{name}» нет данных обогащения. Продолжайте без обогащения.")


class FlakyEnrichment:
    """Для демо и тестов: первые N вызовов завершаются таймаутом сервиса."""

    def __init__(self, inner: EnrichmentProvider, failures: int) -> None:
        self.inner, self.left = inner, failures

    def enrich(self, name: str, kind: str, ticker: str | None) -> Target:
        if self.left > 0:
            self.left -= 1
            raise TimeoutError("сервис обогащения (SEC EDGAR) не ответил за отведённое время")
        return self.inner.enrich(name, kind, ticker)


# ------------------------------------------------------------------------------ сессия

@dataclass
class Session:
    """Всё, что принадлежит пользователю и не должно приходить от модели."""

    network: Network
    cfg: AgentConfig
    enrichment: EnrichmentProvider
    confirm: Callable[[str], bool] = lambda prompt: False  # по умолчанию — отказ
    today: date = field(default_factory=date.today)
    targets: dict[str, Target] = field(default_factory=dict)
    intro_requests: list[dict] = field(default_factory=list)
    declined: set[str] = field(default_factory=set)  # действия, которые человек уже отклонил в этом запросе

    @property
    def owner_id(self) -> str:
        return self.network.owner.id


def _base_target(name: str, kind: str) -> Target:
    target = Target(name=name, kind=kind)
    if kind == "company":
        target.affiliations.append(Affiliation("position", name, normalize_org(name), Period(), ""))
    return target


# ---------------------------------------------------------------------------- инструменты

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[Session, dict], dict]
    needs_confirmation: Callable[[Session, dict], str] | None = None  # текст вопроса человеку

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _enrich_target(session: Session, args: dict) -> dict:
    name, kind = args["target"], args["kind"]
    target = session.enrichment.enrich(name, kind, args.get("ticker"))
    session.targets[normalize_org(name)] = target
    return {"target": target.name, "kind": target.kind or kind, "insiders_found": len(target.insiders),
            "works_found": len(target.works),
            "next": "вызовите find_intro_paths, чтобы увидеть, кто из контактов связан с целью"}


def _find_intro_paths(session: Session, args: dict) -> dict:
    name, kind = args["target"], args["kind"]
    limit = args.get("limit", 5)
    target = session.targets.get(normalize_org(name))
    enriched = target is not None
    target = target or _base_target(name, kind)
    paths = find_intro_paths(session.network, target, today=session.today, limit=limit)
    out: dict[str, Any] = {
        "target": target.name, "enriched": enriched, "found": len(paths),
        "paths": [
            {"rank": i, "connector_id": p.connector.id, "name": p.connector.full_name,
             "strength": p.strength,
             "signals": [{"code": s.code, "detail": s.detail, "weight": s.weight,
                          "source": s.evidence_url} for s in p.signals if s.weight > 0]}
            for i, p in enumerate(paths, start=1)
        ],
    }
    if args.get("include_chains"):
        out["through_acquaintance"] = [
            {"via_id": c.via.id, "via_name": c.via.full_name, "bridge": c.bridge_name,
             "bridge_role": c.bridge_role, "strength": c.strength}
            for c in find_chain_paths(session.network, target, today=session.today, limit=limit)
        ]
    if not enriched:
        out["hint"] = "цель не обогащена: вызовите enrich_target, чтобы учесть директоров SEC и соавторов"
    return out


def _check_intro_args(session: Session, args: dict) -> Any:
    contact = session.network.contacts.get(args["connector_id"])
    if contact is None:
        raise ToolError(f"контакта {args['connector_id']!r} нет в сети пользователя; "
                        "берите connector_id только из результата find_intro_paths")
    if len(session.intro_requests) >= session.cfg.max_intro_requests:
        raise ToolError(f"лимит запросов на знакомство за сессию исчерпан ({session.cfg.max_intro_requests})")
    return contact


def _intro_prompt(session: Session, args: dict) -> str:
    contact = _check_intro_args(session, args)
    return (f"Поставить в очередь запрос на знакомство через {contact.full_name} "
            f"к цели «{args['target']}»?\n  Текст: {args['message']}")


def _request_introduction(session: Session, args: dict) -> dict:
    contact = _check_intro_args(session, args)
    outbox = Path(session.cfg.outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    number = len(session.intro_requests) + 1
    record = {"owner": session.owner_id, "connector_id": contact.id, "connector": contact.full_name,
              "target": args["target"], "message": args["message"], "status": "queued_for_manual_send"}
    path = outbox / f"intro_{session.owner_id}_{number}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    session.intro_requests.append(record)
    return {"queued": True, "file": str(path),
            "note": "Сообщение НЕ отправлено: это черновик в очереди, человек отправляет его сам."}


def build_tools() -> list[Tool]:
    target = {"type": "string", "minLength": 1, "maxLength": 120,
              "description": "Название компании или имя человека, как в запросе пользователя."}
    kind = {"type": "string", "enum": ["company", "person"],
            "description": "company — компания, person — человек."}
    return [
        Tool(
            name="enrich_target",
            description=(
                "Подгружает из внешних источников данные о цели: для компании — директоров и "
                "руководителей из SEC EDGAR (только компании США), для человека — его научные "
                "работы и соавторов из OpenAlex. Вызывайте ОДИН раз в начале, до find_intro_paths. "
                "Не обращается к контактам пользователя и ничего не отправляет. Возвращает число "
                "найденных инсайдеров и работ. Ошибки: сервис не ответил (таймаут) — повторите "
                "вызов один раз или продолжайте без обогащения; данных о цели нет — продолжайте без них."),
            input_schema={
                "type": "object", "additionalProperties": False, "required": ["target", "kind"],
                "properties": {
                    "target": target, "kind": kind,
                    "ticker": {"type": "string", "pattern": "^[A-Za-z.]{1,6}$",
                               "description": "Биржевой тикер компании, если известен (например AAPL). Необязательно."},
                }},
            handler=_enrich_target),
        Tool(
            name="find_intro_paths",
            description=(
                "Ищет среди КОНТАКТОВ ПОЛЬЗОВАТЕЛЯ людей, которые могут представить его цели, и "
                "возвращает ранжированный список. Для каждого: connector_id, имя, сила пути от 0 до 1 "
                "и сигналы (общий работодатель, вуз, совет директоров, директор/офицер SEC, "
                "совместные статьи, переписка). Контакты и владелец берутся из сессии — не передавайте "
                "их. Только читает данные, ничего не отправляет. include_chains=true добавляет пути "
                "«через знакомого знакомого» (оценка, не подтверждение). Ошибок кроме неверных "
                "аргументов нет; пустой список значит, что связей не найдено."),
            input_schema={
                "type": "object", "additionalProperties": False, "required": ["target", "kind"],
                "properties": {
                    "target": target, "kind": kind,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10,
                              "description": "Сколько путей вернуть, по умолчанию 5."},
                    "include_chains": {"type": "boolean", "description": "Добавить цепочки через знакомых."},
                }},
            handler=_find_intro_paths),
        Tool(
            name="request_introduction",
            description=(
                "ДЕЙСТВИЕ: ставит в очередь запрос на знакомство через выбранный контакт. Выполняется "
                "только после подтверждения человеком; вызывайте, когда пользователь явно просит "
                "подготовить запрос, и только для connector_id из результата find_intro_paths. "
                "Сообщение автоматически не отправляется — сохраняется черновик для ручной отправки. "
                "Если человек отказался, действие не выполнено: не повторяйте вызов без нового указания. "
                "Ошибки: контакта нет в сети; лимит запросов за сессию исчерпан; человек отказал."),
            input_schema={
                "type": "object", "additionalProperties": False,
                "required": ["connector_id", "target", "message"],
                "properties": {
                    "connector_id": {"type": "string", "minLength": 1, "maxLength": 100,
                                     "description": "Идентификатор контакта из find_intro_paths."},
                    "target": target,
                    "message": {"type": "string", "minLength": 10, "maxLength": 600,
                                "description": "Короткий текст просьбы о знакомстве от первого лица."},
                }},
            handler=_request_introduction, needs_confirmation=_intro_prompt),
    ]


# -------------------------------------------------------------------------- исполнение

def limit_result(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    note = f"\n…[результат обрезан: показано {max_chars} из {len(text)} символов]"
    return text[: max_chars - len(note)] + note


def _run_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    """Выполнить fn с таймаутом в демоническом потоке.

    Зависший вызов нельзя убить, но поток-демон не мешает процессу завершиться
    (в отличие от ThreadPoolExecutor, который ждёт свои потоки при выходе).
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # передаём вызывающему, не теряем
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True, name="tool-call")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise FutureTimeout()  # вызов не уложился в таймаут
    if "error" in box:
        raise box["error"]
    return box["value"]


def validate_input(tool: Tool, args: Any) -> str | None:
    if not isinstance(args, dict):
        return "аргументы должны быть JSON-объектом"
    errors = sorted(Draft202012Validator(tool.input_schema).iter_errors(args), key=lambda e: list(e.path))
    if not errors:
        return None
    parts = [f"{'.'.join(map(str, e.path)) or 'аргументы'}: {e.message}" for e in errors[:5]]
    return "неверные аргументы: " + "; ".join(parts)


def execute_tool(session: Session, tools: dict[str, Tool], name: str, args: Any) -> tuple[str, bool]:
    """Исполняет вызов модели. Возвращает (текст для tool_result, is_error). Никогда не бросает."""
    cfg = session.cfg
    tool = tools.get(name)
    if tool is None:
        return f"неизвестный инструмент {name!r}; доступны: {', '.join(tools)}", True
    problem = validate_input(tool, args)
    if problem:
        return problem, True
    try:
        if tool.needs_confirmation:
            if tool.name in session.declined:
                # Запрет повтора держится в коде, а не только в промпте: человека не спрашиваем второй раз.
                return ("Человек уже отклонил это действие в текущем запросе. Повтор запрещён: "
                        "дождитесь нового указания пользователя."), True
            question = tool.needs_confirmation(session, args)  # проверки и текст вопроса
            if not session.confirm(question):  # человек, вне таймаута инструмента
                session.declined.add(tool.name)
                return ("Человек отклонил действие. Ничего не сделано. "
                        "Не повторяйте без нового указания пользователя."), True
        def guarded() -> dict:
            try:
                return tool.handler(session, args)
            except TimeoutError as exc:  # в Python 3.11+ это тот же класс, что и таймаут ожидания
                raise ServiceTimeout(str(exc)) from exc

        result = _run_with_timeout(guarded, cfg.tool_timeout_s)
    except ServiceTimeout as exc:
        return f"таймаут внешнего сервиса: {exc}. Повторите вызов один раз или продолжайте без него.", True
    except FutureTimeout:
        return f"инструмент {name} не уложился в {cfg.tool_timeout_s:g} с (таймаут). Повторите позже или продолжайте без него.", True
    except (ToolError, HttpError) as exc:
        return str(exc), True
    except Exception as exc:  # любой сбой — ошибка для модели, а не падение агента
        return f"внутренняя ошибка инструмента ({type(exc).__name__}): {exc}", True
    return limit_result(json.dumps(result, ensure_ascii=False), cfg.result_max_chars), False
