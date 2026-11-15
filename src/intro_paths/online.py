"""Онлайн-режим: живые SEC EDGAR / OpenAlex / Crossref, а офлайн-снимок — только запасной вариант.

Онлайн включён всегда. Если сервис недоступен, не задан ключ или цель в нём не найдена (например,
вымышленная компания демо-набора), автоматически берётся готовый снимок — и это явно сообщается.
Переменная `INTRO_PATHS_OFFLINE=1` принудительно отключает сеть (так работают тесты).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import requests

from .config import build_services, load_env
from .models import Target
from .sources.http import HttpError
from .target_enrich import enrich_company, enrich_person

OFFLINE_ENV = "INTRO_PATHS_OFFLINE"
LIVE_ERRORS = (HttpError, ValueError, requests.RequestException, TimeoutError, OSError)


def forced_offline() -> bool:
    return bool(os.environ.get(OFFLINE_ENV))


def live_enrich(target: Target, ticker: str | None = None, *, services=None,
                progress: Callable[[str], None] = lambda m: None) -> dict:
    """Загрузить данные цели из живых источников. Бросает исключение при сбое. Возвращает отчёт."""
    if forced_offline():
        raise ValueError(f"сеть отключена ({OFFLINE_ENV})")
    load_env()
    if target.kind == "company":
        services = services or build_services(use_openalex=False, use_crossref=False)
        report = enrich_company(target, services.edgar, query=ticker or target.name, progress=progress)
        return {"source": "SEC EDGAR", "insiders": report["insiders"], "works": 0,
                "found": report["company"] is not None}
    services = services or build_services(use_edgar=False)
    report = enrich_person(target, services.publications, progress=progress)
    return {"source": report.get("source") or "OpenAlex/Crossref", "insiders": 0, "works": report["works"],
            "found": bool(report["works"])}


def enrich_online(
    target: Target, ticker: str | None = None, *, services=None, snapshot_dir: Path | None = None,
    progress: Callable[[str], None] = lambda m: None,
) -> tuple[Target, list[str]]:
    """Онлайн, а при сбое или пустом результате — запасной офлайн-снимок. Возвращает (цель, заметки)."""
    from .agent.tools import SnapshotEnrichment, ToolError

    notes: list[str] = []
    snap: Target | None = None
    if snapshot_dir is not None:
        try:
            snap = SnapshotEnrichment(snapshot_dir).enrich(target.name, target.kind, ticker)
        except ToolError:
            snap = None
    if snap is not None and snap.fictional:  # вымышленная демо-цель: искать её в сети нельзя
        return snap, ["Вымышленная демо-цель: онлайн-источники не запрашиваются, данные из демо-набора"]
    live_ok = False
    try:
        report = live_enrich(target, ticker, services=services, progress=progress)
        live_ok = bool(report["found"] and (report["insiders"] or report["works"]))
        if live_ok:
            notes.append(f"Онлайн: {report['source']} — инсайдеров {report['insiders']}, работ {report['works']}")
            if snap is not None and merge_affiliations(target, snap):
                notes.append("Места работы и учёбы цели дополнены из офлайн-снимка (онлайн-источник их не отдаёт)")
        else:
            notes.append(f"Онлайн: {report['source']} ответил, но данных о цели нет")
    except LIVE_ERRORS as exc:
        notes.append(f"Онлайн-источник недоступен: {exc}")
    if not live_ok:
        if snap is not None:
            notes.append(f"Запасной офлайн-снимок: инсайдеров {len(snap.insiders)}, работ {len(snap.works)}")
            return snap, notes
        if snapshot_dir is not None:
            notes.append("Офлайн-снимка для этой цели нет: ищу по месту работы и вузу")
    return target, notes


def merge_affiliations(target: Target, snapshot: Target) -> bool:
    """Добавить цели аффилиации из снимка, которых в онлайн-данных нет (по виду и названию)."""
    have = {(a.kind, a.norm) for a in target.affiliations}
    added = [a for a in snapshot.affiliations if (a.kind, a.norm) not in have]
    target.affiliations.extend(added)
    return bool(added)


# ----------------------------------------------------------------------- статус сервисов

def _default_get(url: str, headers: dict | None = None, timeout: float = 8.0) -> Any:
    response = requests.get(url, headers=headers or {}, timeout=timeout, stream=True)
    response.close()  # тело не читаем: нужен только код ответа
    return response


def check_online(env: dict | None = None, get: Callable[..., Any] = _default_get) -> list[dict]:
    """Доступность онлайн-сервисов. Значения ключей не возвращаются — только «задан / нет»."""
    env = os.environ if env is None else env
    from .agent.config import AgentConfig

    cfg = AgentConfig()
    key = (env.get(cfg.api_key_env) or "").strip()
    link = (env.get(cfg.base_url_env) or "").strip().rstrip("/")
    sec_agent = (env.get("SEC_USER_AGENT") or "").strip()
    out: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        out.append({"name": name, "ok": ok, "detail": detail})

    def probe(url: str, headers: dict | None = None) -> tuple[bool, str]:
        try:
            status = get(url, headers=headers).status_code
        except LIVE_ERRORS as exc:
            return False, f"нет связи: {type(exc).__name__}"
        if status in (401, 403):
            return False, f"доступ отклонён ({status}) — проверьте ключ"
        return (status < 500), f"ответ {status}"

    if not key or not link:
        add("Модель (OpenAI-совместимый API)", False,
            f"не заданы {cfg.api_key_env} и/или {cfg.base_url_env} в .env")
    elif not link.startswith("https://"):
        add("Модель (OpenAI-совместимый API)", False, f"{cfg.base_url_env} должен начинаться с https://")
    else:
        ok, detail = probe(f"{link}/models", {"Authorization": f"Bearer {key}"})
        add("Модель (OpenAI-совместимый API)", ok or "404" in detail, f"ключ и адрес заданы; {detail}")
    if not sec_agent or "@" not in sec_agent:
        add("SEC EDGAR", False, "не задан SEC_USER_AGENT («Имя Фамилия email») в .env")
    else:
        ok, detail = probe("https://data.sec.gov/submissions/CIK0000320193.json", {"User-Agent": sec_agent})
        add("SEC EDGAR", ok, detail)
    ok, detail = probe("https://api.openalex.org/authors?search=test&per_page=1")
    add("OpenAlex", ok, detail)
    ok, detail = probe("https://api.crossref.org/works?rows=0")
    add("Crossref", ok, detail)
    return out
