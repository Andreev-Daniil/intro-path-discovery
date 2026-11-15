"""Картинки без сторонних библиотек: SVG-схема путей и статическая карта связей.

Работают в любом окружении (не нужны ни pyvis, ни интернет), скачиваются как .svg.
"""

from __future__ import annotations

import html
import math
import random

from .graph import ChainPath, Graph
from .paths import IntroPath
from .scoring import LABELS

FONT = "font-family='Segoe UI, Helvetica, Arial, sans-serif'"
COLOR = {"owner": "#1f77b4", "contact": "#8a9499", "org": "#f39c12", "school": "#27ae60", "work": "#8e44ad",
         "target": "#e74c3c", "insider": "#c0392b", "bridge": "#e67e22"}
SIGNAL_COLOR = {"same_employer": "#f5b041", "same_school": "#58d68d", "same_board": "#d35400",
                "insider_director": "#e74c3c", "insider_officer": "#ec7063", "coauthor": "#a569bd",
                "interaction": "#bdc3c7"}
SIGNAL_SHORT = {"same_employer": "общий работодатель", "same_school": "общий вуз",
                "same_board": "совет директоров", "insider_director": "директор (SEC)",
                "insider_officer": "офицер (SEC)", "coauthor": "совместные статьи", "bridge": "мост"}


def _esc(text: str, limit: int = 40) -> str:
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return html.escape(text, quote=True)


def _svg(width: int, height: int, body: str) -> str:
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='{width}' "
            f"height='{height}' {FONT}><rect width='100%' height='100%' fill='#ffffff'/>{body}</svg>")


def _curve(x1: float, y1: float, x2: float, y2: float, color: str, width: float = 2, dashed: bool = False) -> str:
    mid = (x1 + x2) / 2
    dash = " stroke-dasharray='6 4'" if dashed else ""
    return (f"<path d='M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}' "
            f"fill='none' stroke='{color}' stroke-width='{width}'{dash} opacity='0.85'/>")


