"""Ручное обогащение: дополнительные аффилиации контактов.

LinkedIn не отдаёт историю работы и советы директоров контактов в экспорте,
поэтому такие данные вводятся отдельным CSV (из своих заметок, рекомендаций,
публичных источников, которые разрешено использовать).

Колонки: person_id,kind,name,start,end,role
- person_id — id контакта (slug из URL профиля, например ivan-petrov-example);
  'me' — сам пользователь;
- kind — position | education | board;
- start / end — 2019-03, 2019, 2019-03-15; пустой end = по настоящее время
  (для education пустой end = эвристика «4 года»).
"""

from __future__ import annotations

import csv
from pathlib import Path

from .dates import education_end, parse_date
from .models import Affiliation, Period
from .normalize import normalize_org


def read_enrichment(path: Path) -> list[tuple[str, Affiliation]]:
    result: list[tuple[str, Affiliation]] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            person_id = (row.get("person_id") or "").strip()
            kind = (row.get("kind") or "").strip().lower()
            name = (row.get("name") or "").strip()
            if not (person_id and kind and name):
                raise ValueError(f"{path}:{line_no}: person_id, kind и name обязательны")
            start = parse_date(row.get("start"))
            end = parse_date(row.get("end"), end=True)
            if kind == "education" and end is None:
                end = education_end(start)
            try:
                aff = Affiliation(
                    kind=kind,
                    name=name,
                    norm=normalize_org(name),
                    period=Period(start, end),
                    role=(row.get("role") or "").strip(),
                )
            except ValueError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            result.append((person_id, aff))
    return result
