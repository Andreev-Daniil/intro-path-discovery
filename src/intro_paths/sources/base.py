"""Общий интерфейс источников публикаций.

Сюда же позже встаёт Google Scholar через платный SerpApi — напрямую Scholar
не подключаем: у него нет API, /scholar закрыт в robots.txt и защищён капчей.
"""

from __future__ import annotations

from typing import Protocol

from ..models import Work


class PublicationSource(Protocol):
    def find_works(self, name: str, hints: set[str]) -> list[Work]:
        """Работы человека по имени; hints — нормализованные названия его организаций."""
        ...