def _box(x, y, w, h, fill, stroke, lines: list[tuple[str, int, str, str]], radius=8) -> str:
    """Прямоугольник с несколькими строками: (текст, размер, цвет, начертание)."""
    out = [f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='{radius}' fill='{fill}' stroke='{stroke}' stroke-width='1.5'/>"]
    cy = y + 18
    for text, size, color, weight in lines:
        out.append(f"<text x='{x + 10}' y='{cy}' font-size='{size}' fill='{color}' font-weight='{weight}'>{text}</text>")
        cy += size + 6
    return "".join(out)


def _strength_color(value: float) -> str:
    return "#c0392b" if value >= 0.75 else "#e67e22" if value >= 0.5 else "#7f8c8d"


def paths_diagram_svg(
    owner: str, target: str, paths: list[IntroPath], chains: list[ChainPath] | None = None, *, top: int = 6,
) -> str:
    """Схема «Вы → контакт → почему → цель» для топ-путей и цепочек через знакомого знакомого."""
    chains = chains or []
    rows: list[dict] = []
    for p in paths[:top]:
        chips = [(s.code, s.detail, s.weight) for s in p.signals if s.weight > 0][:3] or [("coauthor", "связь", 0.0)]
        rows.append({"name": p.connector.full_name, "strength": p.strength, "chips": chips, "dashed": False})
    for c in chains[:3]:
        rows.append({"name": c.via.full_name, "strength": c.strength, "dashed": True,
                     "chips": [("bridge", f"мост: {c.bridge_name} ({c.bridge_role})", c.link)]})
    if not rows:
        return _svg(900, 160, f"<text x='450' y='85' text-anchor='middle' font-size='18' fill='#7f8c8d'>"
                              f"Путей к «{_esc(target, 50)}» не найдено</text>")

    width, x_owner, x_con, x_chip, x_target = 1120, 24, 250, 560, 960
    heights = [max(78, 32 * len(r["chips"]) + 18) for r in rows]
    total = 70 + sum(heights) + 16 * (len(rows) - 1) + 30
    body = [f"<text x='24' y='34' font-size='20' font-weight='700' fill='#2c3e50'>Кто и через кого может представить: "
            f"{_esc(target, 60)}</text>",
            "<text x='250' y='58' font-size='12' fill='#7f8c8d'>контакт · сила пути</text>"
            "<text x='560' y='58' font-size='12' fill='#7f8c8d'>почему · вес сигнала</text>"]
    centers = []
    y = 70
    for r, h in zip(rows, heights):
        centers.append(y + h / 2)
        y += h + 16
    owner_y = (centers[0] + centers[-1]) / 2
    target_y = owner_y

    body.append(_box(x_owner, owner_y - 30, 150, 60, "#eaf3fb", COLOR["owner"],
                     [("Вы", 12, "#7f8c8d", "400"), (_esc(owner, 18), 14, "#1f4e79", "700")]))
    body.append(_box(x_target, target_y - 30, 136, 60, "#fdecea", COLOR["target"],
                     [("Цель", 12, "#7f8c8d", "400"), (_esc(target, 15), 14, "#922b21", "700")]))

    y = 70
    for r, h, cy in zip(rows, heights, centers):
        dashed = r["dashed"]
        body.append(_curve(x_owner + 150, owner_y, x_con, cy, "#aab7c4", 2, dashed))
        s = r["strength"]
        bar_w = 200 * min(max(s, 0.0), 1.0)
        body.append(_box(x_con, cy - 30, 240, 60, "#ffffff", _strength_color(s),
                         [(_esc(r["name"], 26), 14, "#2c3e50", "700")]))
        body.append(f"<rect x='{x_con + 10}' y='{cy + 6}' width='200' height='8' rx='4' fill='#ecf0f1'/>"
                    f"<rect x='{x_con + 10}' y='{cy + 6}' width='{bar_w:.0f}' height='8' rx='4' fill='{_strength_color(s)}'/>"
                    f"<text x='{x_con + 216}' y='{cy + 14}' font-size='12' font-weight='700' fill='{_strength_color(s)}'>"
                    f"{s:.2f}{'*' if dashed else ''}</text>")
        n = len(r["chips"])
        top_y = cy - (n * 32) / 2 + 4
        for i, (code, detail, weight) in enumerate(r["chips"]):
            chip_y = top_y + i * 32
            color = SIGNAL_COLOR.get(code, "#e67e22")
            label = SIGNAL_SHORT.get(code, LABELS.get(code, code))
            text = detail.split(": ", 1)[1] if ": " in detail else detail
            body.append(_curve(x_con + 240, cy, x_chip, chip_y + 13, color, 2, dashed))
            body.append(f"<rect x='{x_chip}' y='{chip_y}' width='350' height='26' rx='13' fill='{color}' fill-opacity='0.16' "
                        f"stroke='{color}' stroke-width='1.5'/>"
                        f"<text x='{x_chip + 12}' y='{chip_y + 17}' font-size='12' fill='#2c3e50'>"
                        f"<tspan font-weight='700'>{_esc(label, 22)}</tspan> · {_esc(text, 34)}</text>"
                        f"<text x='{x_chip + 338}' y='{chip_y + 17}' text-anchor='end' font-size='12' font-weight='700' "
                        f"fill='#2c3e50'>{weight:.2f}</text>")
            body.append(_curve(x_chip + 350, chip_y + 13, x_target, target_y, color, 2, dashed))
        y += h + 16
    note = ("* пунктир — цепочка через знакомого знакомого: оценка по общей организации, не подтверждена"
            if any(r["dashed"] for r in rows) else "сплошные линии — подтверждённые сигналы")
    body.append(f"<text x='24' y='{total - 10}' font-size='12' fill='#7f8c8d'>{html.escape(note)}</text>")
    return _svg(width, int(total), "".join(body))


# ---------------------------------------------------------------- статическая карта связей

def _layout(graph: Graph, width: int, height: int, iterations: int = 140) -> dict[str, tuple[float, float]]:
    """Силовая раскладка Фрюхтермана–Рейнгольда: детерминированная, владелец слева, цель справа."""
    rnd = random.Random(42)
    ids = sorted(graph.nodes)
    pos = {i: [width / 2 + rnd.uniform(-width / 3, width / 3), height / 2 + rnd.uniform(-height / 3, height / 3)]
           for i in ids}
    fixed = {"owner": (70.0, height / 2), "target": (width - 90.0, height / 2)}
    for k, v in fixed.items():
        if k in pos:
            pos[k] = list(v)
    n = max(len(ids), 1)
    k = math.sqrt(width * height / n) * 0.9
    edges = [(e.src, e.dst) for e in graph.edges if e.src in pos and e.dst in pos]
    temp = width / 8
    for _ in range(iterations if n <= 400 else 40):
        disp = {i: [0.0, 0.0] for i in ids}
        for a in range(n):
            ia = ids[a]
            for b in range(a + 1, n):
                ib = ids[b]
                dx, dy = pos[ia][0] - pos[ib][0], pos[ia][1] - pos[ib][1]
                dist = math.hypot(dx, dy) or 0.01
                force = k * k / dist
                disp[ia][0] += dx / dist * force
                disp[ia][1] += dy / dist * force
                disp[ib][0] -= dx / dist * force
                disp[ib][1] -= dy / dist * force
        for s, t in edges:
            dx, dy = pos[s][0] - pos[t][0], pos[s][1] - pos[t][1]
            dist = math.hypot(dx, dy) or 0.01
            force = dist * dist / k
            disp[s][0] -= dx / dist * force
            disp[s][1] -= dy / dist * force
            disp[t][0] += dx / dist * force
            disp[t][1] += dy / dist * force
        for i in ids:
            if i in fixed:
                continue
            dx, dy = disp[i]
            d = math.hypot(dx, dy) or 0.01
            pos[i][0] = min(width - 30, max(30, pos[i][0] + dx / d * min(d, temp)))
            pos[i][1] = min(height - 30, max(30, pos[i][1] + dy / d * min(d, temp)))
        temp *= 0.96
    return {i: (p[0], p[1]) for i, p in pos.items()}


def _shape(kind: str, x: float, y: float, r: float, color: str, stroke: str) -> str:
    style = f"fill='{color}' stroke='{stroke}' stroke-width='2'"
    if kind == "org":
        return f"<rect x='{x - r:.1f}' y='{y - r:.1f}' width='{2 * r:.1f}' height='{2 * r:.1f}' {style}/>"
    if kind == "school":
        return f"<polygon points='{x:.1f},{y - r:.1f} {x + r:.1f},{y + r:.1f} {x - r:.1f},{y + r:.1f}' {style}/>"
    if kind == "work":
        return f"<polygon points='{x:.1f},{y - r:.1f} {x + r:.1f},{y:.1f} {x:.1f},{y + r:.1f} {x - r:.1f},{y:.1f}' {style}/>"
    if kind == "target":
        pts = " ".join(f"{x + r * math.cos(math.pi / 3 * i):.1f},{y + r * math.sin(math.pi / 3 * i):.1f}" for i in range(6))
        return f"<polygon points='{pts}' {style}/>"
    return f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r:.1f}' {style}/>"


