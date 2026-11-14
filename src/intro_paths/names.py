"""Сопоставление имён людей между источниками (LinkedIn, SEC, OpenAlex).

Совпадение по имени — слабое свидетельство: однофамильцы неизбежны. Поэтому
`match_names` возвращает уверенность, а вызывающий код понижает вес сигнала,
если нет подтверждения из другого источника (общий работодатель, вуз).
"""

from __future__ import annotations

from .normalize import normalize_text

NAME_THRESHOLD = 0.85


def from_sec_name(raw: str) -> str:
    """«SMITH JOHN A» (формат SEC: фамилия первой) -> «John A Smith»."""
    tokens = [t for t in normalize_text(_drop_apostrophes(raw)).split() if t]
    if len(tokens) < 2:
        return " ".join(t.title() for t in tokens)
    family, given = tokens[0], tokens[1:]
    return " ".join(t.title() for t in [*given, family])


def _drop_apostrophes(value: str) -> str:
    """O'Brien и OBrien — одно и то же: апостроф не разделяет слово."""
    return value.replace("'", "").replace("’", "").replace("`", "")


def _tokens(name: str) -> list[str]:
    """Токены в порядке «имя ... фамилия»; «Doe, Jane» переворачивается."""
    name = _drop_apostrophes(name)
    if "," in name:
        family, _, given = name.partition(",")
        name = f"{given} {family}"
    return [t for t in normalize_text(name).replace("-", " ").split() if t]


def match_names(a: str, b: str) -> float:
    """Уверенность 0..1, что два имени — один человек.

    Фамилия (последний токен) должна совпасть. Первое имя: совпадение — 1.0,
    инициал против полного имени — 0.85, различие — 0.
    """
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return 0.0
    if ta[-1] != tb[-1]:
        return 0.0
    ga, gb = ta[0], tb[0]
    if ga == gb:
        return 1.0
    if len(ga) == 1 and gb.startswith(ga):
        return 0.85
    if len(gb) == 1 and ga.startswith(gb):
        return 0.85
    return 0.0


def is_same_person(a: str, b: str) -> bool:
    return match_names(a, b) >= NAME_THRESHOLD
