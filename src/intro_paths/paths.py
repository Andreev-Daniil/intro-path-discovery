"""Поиск путей знакомства: пользователь → контакт → цель (2 хопа)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import Network, Person, Target
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
    for contact in network.contacts.values():
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
