"""Сборка внешних источников из переменных окружения / .env.

SEC_USER_AGENT   — обязателен для EDGAR, формат «Имя email» (требование SEC);
OPENALEX_API_KEY — необязателен, повышает дневной лимит;
OPENALEX_MAILTO, CROSSREF_MAILTO — необязательны, «вежливый» пул API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .sources.base import PublicationSource
from .sources.crossref import CrossrefSource
from .sources.edgar import EdgarSource
from .sources.http import HttpClient
from .sources.openalex import OpenAlexSource

DEFAULT_CACHE = Path("data") / ".cache"


@dataclass
class Services:
    edgar: EdgarSource | None = None
    publications: list[PublicationSource] = field(default_factory=list)


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def build_services(
    *,
    use_edgar: bool = True,
    use_openalex: bool = True,
    use_crossref: bool = True,
    env=None,
    cache_dir: Path | None = DEFAULT_CACHE,
) -> Services:
    env = os.environ if env is None else env
    services = Services()
    if use_edgar:
        agent = env.get("SEC_USER_AGENT", "").strip()
        if "@" not in agent:
            raise ValueError(
                "для EDGAR задайте SEC_USER_AGENT в .env, например: "
                "SEC_USER_AGENT=\"Имя Фамилия you@example.com\" (требование SEC)"
            )
        http = HttpClient(user_agent=agent, min_interval=0.12, cache_dir=cache_dir)
        services.edgar = EdgarSource(http)
    mailto_oa = env.get("OPENALEX_MAILTO", "")
    if use_openalex:
        http = HttpClient(user_agent="intro-paths/0.1", min_interval=0.2, cache_dir=cache_dir)
        services.publications.append(
            OpenAlexSource(http, api_key=env.get("OPENALEX_API_KEY", ""), mailto=mailto_oa)
        )
    if use_crossref:
        http = HttpClient(user_agent="intro-paths/0.1", min_interval=0.2, cache_dir=cache_dir)
        services.publications.append(CrossrefSource(http, mailto=env.get("CROSSREF_MAILTO", "")))
    return services
