"""Поиск путей знакомства: пользователь → контакт → цель (2 хопа)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from .models import Network, Person, Target
from .names import is_same_person
from .scoring import (
    DEFAULT,
    LABELS,
    ScoringConfig,
    Signal,
    combine_strength,
    compute_all_signals,
    effective_warmth,
    network_has_interactions,
    warmth_multiplier,
)


@dataclass
class IntroPath:
    connector: Person
    strength: float
    signals: list[Signal]


def direct_contacts(network: Network, target: Target) -> list[Person]:
    """Цель-человек, который уже есть среди контактов (по имени): представлять не нужно."""
    if target.kind != "person" or not target.name:
        return []
    return [c for c in network.contacts.values() if is_same_person(c.full_name, target.name)]


def with_direct_contacts(network: Network, target: Target) -> tuple[Target, list[Person]]:
    """Если цель уже в контактах, дополнить её известными местами работы и учёбы из экспорта.

    Тогда посредниками становятся коллеги и однокурсники цели. Исходная цель не меняется.
    """
    direct = direct_contacts(network, target)
    if not direct:
        return target, []
    enriched = replace(target, affiliations=list(target.affiliations))
    have = {(a.kind, a.norm) for a in enriched.affiliations}
    for contact in direct:
        for aff in contact.affiliations:
            if (aff.kind, aff.norm) not in have:
                enriched.affiliations.append(aff)
                have.add((aff.kind, aff.norm))
    return enriched, direct


def find_intro_paths(
    network: Network,
    target: Target,
    *,
    today: date | None = None,
    min_strength: float = 0.0,
    limit: int = 10,
    cfg: ScoringConfig = DEFAULT,
) -> list[IntroPath]:
    today = today or date.today()
    found: list[IntroPath] = []
    has_data = network_has_interactions(network.contacts.values())
    target_itself = {c.id for c in direct_contacts(network, target)}  # цель не может представить сама себя
    for contact in network.contacts.values():
        if contact.id in target_itself:
            continue
        signals = compute_all_signals(contact, target, today, cfg)
        if not signals:
            continue
        # Тепло связи owner→контакт масштабирует силу, но не входит в noisy-OR.
        strength = round(
            combine_strength(signals) * warmth_multiplier(contact, today, has_data), 4
        )
        value = effective_warmth(contact, today, has_data)
        if value is not None:
            signals = [
                *signals,
                Signal("interaction", f"{LABELS['interaction']}: близость {value:.2f}", 0.0),
            ]
        if strength >= min_strength:
            found.append(IntroPath(connector=contact, strength=strength, signals=signals))
    found.sort(key=lambda p: (-p.strength, p.connector.full_name))
    return found[:limit]
