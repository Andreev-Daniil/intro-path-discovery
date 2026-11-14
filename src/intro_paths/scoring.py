"""Сигналы связи контакта с целью и общая сила пути.

Сила пары = noisy-OR по независимым сигналам: 1 - Π(1 - w_i).
Веса — стартовые оценки, их нужно калибровать на размеченных кейсах.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .dates import overlap_status
from .models import Person, Target
from .names import is_same_person
from .normalize import normalize_org

@dataclass(frozen=True)
class ScoringConfig:
    """Все настраиваемые веса и коэффициенты. Значения по умолчанию — стартовые оценки."""

    employer: float = 0.6
    school: float = 0.5
    board: float = 0.9
    insider_director: float = 0.9
    insider_officer: float = 0.7
    coauthor_base: float = 0.5
    coauthor_step: float = 0.1
    coauthor_max: float = 0.85
    unknown_time_factor: float = 0.5  # период неизвестен
    name_only_factor: float = 0.5  # совпало только имя, нет подтверждения
    stale_factor: float = 0.7  # давние свидетельства
    insider_stale_years: int = 6
    coauthor_stale_years: int = 5
    hop_discount: float = 0.5  # скидка за дополнительный хоп (3-хоповые пути)

    def base(self, kind: str) -> tuple[str, float]:
        """kind аффилиации -> (код сигнала, базовый вес)."""
        return {
            "position": ("same_employer", self.employer),
            "education": ("same_school", self.school),
            "board": ("same_board", self.board),
        }[kind]

    def insider_weight(self, code: str) -> float:
        return self.insider_director if code == "insider_director" else self.insider_officer


DEFAULT = ScoringConfig()

LABELS: dict[str, str] = {
    "same_employer": "общий работодатель",
    "same_school": "общий вуз",
    "same_board": "общий совет директоров",
    "insider_director": "директор целевой компании (SEC)",
    "insider_officer": "руководитель целевой компании (SEC)",
    "coauthor": "совместные публикации с целью",
    "interaction": "взаимодействия в LinkedIn",
}

@dataclass(frozen=True)
class Signal:
    code: str
    detail: str
    weight: float
    evidence_url: str = ""


def compute_signals(
    connector: Person, target: Target, today: date, cfg: ScoringConfig = DEFAULT
) -> list[Signal]:
    """Сигналы между контактом и целью. Одинаковый сигнал по одной организации учитывается один раз."""
    best: dict[tuple[str, str], Signal] = {}
    for mine in connector.affiliations:
        for theirs in target.affiliations:
            if mine.kind != theirs.kind or not mine.norm or mine.norm != theirs.norm:
                continue
            status = overlap_status(mine.period, theirs.period, today)
            if status == "no":
                continue
            code, base = cfg.base(mine.kind)
            if status == "yes":
                weight, when = base, "периоды пересекаются"
            else:
                weight, when = base * cfg.unknown_time_factor, "даты неизвестны"
            key = (code, mine.norm)
            if key not in best or weight > best[key].weight:
                best[key] = Signal(
                    code=code,
                    detail=f"{LABELS[code]}: {mine.name} ({when})",
                    weight=weight,
                )
    return sorted(best.values(), key=lambda s: -s.weight)


def _target_orgs(target: Target) -> set[str]:
    return {a.norm for a in target.affiliations if a.kind == "position" and a.norm}


def _contact_orgs(person: Person) -> set[str]:
    return {a.norm for a in person.affiliations if a.norm}


def _years_ago(when: date | None, today: date) -> float | None:
    return None if when is None else (today - when).days / 365.25


def compute_insider_signals(
    connector: Person, target: Target, today: date, cfg: ScoringConfig = DEFAULT
) -> list[Signal]:
    """Контакт совпал по имени с директором/офицером целевой компании (SEC)."""
    name = connector.full_name
    if not name or not target.insiders:
        return []
    corroborated = bool(_target_orgs(target) & _contact_orgs(connector))
    best: Signal | None = None
    for insider in target.insiders:
        if not is_same_person(name, insider.name):
            continue
        code = "insider_director" if insider.is_director else "insider_officer"
        weight = cfg.insider_weight(code)
        if not corroborated:
            weight *= cfg.name_only_factor
        age = _years_ago(insider.last_seen, today)
        if age is not None and age > cfg.insider_stale_years:
            weight *= cfg.stale_factor
        note = "подтверждено местом работы" if corroborated else "совпало только имя"
        title = f", {insider.title}" if insider.title else ""
        signal = Signal(
            code=code,
            detail=f"{LABELS[code]}: {insider.name}{title} ({note})",
            weight=round(weight, 4),
            evidence_url=insider.source_url,
        )
        if best is None or signal.weight > best.weight:
            best = signal
    return [best] if best else []


def compute_coauthor_signals(
    connector: Person, target: Target, today: date, cfg: ScoringConfig = DEFAULT
) -> list[Signal]:
    """Совместные работы контакта и цели: по общим id работ или по имени среди соавторов цели."""
    if not target.works:
        return []
    target_by_id = {w.id: w for w in target.works}
    target_by_id.update({w.doi.lower(): w for w in target.works if w.doi})
    shared: dict[str, object] = {}
    for w in connector.works:
        hit = target_by_id.get(w.id) or (target_by_id.get(w.doi.lower()) if w.doi else None)
        if hit:
            shared[hit.id] = hit
    shared = list(shared.values())
    confirmed = bool(shared)

    if not shared:
        name = connector.full_name
        if not name:
            return []
        orgs = _contact_orgs(connector)
        for work in target.works:
            for co in work.coauthors:
                if not is_same_person(name, co.name):
                    continue
                shared.append(work)
                if orgs & {normalize_org(i) for i in co.institutions}:
                    confirmed = True
                break
    if not shared:
        return []

    weight = min(cfg.coauthor_max, cfg.coauthor_base + cfg.coauthor_step * (len(shared) - 1))
    years = [w.year for w in shared if w.year]
    if years and today.year - max(years) > cfg.coauthor_stale_years:
        weight *= cfg.stale_factor
    if not confirmed:
        weight *= cfg.name_only_factor
    latest = max(shared, key=lambda w: w.year or 0)
    note = "подтверждено" if confirmed else "совпало только имя"
    year = f", {latest.year}" if latest.year else ""
    return [
        Signal(
            code="coauthor",
            detail=f"{LABELS['coauthor']}: {len(shared)} шт., например «{latest.title}»{year} ({note})",
            weight=round(weight, 4),
            evidence_url=latest.url or (f"https://doi.org/{latest.doi}" if latest.doi else ""),
        )
    ]


def compute_all_signals(
    connector: Person, target: Target, today: date, cfg: ScoringConfig = DEFAULT
) -> list[Signal]:
    signals = (
        compute_signals(connector, target, today, cfg)
        + compute_insider_signals(connector, target, today, cfg)
        + compute_coauthor_signals(connector, target, today, cfg)
    )
    return sorted(signals, key=lambda s: -s.weight)


def warmth(person: Person, today: date) -> float | None:
    """Близость владельца и контакта 0..1 по переписке и приглашениям; None — данных нет."""
    if not person.interactions:
        return None
    messages = sum(i.count for i in person.interactions if i.kind == "message")
    invites = sum(i.count for i in person.interactions if i.kind == "invite")
    dates = [i.when for i in person.interactions if i.when]
    recent = 1.0
    if dates:
        age = (today - max(dates)).days / 365.25
        recent = 1.0 if age <= 1 else 0.6 if age <= 3 else 0.3
    base = min(messages / 5, 1.0) * 0.7 + min(invites, 1) * 0.3
    return round(base * recent, 4)


def network_has_interactions(contacts) -> bool:
    return any(c.interactions for c in contacts)


def effective_warmth(person: Person, today: date, has_data: bool = False) -> float | None:
    """Тепло с учётом наличия данных: если переписка в экспорте есть, а у контакта её нет — 0."""
    if person.interactions:
        return warmth(person, today)
    return 0.0 if has_data else None


def warmth_multiplier(person: Person, today: date, has_data: bool = False) -> float:
    value = effective_warmth(person, today, has_data)
    return 1.0 if value is None else round(0.5 + 0.5 * value, 4)


def combine_strength(signals: list[Signal]) -> float:
    """Noisy-OR: независимые сигналы усиливают друг друга, но не складываются линейно."""
    remaining = 1.0
    for signal in signals:
        remaining *= 1.0 - signal.weight
    return round(1.0 - remaining, 4)
