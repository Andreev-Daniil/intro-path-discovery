"""Фоновый запуск агента для интерфейса: шаги видны по мере выполнения, подтверждение — кнопками.

Агент работает в потоке; когда `request_introduction` просит подтверждения, поток ждёт ответа
человека (`answer(True/False)`). Без ответа за `confirm_timeout` действие отклоняется.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date

from ..models import Network
from .config import AgentConfig
from .llm import LLM, make_llm
from .loop import AgentResult, make_session_from_network, run_agent


@dataclass
class Event:
    kind: str  # call | ok | error | model | confirm | stop | info
    text: str
    step: int = 0
    at: float = field(default_factory=time.time)


def classify(line: str) -> Event:
    """Разбор строки журнала цикла: '[шаг N] вызов …' / 'ок:' / 'ОШИБКА:' / 'модель:'."""
    step = 0
    body = line
    if line.startswith("[шаг "):
        head, _, body = line.partition("] ")
        try:
            step = int(head.removeprefix("[шаг "))
        except ValueError:
            step = 0
    if body.startswith("вызов "):
        return Event("call", body.removeprefix("вызов "), step)
    if body.startswith("ОШИБКА: "):
        return Event("error", body.removeprefix("ОШИБКА: "), step)
    if body.startswith("ок: "):
        return Event("ok", body.removeprefix("ок: "), step)
    if body.startswith("модель:"):
        return Event("model", body.removeprefix("модель:").strip(), step)
    if line.startswith("[стоп]"):
        return Event("stop", line.removeprefix("[стоп]").strip(), step)
    return Event("info", line, step)


class AgentRunner:
    def __init__(self, cfg: AgentConfig, request: str, network: Network, today: date,
                 llm: LLM | None = None, confirm_timeout: float = 600.0) -> None:
        self.cfg, self.request, self.network, self.today = cfg, request, network, today
        self._llm = llm
        self.confirm_timeout = confirm_timeout
        self.events: list[Event] = []
        self.result: AgentResult | None = None
        self.error: str = ""
        self.pending: str | None = None  # вопрос человеку, пока ждём ответ
        self._answer: bool | None = None
        self._answered = threading.Event()
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True, name="agent-runner")

    # --- жизненный цикл
    def start(self) -> "AgentRunner":
        self.thread.start()
        return self

    @property
    def running(self) -> bool:
        return self.thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)

    # --- подтверждение человеком
    def _confirm(self, prompt: str) -> bool:
        with self._lock:
            self.pending, self._answer = prompt, None
            self._answered.clear()
            self.events.append(Event("confirm", prompt))
        got = self._answered.wait(self.confirm_timeout)
        with self._lock:
            decision = bool(self._answer) if got else False
            self.pending = None
            if not got:
                self.events.append(Event("info", "Время ожидания подтверждения вышло — действие отклонено."))
        return decision

    def answer(self, approve: bool) -> None:
        with self._lock:
            if self.pending is None:
                return
            self._answer = approve
        self._answered.set()

    def wait_pending(self, timeout: float = 10.0) -> bool:
        """Для тестов: дождаться вопроса человеку."""
        end = time.time() + timeout
        while time.time() < end:
            if self.pending is not None:
                return True
            if not self.running:
                return False
            time.sleep(0.02)
        return False

    # --- поток агента
    def _log(self, line: str) -> None:
        if line.strip():
            with self._lock:
                self.events.append(classify(line))

    def _run(self) -> None:
        try:
            llm = self._llm or make_llm(self.cfg)
            session = make_session_from_network(self.cfg, self.network, self._confirm, today=self.today)
            self.result = run_agent(self.request, llm=llm, session=session, on_event=self._log)
        except Exception as exc:  # конфиг, ключ, сеть — показываем в интерфейсе, а не роняем поток молча
            self.error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self.events.append(Event("error", self.error))

    def snapshot(self) -> list[Event]:
        with self._lock:
            return list(self.events)
