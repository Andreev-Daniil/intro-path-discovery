"""Конфиг агента (TOML). Провайдер и модель меняются здесь, код не трогаем.

Секретов в конфиге нет: ключ Anthropic берётся из окружения (`ANTHROPIC_API_KEY`) или из
профиля `ant auth login`; SEC_USER_AGENT — из `.env`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

PROVIDERS = ("openai_compatible", "anthropic", "scripted")
ENRICHMENT = ("snapshot", "live")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class AgentConfig:
    provider: str = "openai_compatible"
    model: str = "qwen/qwen3.8-27b"
    api_key_env: str = "API_KEY"  # ИМЯ переменной окружения с ключом (сам ключ в конфиг не пишем)
    base_url_env: str = "API_LINK"  # имя переменной с адресом API, например https://routerai.ru/api/v1
    effort: str = "medium"
    fallbacks: bool = True  # серверный запасной маршрут при отказе классификатора безопасности
    max_tokens: int = 16000
    request_timeout_s: float = 120.0
    max_steps: int = 8  # лимит шагов цикла «модель → инструменты»
    tool_timeout_s: float = 20.0  # таймаут одного вызова инструмента
    result_max_chars: int = 6000  # ограничение размера результата инструмента
    max_intro_requests: int = 3  # сколько запросов на знакомство можно поставить за сессию
    network: str = "data/demo/network.json"
    targets_dir: str = "data/demo/targets"
    outbox_dir: str = "data/outbox"
    enrichment: str = "live"  # live — онлайн (SEC EDGAR/OpenAlex/Crossref); snapshot — только офлайн-снимки
    fallback_to_snapshot: bool = True  # при сбое онлайн-источника или пустом ответе взять офлайн-снимок
    simulate_enrichment_failures: int = 0  # для демо: первые N вызовов обогащения падают по таймауту
    script: str = ""  # для provider = "scripted": JSON со сценарием ответов модели

    def validate(self) -> "AgentConfig":
        if self.provider not in PROVIDERS:
            raise ValueError(f"provider {self.provider!r}: допустимо {', '.join(PROVIDERS)}")
        if self.enrichment not in ENRICHMENT:
            raise ValueError(f"enrichment {self.enrichment!r}: допустимо {', '.join(ENRICHMENT)}")
        if self.effort not in EFFORTS:
            raise ValueError(f"effort {self.effort!r}: допустимо {', '.join(EFFORTS)}")
        if not 1 <= self.max_steps <= 50:
            raise ValueError("max_steps должен быть от 1 до 50")
        if self.tool_timeout_s <= 0 or self.request_timeout_s <= 0:
            raise ValueError("таймауты должны быть положительными")
        if self.result_max_chars < 200:
            raise ValueError("result_max_chars слишком мал (минимум 200)")
        if self.provider == "scripted" and not self.script:
            raise ValueError("для provider = scripted нужен путь script")
        return self


def load_config(path: str | Path | None) -> AgentConfig:
    if path is None:
        return AgentConfig().validate()
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    known = {f.name for f in fields(AgentConfig)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{path}: неизвестные ключи конфига: {', '.join(sorted(unknown))}")
    return AgentConfig(**data).validate()
