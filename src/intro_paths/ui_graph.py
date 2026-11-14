"""Отрисовка графа (pyvis / vis-network, ресурсы встроены — работает офлайн)."""

from __future__ import annotations

import html
import re

from .graph import Graph

NODE_STYLE = {
    "owner": ("#1f77b4", "star", 34, "Вы"),
    "contact": ("#7f8c8d", "dot", 14, "Контакт"),
    "org": ("#f39c12", "square", 20, "Организация"),
    "school": ("#27ae60", "triangle", 20, "Вуз"),
    "work": ("#8e44ad", "diamond", 15, "Работа (OpenAlex)"),
    "target": ("#e74c3c", "hexagon", 36, "Цель"),
    "insider": ("#c0392b", "dot", 14, "Инсайдер SEC (не в сети)"),
    "bridge": ("#e67e22", "dot", 14, "Мост (знакомый знакомого)"),
}
EDGE_COLOR = {
    "connected": "#bdc3c7", "worked_at": "#f5b041", "studied_at": "#58d68d", "board": "#d35400",
    "coauthored": "#a569bd", "insider_of": "#e74c3c", "colleague": "#85c1e9", "classmate": "#85c1e9",
}
EDGE_LABEL = {
    "connected": "контакт", "worked_at": "работа", "studied_at": "учёба", "board": "совет директоров",
    "coauthored": "соавторство", "insider_of": "директор/офицер (SEC)", "colleague": "коллеги (выведено)",
    "classmate": "однокурсники (выведено)",
}
HIGHLIGHT = "#e74c3c"
OPTIONS = """{
  "physics": {"barnesHut": {"gravitationalConstant": -9000, "springLength": 140, "springConstant": 0.03,
              "damping": 0.35}, "stabilization": {"iterations": 250}},
  "interaction": {"hover": true, "tooltipDelay": 80, "navigationButtons": true, "keyboard": true},
  "edges": {"smooth": {"type": "continuous"}}
}"""


def legend() -> list[tuple[str, str, str]]:
    return [(label, color, shape) for color, shape, _, label in NODE_STYLE.values()]


def render_graph_html(
    g: Graph, *, height: int = 680, min_weight: float = 0.0, search: str = "", only_highlight: bool = False
) -> str:
    from pyvis.network import Network as PyNet

    # на 10 px ниже рамки iframe, чтобы внутри не появлялась полоса прокрутки
    net = PyNet(height=f"{height - 10}px", width="100%", cdn_resources="in_line", bgcolor="#ffffff")
    needle = search.strip().lower()
    top = set(g.top_connectors)

    visible: set[str] = set()
    edge_rows = []
    for i, e in enumerate(g.edges):
        if e.weight < min_weight and e.kind != "connected":
            continue
        if e.kind == "connected" and e.weight < min_weight:
            continue
        if only_highlight and i not in g.highlight:
            continue
        edge_rows.append((i, e))
        visible.update((e.src, e.dst))
    if only_highlight:
        visible.update(n for n in ("owner", "target") if n in g.nodes)

    for node in g.nodes.values():
        if node.id not in visible:
            continue
        color, shape, size, type_label = NODE_STYLE[node.type]
        matched = bool(needle) and needle in node.label.lower()
        border = HIGHLIGHT if node.id in top or matched else color
        safe_label = node.label.replace("<", "‹").replace(">", "›")  # метка попадает в <script>
        title = f"<b>{html.escape(node.label)}</b><br>{type_label}"
        if node.type == "contact" and node.meta.get("warmth") is not None:
            title += f"<br>тепло связи: {node.meta['warmth']:.2f}"
        net.add_node(
            node.id, label=safe_label, title=title, shape=shape, color={"background": color, "border": border},
            size=size + (8 if node.id in top or matched else 0), borderWidth=4 if border == HIGHLIGHT else 1,
            font={"size": 13 if node.type in ("owner", "target") else 11},
        )
    for i, e in edge_rows:
        marked = i in g.highlight
        title = EDGE_LABEL.get(e.kind, e.kind) + (f": {e.label}" if e.label else "")
        if e.weight and e.kind in ("insider_of", "connected", "colleague"):
            title += f" (вес {e.weight:.2f})"
        net.add_edge(
            e.src, e.dst, title=html.escape(title), dashes=e.inferred,
            color=HIGHLIGHT if marked else EDGE_COLOR.get(e.kind, "#aaa"),
            width=4 if marked else 1 + 2.5 * min(e.weight, 1.0),
        )
    net.set_options(OPTIONS)
    page = net.generate_html(notebook=False)
    # pyvis подтягивает bootstrap с CDN — для графа он не нужен, а офлайн-режим ломается
    page = re.sub(r"<(link|script)\b[^>]*cdn\.jsdelivr\.net[^>]*>(\s*</script>)?", "", page, flags=re.S)
    page = re.sub(r"<center>\s*<h1>.*?</h1>\s*</center>", "", page, flags=re.S)
    # граф целиком в кадре после стабилизации физики; без внутренней прокрутки и рамки
    page = page.replace(
        "</head>", "<style>html,body{margin:0;overflow:hidden}.card{border:0!important}</style></head>", 1)
    return page.replace(
        "network = new vis.Network(container, data, options);",
        "network = new vis.Network(container, data, options);"
        f"var boxH = '{height - 10}px';"  # контейнер скрыт при загрузке: его высота = 0
        "var fitNow = function(){ network.setSize('100%', boxH); network.redraw(); network.fit({animation:false}); };"
        # Вкладка Streamlit скрыта при первой отрисовке (размер 0): раскладываем граф и подгоняем кадр,
        # когда он впервые становится видимым; затем замораживаем физику (узлы можно двигать вручную).
        "var settle = function(){ network.setOptions({physics:{enabled:true}}); network.setSize('100%', boxH);"
        " network.once('stabilizationIterationsDone', function(){"
        " network.setOptions({physics:{enabled:false}}); fitNow(); }); network.stabilize(300); };"
        # опрос видимости надёжнее IntersectionObserver внутри скрытой вкладки
        "var waitVisible = setInterval(function(){ if (container.offsetWidth > 0) {"
        " clearInterval(waitVisible); settle(); } }, 300);"
        "window.addEventListener('resize', fitNow);",
        1,
    )
