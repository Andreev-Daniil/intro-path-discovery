"""Граф связей: владелец, контакты, организации, вузы, работы и цель.

Граф нужен для объяснения и визуализации; ранжирование путей по-прежнему считает
`paths.find_intro_paths`. Рёбра `colleague`/`classmate` между контактами — выведены
из пересечения периодов и помечены `inferred=True`: знакомство людей не подтверждено.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from .dates import overlap_status
from .models import Affiliation, Network, Person, Target
from .names import is_same_person
from .normalize import normalize_org
from .paths import IntroPath
from .scoring import (
    DEFAULT,
    ScoringConfig,
    Signal,
    combine_strength,
    compute_insider_signals,
    compute_signals,
    effective_warmth,
    network_has_interactions,
    warmth_multiplier,
)

NODE_TYPES = ("owner", "contact", "org", "school", "work", "target", "insider", "bridge")
EDGE_KINDS = ("connected", "worked_at", "studied_at", "board", "coauthored",
              "insider_of", "colleague", "classmate")
MAX_GROUP_FOR_INFERRED = 25


@dataclass
class Node:
    id: str
    type: str
    label: str
    meta: dict = field(default_factory=dict)


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    weight: float = 1.0
    label: str = ""
    evidence_url: str = ""
    inferred: bool = False


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    top_connectors: list[str] = field(default_factory=list)
    highlight: set[int] = field(default_factory=set)  # индексы рёбер на топ-путях

    def add_node(self, node: Node) -> Node:
        return self.nodes.setdefault(node.id, node)

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)

    def neighbors(self, node_id: str) -> list[tuple[Edge, str]]:
        out = []
        for e in self.edges:
            if e.src == node_id:
                out.append((e, e.dst))
            elif e.dst == node_id:
                out.append((e, e.src))
        return out


_GROUP = {"position": "org", "board": "org", "education": "school"}
_KIND = {"position": "worked_at", "board": "board", "education": "studied_at"}


def _period_label(a: Affiliation) -> str:
    start = a.period.start.year if a.period.start else "?"
    end = a.period.end.year if a.period.end else "н.в."
    return f"{a.role + ', ' if a.role else ''}{start}–{end}"


def build_graph(
    network: Network,
    target: Target,
    paths: list[IntroPath] | None = None,
    chains: list["ChainPath"] | None = None,
    *,
    show_orgs: bool = True,
    show_schools: bool = True,
    show_works: bool = True,
    show_insiders: bool = True,
    inferred: bool = False,
    min_shared: int = 2,
    today: date | None = None,
    cfg: ScoringConfig = DEFAULT,
) -> Graph:
    today = today or date.today()
    g = Graph()
    owner = network.owner
    g.add_node(Node("owner", "owner", owner.full_name or "Вы"))
    g.add_node(Node("target", "target", target.name or "Цель", {"kind": target.kind}))

    people: list[tuple[str, Person]] = [("owner", owner)]
    has_data = network_has_interactions(network.contacts.values())
    for c in network.contacts.values():
        nid = f"c:{c.id}"
        value = effective_warmth(c, today, has_data)
        messages = sum(i.count for i in c.interactions if i.kind == "message")
        g.add_node(Node(nid, "contact", c.full_name or c.id,
                        {"id": c.id, "url": c.profile_url, "warmth": value}))
        g.add_edge(Edge("owner", nid, "connected",
                        weight=value if value is not None else 0.2,
                        label=f"сообщений: {messages}" if c.interactions else "контакт"))
        people.append((nid, c))

    # --- организации и вузы ---
    members: dict[tuple[str, str], list[tuple[str, Affiliation]]] = defaultdict(list)
    names: dict[tuple[str, str], str] = {}
    for pid, person in people:
        for a in person.affiliations:
            if a.norm:
                key = (_GROUP[a.kind], a.norm)
                members[key].append((pid, a))
                names.setdefault(key, a.name)
    target_keys = {(_GROUP[a.kind], a.norm) for a in target.affiliations if a.norm}
    for a in target.affiliations:
        names.setdefault((_GROUP[a.kind], a.norm), a.name)

    included: set[tuple[str, str]] = set()
    for key, items in members.items():
        group = key[0]
        if (group == "org" and not show_orgs) or (group == "school" and not show_schools):
            continue
        if len({pid for pid, _ in items}) >= min_shared or key in target_keys:
            included.add(key)
    for key in target_keys:
        if (key[0] == "org" and show_orgs) or (key[0] == "school" and show_schools):
            included.add(key)

    for key in included:
        group, norm = key
        g.add_node(Node(f"{group[0]}:{norm}", group, names[key]))
    seen: set[tuple[str, str, str]] = set()
    for key in included:
        node_id = f"{key[0][0]}:{key[1]}"
        for pid, a in members.get(key, []):
            dedupe = (pid, node_id, a.kind)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            g.add_edge(Edge(pid, node_id, _KIND[a.kind], label=_period_label(a)))
    for a in target.affiliations:
        key = (_GROUP[a.kind], a.norm)
        if key in included:
            g.add_edge(Edge("target", f"{key[0][0]}:{key[1]}", _KIND[a.kind], label=_period_label(a)))

    # --- инсайдеры SEC ---
    if show_insiders:
        for insider in target.insiders:
            matched = [c for c in network.contacts.values() if is_same_person(c.full_name, insider.name)]
            role = "директор" if insider.is_director else "офицер"
            label = f"{role}{', ' + insider.title if insider.title else ''}"
            if matched:
                for c in matched:
                    sig = compute_insider_signals(c, target, today, cfg)
                    weight = sig[0].weight if sig else 0.0
                    g.add_edge(Edge(f"c:{c.id}", "target", "insider_of", weight=weight,
                                    label=label, evidence_url=insider.source_url))
            else:
                nid = f"i:{insider.name.lower()}"
                g.add_node(Node(nid, "insider", insider.name, {"title": insider.title}))
                g.add_edge(Edge(nid, "target", "insider_of", weight=cfg.insider_weight(
                    "insider_director" if insider.is_director else "insider_officer"),
                    label=label, evidence_url=insider.source_url))

    # --- общие работы ---
    if show_works and target.works:
        for w in target.works:
            hits: list[Person] = []
            for c in network.contacts.values():
                if any(cw.id == w.id or (cw.doi and cw.doi == w.doi) for cw in c.works) or any(
                    is_same_person(c.full_name, co.name) for co in w.coauthors
                ):
                    hits.append(c)
            if not hits:
                continue
            nid = f"w:{w.id}"
            g.add_node(Node(nid, "work", (w.title or w.id)[:60], {"year": w.year, "url": w.url}))
            g.add_edge(Edge("target", nid, "coauthored", label=str(w.year or ""), evidence_url=w.url))
            for c in hits:
                g.add_edge(Edge(f"c:{c.id}", nid, "coauthored", label="соавтор", evidence_url=w.url))

    # --- выведенные связи контакт↔контакт ---
    if inferred:
        for key in included:
            group, _ = key
            people_here = [(pid, a) for pid, a in members.get(key, []) if pid.startswith("c:")]
            if len(people_here) > MAX_GROUP_FOR_INFERRED:
                continue
            for i, (p1, a1) in enumerate(people_here):
                for p2, a2 in people_here[i + 1:]:
                    if p1 == p2 or overlap_status(a1.period, a2.period, today) != "yes":
                        continue
                    kind = "classmate" if group == "school" else "colleague"
                    g.add_edge(Edge(p1, p2, kind, weight=0.4, inferred=True,
                                    label=f"оба: {names[key]}"))

    # --- мосты (3-хоповые цепочки) ---
    for chain in chains or []:
        bid = f"b:{chain.bridge_name.lower()}"
        g.add_node(Node(bid, "bridge", chain.bridge_name, {"role": chain.bridge_role}))
        g.add_edge(Edge(f"c:{chain.via.id}", bid, "colleague", weight=chain.link, inferred=True,
                        label=chain.link_detail))
        g.add_edge(Edge(bid, "target", "insider_of" if chain.kind == "insider" else "coauthored",
                        weight=chain.bridge_weight, label=chain.bridge_role,
                        evidence_url=chain.evidence_url))

    # --- подсветка топ-путей ---
    g.top_connectors = [f"c:{p.connector.id}" for p in paths or []]
    if g.top_connectors:
        adjacent = {e.dst if e.src == "target" else e.src for e in g.edges if "target" in (e.src, e.dst)}
        top = set(g.top_connectors)
        for i, e in enumerate(g.edges):
            if e.src == "owner" and e.dst in top:
                g.highlight.add(i)
            elif e.src in top and (e.dst == "target" or e.dst in adjacent):
                g.highlight.add(i)
            elif e.src == "target" and e.dst in adjacent and any(
                x.src in top and x.dst == e.dst for x in g.edges
            ):
                g.highlight.add(i)
    return g


def graph_stats(g: Graph) -> dict:
    parent = {n: n for n in g.nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    degree: Counter[str] = Counter()
    for e in g.edges:
        if e.src in parent and e.dst in parent:
            parent[find(e.src)] = find(e.dst)
            degree[e.src] += 1
            degree[e.dst] += 1
    components = len({find(n) for n in g.nodes})
    contact_degrees = [(g.nodes[n].label, d) for n, d in degree.most_common()
                       if g.nodes[n].type == "contact"][:5]
    n, m = len(g.nodes), len(g.edges)
    return {
        "nodes": n,
        "edges": m,
        "nodes_by_type": dict(Counter(x.type for x in g.nodes.values())),
        "edges_by_kind": dict(Counter(e.kind for e in g.edges)),
        "components": components,
        "isolated": sum(1 for node in g.nodes if degree[node] == 0),
        "density": round(2 * m / (n * (n - 1)), 4) if n > 1 else 0.0,
        "top_contacts": contact_degrees,
    }


# ---------------------------------------------------------------- 3-хоповые цепочки

@dataclass
class ChainPath:
    """owner → via (контакт) → bridge (не контакт) → цель. Связь via–bridge выведена."""

    via: Person
    bridge_name: str
    bridge_role: str
    kind: str  # insider | coauthor
    bridge_weight: float
    link: float
    link_detail: str
    strength: float
    evidence_url: str = ""
    signals: list[Signal] = field(default_factory=list)


def _bridges(network: Network, target: Target, today: date, cfg: ScoringConfig) -> list[dict]:
    """Люди вокруг цели, которых нет среди контактов: инсайдеры SEC и соавторы."""
    known = [c.full_name for c in network.contacts.values()]

    def is_contact(name: str) -> bool:
        return any(is_same_person(k, name) for k in known)

    out: list[dict] = []
    org_norms = [a.norm for a in target.affiliations if a.kind == "position" and a.norm]
    for ins in target.insiders:
        if is_contact(ins.name):
            continue
        code = "insider_director" if ins.is_director else "insider_officer"
        role = f"{'директор' if ins.is_director else 'офицер'}{', ' + ins.title if ins.title else ''}"
        affs = [Affiliation("position" if not ins.is_director else "board", target.name, n)
                for n in org_norms[:1]] if org_norms else []
        out.append({"name": ins.name, "role": role, "kind": "insider",
                    "weight": cfg.insider_weight(code), "affs": affs, "url": ins.source_url})

    counts: Counter[str] = Counter()
    info: dict[str, tuple[set[str], str, str, int]] = {}
    for w in target.works:
        for co in w.coauthors:
            key = co.openalex_id or co.name.lower()
            counts[key] += 1
            insts, _, _, last = info.get(key, (set(), co.name, "", 0))
            insts |= {i for i in co.institutions if i}
            info[key] = (insts, co.name, w.url, max(last, w.year or 0))
    for key, n in counts.most_common(300):
        insts, name, url, last = info[key]
        if is_contact(name) or not insts:
            continue
        weight = min(cfg.coauthor_max, cfg.coauthor_base + cfg.coauthor_step * (n - 1))
        if last and today.year - last > cfg.coauthor_stale_years:
            weight *= cfg.stale_factor
        affs = [Affiliation("position", i, normalize_org(i)) for i in sorted(insts)]
        out.append({"name": name, "role": f"соавтор цели ({n} раб.)", "kind": "coauthor",
                    "weight": round(weight, 4), "affs": affs, "url": url})
    return out


def find_chain_paths(
    network: Network,
    target: Target,
    *,
    today: date | None = None,
    cfg: ScoringConfig = DEFAULT,
    min_link: float = 0.1,
    per_bridge: int = 1,
    limit: int = 10,
) -> list[ChainPath]:
    """Цепочки через знакомого знакомого: контакт A делит организацию/вуз с человеком B вокруг цели.

    Сила = сила(B→цель) × сила(A–B) × скидка за хоп × тепло(A). Это оценка, а не подтверждение.
    """
    today = today or date.today()
    found: list[ChainPath] = []
    has_data = network_has_interactions(network.contacts.values())
    for bridge in _bridges(network, target, today, cfg):
        as_target = Target(bridge["name"], bridge["affs"])
        options: list[ChainPath] = []
        for contact in network.contacts.values():
            link_signals = compute_signals(contact, as_target, today, cfg)
            link = combine_strength(link_signals)
            if link < min_link:
                continue
            strength = round(
                bridge["weight"] * link * cfg.hop_discount
                * warmth_multiplier(contact, today, has_data), 4
            )
            options.append(ChainPath(
                via=contact, bridge_name=bridge["name"], bridge_role=bridge["role"],
                kind=bridge["kind"], bridge_weight=bridge["weight"], link=link,
                link_detail="; ".join(s.detail for s in link_signals), strength=strength,
                evidence_url=bridge["url"], signals=link_signals,
            ))
        options.sort(key=lambda c: (-c.strength, c.via.full_name))
        found.extend(options[:per_bridge])
    found.sort(key=lambda c: (-c.strength, c.bridge_name))
    return found[:limit]
