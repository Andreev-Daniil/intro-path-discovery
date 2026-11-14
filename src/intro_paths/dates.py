"""Разбор дат и проверка пересечения периодов."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

from .models import Period

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def _month(name: str) -> int | None:
    return _MONTHS.get(name[:3].lower())


def _month_edge(year: int, month: int, end: bool) -> date:
    day = monthrange(year, month)[1] if end else 1
    return date(year, month, day)


def parse_date(text: str | None, *, end: bool = False) -> date | None:
    """Разбирает даты экспорта LinkedIn и ISO-подобные строки.

    Форматы: '15 Nov 2026', 'Nov 2026', '2026-11', '2026-11-15', '2026'.
    Неполная дата конца периода расширяется до последнего дня (end=True).
    Непонятная строка даёт None, а не исключение.
    """
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    try:
        m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,})\.?\s+(\d{4})", t)
        if m:
            month = _month(m[2])
            return date(int(m[3]), month, int(m[1])) if month else None
        m = re.fullmatch(r"([A-Za-z]{3,})\.?\s+(\d{4})", t)
        if m:
            month = _month(m[1])
            return _month_edge(int(m[2]), month, end) if month else None
        m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", t)
        if m:
            year, month = int(m[1]), int(m[2])
            if m[3]:
                return date(year, month, int(m[3]))
            return _month_edge(year, month, end)
        m = re.fullmatch(r"(\d{4})", t)
        if m:
            year = int(m[1])
            return date(year, 12, 31) if end else date(year, 1, 1)
    except ValueError:
        return None
    return None


def education_end(start: date | None) -> date | None:
    """Конец учёбы, если в источнике его нет: эвристика «4 года» (конец 4-го года)."""
    if start is None:
        return None
    return date(start.year + 4, 12, 31)


def overlap_status(a: Period, b: Period, today: date) -> str:
    """Пересекаются ли два периода.

    yes     — оба продолжаются сейчас, либо даты известны и интервалы пересекаются
    unknown — у одного из периодов не известна дата начала
    no      — интервалы не пересекаются
    """
    if a.end is None and b.end is None:
        return "yes"
    if a.start is None or b.start is None:
        return "unknown"
    a_end = a.end or today
    b_end = b.end or today
    return "yes" if max(a.start, b.start) <= min(a_end, b_end) else "no"
