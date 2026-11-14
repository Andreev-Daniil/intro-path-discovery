"""Взаимодействия владельца с контактами из экспорта: Messages.csv и Invitations.csv.

Данные берутся только из файлов, которые пользователь сам скачал. Текст
сообщений не читается и не сохраняется — только факт, дата и счётчик.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .dates import parse_date
from .linkedin_export import read_table
from .models import Interaction, Network

_SLUG = re.compile(r"/in/([^/?#,\s]+)")


def _slugs(*values: str) -> list[str]:
    found: list[str] = []
    for value in values:
        found.extend(_SLUG.findall(value or ""))
    return found


def _date(value: str):
    return parse_date((value or "").strip()[:10])


def read_interactions(export_dir: Path) -> dict[str, list[Interaction]]:
    """slug контакта -> взаимодействия."""
    export_dir = Path(export_dir)
    result: dict[str, list[Interaction]] = defaultdict(list)

    messages = export_dir / "Messages.csv"
    if messages.exists():
        for row in read_table(messages, "CONVERSATION ID"):
            when = _date(row.get("DATE", ""))
            slugs = set(_slugs(row.get("SENDER PROFILE URL", ""), row.get("RECIPIENT PROFILE URLS", "")))
            for slug in slugs:
                result[slug].append(Interaction("message", when))

    invitations = export_dir / "Invitations.csv"
    if invitations.exists():
        for row in read_table(invitations, "From"):
            when = _date(row.get("Sent At", ""))
            for slug in set(_slugs(row.get("inviterProfileUrl", ""), row.get("inviteeProfileUrl", ""))):
                result[slug].append(Interaction("invite", when))
    return result


def attach_interactions(network: Network, export_dir: Path) -> int:
    """Добавить взаимодействия контактам сети; возвращает число контактов с данными."""
    by_slug = read_interactions(export_dir)
    attached = 0
    for contact_id, contact in network.contacts.items():
        items = by_slug.get(contact_id)
        if items:
            contact.interactions.extend(items)
            attached += 1
    return attached
