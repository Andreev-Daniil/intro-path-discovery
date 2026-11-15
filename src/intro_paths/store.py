"""Сохранение сети и цели в JSON."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .dates import parse_date
from .models import (
    Affiliation,
    Coauthor,
    Insider,
    Interaction,
    Network,
    Period,
    Person,
    Target,
    Work,
)
from .normalize import normalize_org


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _affiliation_to_dict(a: Affiliation) -> dict:
    return {
        "kind": a.kind,
        "name": a.name,
        "norm": a.norm,
        "start": _iso(a.period.start),
        "end": _iso(a.period.end),
        "role": a.role,
    }


def _affiliation_from_dict(d: dict) -> Affiliation:
    name = d["name"]
    return Affiliation(
        kind=d["kind"],
        name=name,
        norm=d.get("norm") or normalize_org(name),
        period=Period(parse_date(d.get("start")), parse_date(d.get("end"), end=True)),
        role=d.get("role", ""),
    )


def _work_to_dict(w: Work) -> dict:
    return {
        "id": w.id,
        "title": w.title,
        "year": w.year,
        "doi": w.doi,
        "url": w.url,
        "coauthors": [
            {"name": c.name, "openalex_id": c.openalex_id, "institutions": list(c.institutions)}
            for c in w.coauthors
        ],
    }


def _work_from_dict(d: dict) -> Work:
    return Work(
        id=d["id"],
        title=d.get("title", ""),
        year=d.get("year"),
        doi=d.get("doi", ""),
        url=d.get("url", ""),
        coauthors=tuple(
            Coauthor(c["name"], c.get("openalex_id", ""), tuple(c.get("institutions", [])))
            for c in d.get("coauthors", [])
        ),
    )


def _insider_to_dict(i: Insider) -> dict:
    return {
        "name": i.name,
        "is_director": i.is_director,
        "is_officer": i.is_officer,
        "title": i.title,
        "first_seen": _iso(i.first_seen),
        "last_seen": _iso(i.last_seen),
        "source_url": i.source_url,
    }


def _insider_from_dict(d: dict) -> Insider:
    return Insider(
        name=d["name"],
        is_director=bool(d.get("is_director")),
        is_officer=bool(d.get("is_officer")),
        title=d.get("title", ""),
        first_seen=parse_date(d.get("first_seen")),
        last_seen=parse_date(d.get("last_seen"), end=True),
        source_url=d.get("source_url", ""),
    )


def _person_to_dict(p: Person) -> dict:
    return {
        "id": p.id,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "profile_url": p.profile_url,
        "connected_on": _iso(p.connected_on),
        "affiliations": [_affiliation_to_dict(a) for a in p.affiliations],
        "works": [_work_to_dict(w) for w in p.works],
        "interactions": [
            {"kind": i.kind, "when": _iso(i.when), "count": i.count} for i in p.interactions
        ],
    }


def _person_from_dict(d: dict) -> Person:
    return Person(
        id=d["id"],
        first_name=d.get("first_name", ""),
        last_name=d.get("last_name", ""),
        profile_url=d.get("profile_url", ""),
        connected_on=parse_date(d.get("connected_on")),
        affiliations=[_affiliation_from_dict(a) for a in d.get("affiliations", [])],
        works=[_work_from_dict(w) for w in d.get("works", [])],
        interactions=[
            Interaction(i["kind"], parse_date(i.get("when")), int(i.get("count", 1)))
            for i in d.get("interactions", [])
        ],
    )


def save_network(network: Network, path: Path) -> None:
    data = {
        "owner": _person_to_dict(network.owner),
        "contacts": [_person_to_dict(p) for p in network.contacts.values()],
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def load_network(path: Path) -> Network:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    contacts = [_person_from_dict(p) for p in data["contacts"]]
    return Network(
        owner=_person_from_dict(data["owner"]),
        contacts={p.id: p for p in contacts},
    )


def target_to_dict(t: Target) -> dict:
    return {
        "name": t.name,
        "kind": t.kind,
        "affiliations": [_affiliation_to_dict(a) for a in t.affiliations],
        "works": [_work_to_dict(w) for w in t.works],
        "insiders": [_insider_to_dict(i) for i in t.insiders],
        "fictional": t.fictional,
    }


def target_from_dict(data: dict) -> Target:
    return Target(
        name=data.get("name", ""),
        affiliations=[_affiliation_from_dict(a) for a in data.get("affiliations", [])],
        kind=data.get("kind", ""),
        works=[_work_from_dict(w) for w in data.get("works", [])],
        insiders=[_insider_from_dict(i) for i in data.get("insiders", [])],
        fictional=bool(data.get("fictional", False)),
    )


def save_target(target: Target, path: Path) -> None:
    Path(path).write_text(
        json.dumps(target_to_dict(target), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def load_target(path: Path) -> Target:
    return target_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
