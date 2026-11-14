"""Чтение файлов экспорта LinkedIn (Get a copy of your data → Download).

Используются: Profile.csv, Connections.csv, Positions.csv, Education.csv.

Экспорт не содержает связей между контактами и не даёт история работы
контактов — только их текущую компанию. Историю можно дополнить через enrichment.csv.

Формат файлов меняется, поэтому:
- заголовок ищется по известному столбцу, а не по номеру строки (в Connections.csv
  есть служебные строки сверху);
- колонки читаются по именам, порядок не важен;
- многострочные поля обрабатываются модулем csv.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .dates import education_end, parse_date
from .models import Affiliation, Period, Person
from .normalize import normalize_org, normalize_text


def read_table(path: Path, marker: str) -> list[dict[str, str]]:
    """Строки таблицы как dict. Заголовок — первая запись, содержащая столбец marker."""
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    with open(path, encoding="utf-8-sig", newline="") as f:
        for raw in csv.reader(f):
            cells = [c.strip() for c in raw]
            if header is None:
                if marker in cells:
                    header = cells
                continue
            if not any(cells):
                continue
            padded = cells + [""] * (len(header) - len(cells))
            rows.append(dict(zip(header, padded)))
    if header is None:
        raise ValueError(f"{path}: не найден заголовок со столбцом {marker!r}")
    return rows


def read_owner(export_dir: Path) -> Person | None:
    path = export_dir / "Profile.csv"
    if not path.exists():
        return None
    rows = read_table(path, "First Name")
    if not rows:
        return None
    return Person(
        id="me",
        first_name=rows[0].get("First Name", ""),
        last_name=rows[0].get("Last Name", ""),
    )


def read_connections(path: Path) -> list[Person]:
    people: list[Person] = []
    for r in read_table(path, "First Name"):
        first, last = r.get("First Name", ""), r.get("Last Name", "")
        if not (first or last):
            continue
        person = Person(
            id="",  # назначит network.build_network
            first_name=first,
            last_name=last,
            profile_url=r.get("URL", ""),
            connected_on=parse_date(r.get("Connected On")),
        )
        company = r.get("Company", "")
        if company:
            # Текущая компания без дат: start неизвестен, end=None означает «сейчас».
            person.affiliations.append(
                Affiliation(
                    kind="position",
                    name=company,
                    norm=normalize_org(company),
                    period=Period(),
                    role=r.get("Position", ""),
                )
            )
        people.append(person)
    return people


def read_positions(path: Path) -> list[Affiliation]:
    result: list[Affiliation] = []
    for r in read_table(path, "Company Name"):
        name = r.get("Company Name", "")
        if not name:
            continue
        result.append(
            Affiliation(
                kind="position",
                name=name,
                norm=normalize_org(name),
                period=Period(
                    parse_date(r.get("Started On")),
                    parse_date(r.get("Finished On"), end=True),  # пусто = работает сейчас
                ),
                role=r.get("Title", ""),
            )
        )
    return result


def read_education(path: Path) -> list[Affiliation]:
    result: list[Affiliation] = []
    for r in read_table(path, "School Name"):
        name = r.get("School Name", "")
        if not name:
            continue
        start = parse_date(r.get("Start Date"))
        end = parse_date(r.get("End Date"), end=True) or education_end(start)
        result.append(
            Affiliation(
                kind="education",
                name=name,
                norm=normalize_org(name),
                period=Period(start, end),
                role=r.get("Degree Name", ""),
            )
        )
    return result
