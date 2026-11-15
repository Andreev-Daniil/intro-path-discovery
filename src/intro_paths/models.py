"""Доменные модели: люди, аффилиации (работа, вуз, совет директоров) и периоды."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

KINDS = ("position", "education", "board")


@dataclass(frozen=True)
class Period:
    """Период действия аффилиации.

    start=None — дата начала неизвестна.
    end=None   — аффилиация продолжается по настоящее время.
    """

    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class Affiliation:
    kind: str  # position | education | board
    name: str  # название как в источнике
    norm: str  # нормализованное название для сравнения
    period: Period = field(default_factory=Period)
    role: str = ""  # должность / степень / роль в совете

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"неизвестный тип аффилиации: {self.kind!r}")


@dataclass(frozen=True)
class Coauthor:
    """Соавтор работы (из OpenAlex/Crossref)."""

    name: str
    openalex_id: str = ""
    institutions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Work:
    """Научная работа с соавторами."""

    id: str  # OpenAlex id или DOI
    title: str = ""
    year: int | None = None
    doi: str = ""
    url: str = ""
    coauthors: tuple[Coauthor, ...] = ()


@dataclass(frozen=True)
class Insider:
    """Директор/офицер публичной компании по данным SEC EDGAR (формы 3/4/5)."""

    name: str  # в естественном порядке «Имя Фамилия»
    is_director: bool = False
    is_officer: bool = False
    title: str = ""
    first_seen: date | None = None
    last_seen: date | None = None
    source_url: str = ""


@dataclass(frozen=True)
class Interaction:
    """Взаимодействие владельца с контактом (из Messages.csv / Invitations.csv)."""

    kind: str  # message | invite
    when: date | None = None
    count: int = 1


@dataclass
class Person:
    id: str
    first_name: str = ""
    last_name: str = ""
    profile_url: str = ""
    connected_on: date | None = None
    affiliations: list[Affiliation] = field(default_factory=list)
    works: list[Work] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class Network:
    """Сеть пользователя: он сам и его контакты первого круга."""

    owner: Person
    contacts: dict[str, Person] = field(default_factory=dict)


@dataclass
class Target:
    """Цель знакомства: человек или компания, описанные аффилиациями.

    Для компании — одна аффилиация kind=position с названием компании.
    """

    name: str
    affiliations: list[Affiliation] = field(default_factory=list)
    kind: str = ""  # company | person | "" (не задано)
    works: list[Work] = field(default_factory=list)
    insiders: list[Insider] = field(default_factory=list)
    fictional: bool = False  # вымышленная демо-цель: в онлайн-источниках её искать нельзя (совпадёт с реальной)
