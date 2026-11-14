"""Сборка сети из экспорта LinkedIn и ручного обогащения."""

from __future__ import annotations

import re
from pathlib import Path

from .enrichment import read_enrichment
from .interactions import attach_interactions
from .linkedin_export import read_connections, read_education, read_owner, read_positions
from .models import Network, Person
from .normalize import normalize_text


def _slug_from_url(url: str) -> str:
    match = re.search(r"/in/([^/?#]+)", url)
    return match.group(1) if match else ""


def _unique_id(base: str, taken: dict[str, Person]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def build_network(export_dir: Path, enrichment_path: Path | None = None) -> Network:
    export_dir = Path(export_dir)
    connections_path = export_dir / "Connections.csv"
    if not connections_path.exists():
        raise FileNotFoundError(f"нет файла {connections_path}")

    owner = read_owner(export_dir) or Person(id="me")
    positions_path = export_dir / "Positions.csv"
    if positions_path.exists():
        owner.affiliations.extend(read_positions(positions_path))
    education_path = export_dir / "Education.csv"
    if education_path.exists():
        owner.affiliations.extend(read_education(education_path))

    contacts: dict[str, Person] = {}
    for person in read_connections(connections_path):
        base = _slug_from_url(person.profile_url) or normalize_text(
            person.full_name
        ).replace(" ", "-")
        person.id = _unique_id(base or "contact", contacts)
        contacts[person.id] = person

    if enrichment_path is not None:
        for person_id, affiliation in read_enrichment(Path(enrichment_path)):
            if person_id == "me":
                owner.affiliations.append(affiliation)
            elif person_id in contacts:
                contacts[person_id].affiliations.append(affiliation)
            else:
                raise ValueError(
                    f"обогащение: неизвестный person_id {person_id!r} (опечатка?)"
                )

    network = Network(owner=owner, contacts=contacts)
    attach_interactions(network, export_dir)  # если в экспорте есть Messages/Invitations
    return network