def graph_svg(graph: Graph, *, width: int = 1100, height: int = 700) -> str:
    """Статическая карта связей: те же узлы и рёбра, что в интерактивном графе."""
    if not graph.nodes:
        return _svg(width, 120, "<text x='50%' y='60' text-anchor='middle' fill='#7f8c8d'>Граф пуст</text>")
    pos = _layout(graph, width, height)
    top = set(graph.top_connectors)
    body = []
    for i, e in enumerate(graph.edges):
        if e.src not in pos or e.dst not in pos:
            continue
        (x1, y1), (x2, y2) = pos[e.src], pos[e.dst]
        hot = i in graph.highlight
        color = "#e74c3c" if hot else ("#85c1e9" if e.inferred else "#c5ccd1")
        dash = " stroke-dasharray='5 4'" if e.inferred else ""
        body.append(f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}' stroke='{color}' "
                    f"stroke-width='{3.5 if hot else 1.2}'{dash} opacity='{0.95 if hot else 0.7}'/>")
    sizes = {"owner": 16, "target": 20, "org": 10, "school": 10, "work": 8, "insider": 7, "bridge": 8, "contact": 6}
    order = ["contact", "insider", "bridge", "work", "org", "school", "owner", "target"]
    for kind in order:
        for node in graph.nodes.values():
            if node.type != kind:
                continue
            x, y = pos[node.id]
            hot = node.id in top
            body.append(_shape(kind, x, y, sizes[kind] + (4 if hot else 0), COLOR[kind],
                               "#e74c3c" if hot else "#ffffff"))
            if kind != "contact" or hot:
                body.append(f"<text x='{x:.1f}' y='{y - sizes[kind] - 6:.1f}' text-anchor='middle' font-size='11' "
                            f"font-weight='{'700' if kind in ('owner', 'target') or hot else '400'}' fill='#2c3e50' "
                            f"stroke='#ffffff' stroke-width='3' paint-order='stroke'>{_esc(node.label, 22)}</text>")
    legend = [("owner", "Вы"), ("contact", "Контакт"), ("org", "Организация"), ("school", "Вуз"),
              ("work", "Работа"), ("target", "Цель"), ("insider", "Инсайдер SEC"), ("bridge", "Мост")]
    lx = 16
    for kind, label in legend:
        body.append(_shape(kind, lx + 7, height - 16, 6, COLOR[kind], "#ffffff"))
        body.append(f"<text x='{lx + 18}' y='{height - 12}' font-size='11' fill='#2c3e50'>{label}</text>")
        lx += 24 + 7 * len(label)
    body.append(f"<text x='{width - 12}' y='{height - 12}' text-anchor='end' font-size='11' fill='#7f8c8d'>"
                f"красным — топ-пути · пунктиром — выведенные связи</text>")
    return _svg(width, height, "".join(body))
