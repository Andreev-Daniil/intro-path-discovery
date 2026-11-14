"""Streamlit-интерфейс Intro-Path Discovery.

Запуск:  streamlit run src/intro_paths/app.py

Вкладки: обзор конвейера и целей проекта, данные, интерактивный граф, ранжированные пути,
«как считается» (с пересчётом весов) и источники/приватность.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from intro_paths.config import build_services, load_env
from intro_paths.graph import ChainPath, build_graph, find_chain_paths, graph_stats
from intro_paths.models import Affiliation, Network, Period, Target
from intro_paths.network import build_network
from intro_paths.normalize import normalize_org
from intro_paths.paths import IntroPath, find_intro_paths
from intro_paths.scoring import (
    DEFAULT,
    ScoringConfig,
    combine_strength,
    effective_warmth,
    network_has_interactions,
)
from intro_paths.store import load_network, load_target
from intro_paths.target_enrich import confirm_candidates, enrich_company, enrich_person
from intro_paths.ui_graph import legend, render_graph_html

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "sample"
DEMO = ROOT / "data" / "demo"
DEMO_TODAY = date(2026, 11, 15)
DEMO_ZIP = DEMO / "linkedin_export_demo.zip"
DEMO_ENRICHMENT = DEMO / "enrichment.csv"
DEMO_TARGETS = DEMO / "targets"

DS_APPLE = "Синтетика: Apple (SEC EDGAR)"
DS_HINTON = "Синтетика: Geoffrey Hinton (OpenAlex)"
DS_MINE = "Мои данные (ZIP экспорта LinkedIn)"
DEMO_FILES = {DS_APPLE: "apple", DS_HINTON: "hinton"}


@dataclass
class Result:
    network: Network
    target: Target
    paths: list[IntroPath]
    notes: list[str]


# ----------------------------------------------------------------- загрузка и расчёт

def find_export_dir(root: Path) -> Path:
    """Папка с Connections.csv внутри распакованного архива (на любой глубине)."""
    for candidate in root.rglob("Connections.csv"):
        return candidate.parent
    raise ValueError("в архиве нет Connections.csv — загрузите экспорт LinkedIn целиком")


def load_network_from_zip(data: bytes, enrichment: bytes | None, workdir: Path) -> Network:
    zip_path = workdir / "export.zip"
    zip_path.write_bytes(data)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():  # защита от path traversal
            if Path(member).is_absolute() or ".." in Path(member).parts:
                raise ValueError("подозрительный путь в архиве")
        zf.extractall(workdir / "export")
    enrichment_path = None
    if enrichment:
        enrichment_path = workdir / "enrichment.csv"
        enrichment_path.write_bytes(enrichment)
    return build_network(find_export_dir(workdir / "export"), enrichment_path)


def run_search(
    network: Network, *, kind: str, name: str, ticker: str, use_edgar: bool, use_openalex: bool,
    use_crossref: bool, deep: bool, top: int, limit: int, progress=lambda m: None,
    snapshot_dir: Path | None = None, today: date | None = None,
) -> Result:
    notes: list[str] = []
    target = Target(name=name, kind=kind)
    if kind == "company":
        target.affiliations.append(Affiliation("position", name, normalize_org(name), Period(), ""))
    if snapshot_dir is not None:  # офлайн: готовые снимки SEC EDGAR / OpenAlex, без сети
        from intro_paths.agent.tools import SnapshotEnrichment, ToolError

        try:
            target = SnapshotEnrichment(snapshot_dir).enrich(name, kind, ticker or None)
            notes.append(f"Офлайн-снимок цели: инсайдеров SEC — {len(target.insiders)}, работ — {len(target.works)}")
        except ToolError as exc:
            notes.append(f"Снимка для цели нет, ищу только по месту работы и вузу: {exc}")
        use_edgar = use_openalex = use_crossref = False
    external = use_edgar or use_openalex or use_crossref
    services = build_services(
        use_edgar=use_edgar and kind == "company",
        use_openalex=use_openalex and kind == "person",
        use_crossref=use_crossref and kind == "person",
    ) if external else None
    if services and kind == "company" and services.edgar:
        report = enrich_company(target, services.edgar, query=ticker or name, progress=progress)
        notes.append(f"EDGAR: инсайдеров найдено — {report['insiders']}")
    elif services and kind == "person" and services.publications:
        report = enrich_person(target, services.publications, progress=progress)
        notes.append(f"Публикации: найдено работ — {report['works']}")
    if services and deep and services.publications:
        n = confirm_candidates(network, target, services.publications, top=top, progress=progress)
        notes.append(f"Проверено контактов во внешних источниках: {n}")
    return Result(network, target, find_intro_paths(network, target, today=today, limit=limit), notes)


def paths_to_rows(paths: list[IntroPath]) -> list[dict]:
    return [
        {"#": i, "Контакт": p.connector.full_name, "Сила": p.strength,
         "Сигналы": "; ".join(s.detail for s in p.signals if s.weight > 0),
         "Профиль": p.connector.profile_url}
        for i, p in enumerate(paths, start=1)
    ]


def paths_to_json(paths: list[IntroPath]) -> str:
    payload = [
        {"rank": i, "connector": {"id": p.connector.id, "full_name": p.connector.full_name,
                                  "profile_url": p.connector.profile_url},
         "strength": p.strength,
         "signals": [{"code": s.code, "detail": s.detail, "weight": s.weight,
                      "evidence_url": s.evidence_url} for s in p.signals]}
        for i, p in enumerate(paths, start=1)
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def explain(path: IntroPath, today: date, has_data: bool) -> list[str]:
    """Разбор формулы: noisy-OR по сигналам и множитель тепла."""
    weighted = [s for s in path.signals if s.weight > 0]
    if not weighted:
        return []
    factors = " · ".join(f"(1 − {s.weight:.2f})" for s in weighted)
    base = combine_strength(weighted)
    warm = effective_warmth(path.connector, today, has_data)
    mult = 1.0 if warm is None else 0.5 + 0.5 * warm
    lines = [f"**Сигналы (noisy-OR):** 1 − {factors} = **{base:.2f}**"]
    lines.append("Данных о переписке нет → множитель тепла **1.00**" if warm is None else
                 f"**Тепло связи** {warm:.2f} → множитель 0.5 + 0.5·{warm:.2f} = **{mult:.2f}**")
    lines.append(f"**Итог:** {base:.2f} × {mult:.2f} = **{path.strength:.2f}**")
    return lines


@st.cache_data(show_spinner=False)
def load_demo(key: str):
    network = load_network(DEMO / "network.json")
    target = load_target(DEMO / "targets" / f"{key}.json")
    scenarios_path = DEMO / "scenarios.json"
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8")) if scenarios_path.exists() else {}
    return network, target, scenarios


# --------------------------------------------------------------------------- вкладки

def weights_ui() -> ScoringConfig:
    st.markdown("Меняйте веса — рейтинг на вкладке «Пути» и граф пересчитаются сразу.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.caption("Организации")
        employer = st.slider("Общий работодатель", 0.0, 1.0, DEFAULT.employer, 0.05, key="w_employer")
        school = st.slider("Общий вуз", 0.0, 1.0, DEFAULT.school, 0.05, key="w_school")
        board = st.slider("Общий совет директоров", 0.0, 1.0, DEFAULT.board, 0.05, key="w_board")
    with c2:
        st.caption("Люди")
        director = st.slider("Директор цели (SEC)", 0.0, 1.0, DEFAULT.insider_director, 0.05, key="w_dir")
        officer = st.slider("Офицер цели (SEC)", 0.0, 1.0, DEFAULT.insider_officer, 0.05, key="w_off")
        co_base = st.slider("Соавторство: первая работа", 0.0, 1.0, DEFAULT.coauthor_base, 0.05, key="w_cob")
        co_step = st.slider("Соавторство: +за каждую работу", 0.0, 0.3, DEFAULT.coauthor_step, 0.01, key="w_cos")
    with c3:
        st.caption("Понижающие коэффициенты")
        unknown = st.slider("Период неизвестен", 0.0, 1.0, DEFAULT.unknown_time_factor, 0.05, key="w_unk")
        name_only = st.slider("Совпало только имя", 0.0, 1.0, DEFAULT.name_only_factor, 0.05, key="w_name")
        stale = st.slider("Давнее свидетельство", 0.0, 1.0, DEFAULT.stale_factor, 0.05, key="w_stale")
        hop = st.slider("Скидка за 3-й хоп", 0.0, 1.0, DEFAULT.hop_discount, 0.05, key="w_hop")
    return replace(
        DEFAULT, employer=employer, school=school, board=board, insider_director=director,
        insider_officer=officer, coauthor_base=co_base, coauthor_step=co_step,
        unknown_time_factor=unknown, name_only_factor=name_only, stale_factor=stale, hop_discount=hop,
    )


def tab_overview(network, target, graph, has_data, demo):
    contacts = list(network.contacts.values())
    cols = st.columns(6)
    cols[0].metric("Контактов", len(contacts))
    cols[1].metric("Аффилиаций", sum(len(c.affiliations) for c in contacts))
    cols[2].metric("С перепиской", sum(1 for c in contacts if c.interactions))
    cols[3].metric("Инсайдеров SEC", len(target.insiders))
    cols[4].metric("Работ OpenAlex", len(target.works))
    stats = graph_stats(graph)
    cols[5].metric("Граф: узлы / рёбра", f"{stats['nodes']} / {stats['edges']}")

    st.subheader("Как работает конвейер")
    st.graphviz_chart(
        'digraph { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef3fb", fontname="Helvetica"];'
        ' A [label="1. Импорт\\nконтактов LinkedIn\\n(ZIP/CSV)"]; B [label="2. Обогащение\\nSEC EDGAR · OpenAlex\\nCrossref · enrichment.csv"];'
        ' C [label="3. Граф связей\\nлюди · организации\\nвузы · работы"]; D [label="4. Пути\\nвладелец → контакт → цель\\n+ через знакомого"];'
        ' E [label="5. Ранжирование\\nсила = noisy-OR сигналов\\n× тепло связи"]; A -> B -> C -> D -> E; }'
    )
    st.subheader("Цели проекта и их состояние")
    st.dataframe([
        {"Цель": "Импорт контактов из LinkedIn", "Статус": "✅", "Как": "ZIP экспорта; Connections/Positions/Education/Messages/Invitations", "Ограничение": "API LinkedIn не используется (ToS)"},
        {"Цель": "Обогащение профилей", "Статус": "✅", "Как": "SEC EDGAR, OpenAlex, Crossref, enrichment.csv", "Ограничение": "EDGAR — только компании США; Google Scholar не подключён"},
        {"Цель": "Граф связей", "Статус": "✅", "Как": "вкладка «Граф»", "Ограничение": "связи контакт↔контакт выведены, а не подтверждены"},
        {"Цель": "Короткие и сильные пути", "Статус": "✅", "Как": "2 хопа — по сигналам; 3 хопа — через знакомого знакомого", "Ограничение": "3-й хоп — оценка со скидкой"},
        {"Цель": "Сигналы: работодатель, вуз, совет, соавторство, взаимодействия", "Статус": "✅", "Как": "вкладка «Как считается»", "Ограничение": "совпадение людей по имени ненадёжно (×0.5 без подтверждения)"},
        {"Цель": "Оценка силы и перечень сигналов", "Статус": "✅", "Как": "вкладка «Пути»: формула с подстановкой", "Ограничение": "веса стартовые, не калибрированы"},
        {"Цель": "Интерфейс загрузки контактов и цели", "Статус": "✅", "Как": "боковая панель", "Ограничение": "хранение данных локальное"},
    ], hide_index=True, width="stretch")
    if demo:
        st.caption("В демо-режиме внешние сервисы не вызываются: данные берутся из `data/demo/`.")


def tab_data(network, target, scenarios):
    st.subheader("Контакты")
    rows = []
    for c in network.contacts.values():
        row = {
            "Контакт": c.full_name,
            "Работа": ", ".join(dict.fromkeys(a.name for a in c.affiliations if a.kind == "position")),
            "Вуз": ", ".join(dict.fromkeys(a.name for a in c.affiliations if a.kind == "education")),
            "Сообщений": sum(i.count for i in c.interactions if i.kind == "message"),
            "Работ": len(c.works),
        }
        if scenarios:
            row["Сценарий"] = scenarios.get(c.id, {}).get("tag", "")
        rows.append(row)
    st.dataframe(rows, hide_index=True, width="stretch")
    st.subheader(f"Инсайдеры целевой компании (SEC EDGAR): {len(target.insiders)}")
    if target.insiders:
        st.dataframe([
            {"Имя": i.name, "Роль": ("директор" if i.is_director else "") + (" офицер" if i.is_officer else ""),
             "Должность": i.title, "Впервые": i.first_seen, "Последний раз": i.last_seen, "Документ": i.source_url}
            for i in target.insiders
        ], hide_index=True, width="stretch",
            column_config={"Документ": st.column_config.LinkColumn(display_text="SEC")})
    else:
        st.caption("Нет данных: цель — не компания или EDGAR не подключён.")
    st.subheader(f"Работы цели (OpenAlex/Crossref): {len(target.works)}")
    if target.works:
        st.dataframe([
            {"Год": w.year, "Название": w.title, "Соавторов": len(w.coauthors), "Ссылка": w.url}
            for w in target.works
        ], hide_index=True, width="stretch",
            column_config={"Ссылка": st.column_config.LinkColumn(display_text="OpenAlex")})
    else:
        st.caption("Нет данных: цель — не человек или источники публикаций не подключены.")


def tab_graph(network, target, paths, chains, cfg, today):
    st.markdown("**Как читать граф.** Вы в центре, вокруг — контакты. Организации, вузы и работы "
                "соединяют людей; красным выделены топ-пути к цели. Пунктир — выведенная, "
                "не подтверждённая связь.")
    c1, c2, c3, c4 = st.columns(4)
    show_orgs = c1.checkbox("Организации", True)
    show_schools = c1.checkbox("Вузы", True)
    show_works = c2.checkbox("Работы (OpenAlex)", True)
    show_insiders = c2.checkbox("Инсайдеры SEC", True)
    inferred = c3.checkbox("Выведенные связи контактов", False)
    show_chains = c3.checkbox("Мосты (3-й хоп)", True)
    only_top = c4.checkbox("Только топ-пути", False)
    top_n = c4.number_input("Топ путей в подсветке", 1, 20, min(5, max(1, len(paths))))
    c5, c6 = st.columns(2)
    min_weight = c5.slider("Минимальный вес ребра", 0.0, 1.0, 0.0, 0.05)
    search = c6.text_input("Найти узел (подсветится)")

    graph = build_graph(
        network, target, paths[: int(top_n)], chains[:5] if show_chains else None,
        show_orgs=show_orgs, show_schools=show_schools, show_works=show_works,
        show_insiders=show_insiders, inferred=inferred, today=today, cfg=cfg,
    )
    html = render_graph_html(graph, min_weight=min_weight, search=search, only_highlight=only_top)
    if hasattr(st, "iframe"):  # components.v1.html объявлен устаревшим
        st.iframe(html, height=700)
    else:
        components.html(html, height=700, scrolling=False)
    st.markdown(" &nbsp; ".join(
        f"<span style='color:{color}'>●</span> {label}" for label, color, _ in legend()),
        unsafe_allow_html=True)

    stats = graph_stats(graph)
    a, b, c = st.columns(3)
    a.metric("Узлов", stats["nodes"])
    b.metric("Рёбер", stats["edges"])
    c.metric("Компонент связности", stats["components"])
    d1, d2 = st.columns(2)
    d1.caption("Узлы по типам")
    d1.dataframe([{"Тип": k, "Число": v} for k, v in stats["nodes_by_type"].items()], hide_index=True)
    d2.caption("Рёбра по видам")
    d2.dataframe([{"Вид": k, "Число": v} for k, v in stats["edges_by_kind"].items()], hide_index=True)
    st.caption("Самые связанные контакты: " + ", ".join(f"{n} ({d})" for n, d in stats["top_contacts"]))

    st.subheader("Инспектор узла")
    labelled = {f"{n.label} · {n.type}": n.id for n in graph.nodes.values()}
    pick = st.selectbox("Выберите узел", sorted(labelled))
    if pick:
        node_id = labelled[pick]
        rows = [{"Связь": e.kind, "С кем": graph.nodes[other].label, "Вес": round(e.weight, 2),
                 "Описание": e.label, "Выведено": e.inferred, "Источник": e.evidence_url}
                for e, other in graph.neighbors(node_id)]
        st.dataframe(rows, hide_index=True, width="stretch",
                     column_config={"Источник": st.column_config.LinkColumn(display_text="открыть")})


def tab_paths(network, target, paths, chains, today, has_data, key):
    if not paths:
        st.warning("Путей не найдено. Добавьте источники или enrichment.csv с историей работы контактов.")
    else:
        st.dataframe(paths_to_rows(paths), hide_index=True, width="stretch", column_config={
            "Сила": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
            "Профиль": st.column_config.LinkColumn(display_text="LinkedIn"),
        })
        for i, p in enumerate(paths, start=1):
            with st.expander(f"{i}. {p.connector.full_name} — сила {p.strength:.2f}"):
                st.markdown(f"Вы → **{p.connector.full_name}** → **{target.name}**")
                for s in p.signals:
                    if s.weight > 0:
                        line = f"- {s.detail} — вес **{s.weight:.2f}**"
                        st.markdown(f"{line} · [источник]({s.evidence_url})" if s.evidence_url else line)
                    else:
                        st.markdown(f"- {s.detail}")
                for line in explain(p, today, has_data):
                    st.markdown(line)
        st.download_button("Скачать JSON", paths_to_json(paths), "intro_paths.json", "application/json", key=f"dl_{key}")
    st.subheader("Через знакомого знакомого (3 хопа, оценка)")
    st.caption("Контакт связан с человеком вокруг цели (директор SEC или соавтор), которого нет в вашей сети. "
               "Связь выведена по общей организации — не подтверждена.")
    if chains:
        st.dataframe([
            {"Через": c.via.full_name, "Мост": c.bridge_name, "Роль моста": c.bridge_role,
             "Сила": c.strength, "Связь контакт–мост": c.link, "Почему": c.link_detail}
            for c in chains
        ], hide_index=True, width="stretch",
            column_config={"Сила": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f")})
    else:
        st.caption("Таких цепочек нет.")


def tab_how(cfg, network, target, today, limit, paths):
    st.subheader("Что изменилось относительно весов по умолчанию")
    base = find_intro_paths(network, target, today=today, limit=limit, cfg=DEFAULT)
    base_rank = {p.connector.id: i for i, p in enumerate(base, start=1)}
    rows = []
    for i, p in enumerate(paths, start=1):
        old = base_rank.get(p.connector.id)
        rows.append({"Контакт": p.connector.full_name, "Сейчас": i, "По умолчанию": old or "—",
                     "Сдвиг": "новый" if old is None else (old - i)})
    st.dataframe(rows, hide_index=True, width="stretch")
    st.markdown(
        "**Формула.** Сила пути = (1 − Π(1 − wᵢ)) × (0.5 + 0.5 · тепло). "
        "Сигналы независимы и усиливают друг друга, но не складываются линейно. "
        "Тепло — доля переписки и приглашений; если в экспорте есть переписка, а у контакта её нет, "
        "тепло = 0. Для 3-хоповых цепочек: сила(мост→цель) × сила(контакт–мост) × скидка × тепло.")
    st.caption("Веса стартовые и не калибрированы на размеченных кейсах.")


def tab_sources(demo_key, network, target):
    st.markdown(
        "**Что уходит во внешние сервисы.** Только цель (название компании или имя человека) — в SEC EDGAR, "
        "OpenAlex и Crossref. Имена контактов не отправляются, пока вы не включите глубокий режим "
        "(`--deep`): тогда топ-N совпавших контактов проверяются по OpenAlex/Crossref.\n\n"
        "**Не используется:** API LinkedIn и скрейпинг; Google Scholar (нет API, закрыт в robots.txt).\n\n"
        "**Хранение.** Данные локальные; внешние ответы кэшируются в `data/.cache/` на 24 часа. "
        "Для реальных пользователей нужны шифрование и политика хранения (152-ФЗ/GDPR).")
    if demo_key is None:
        return
    st.subheader("Обновить цель из живых источников")
    st.caption("Требуется `SEC_USER_AGENT` в `.env` для EDGAR. Результат заменит снимок цели в этой сессии.")
    if st.button("Обновить из SEC EDGAR / OpenAlex", key="live_refresh"):
        load_env()
        try:
            fresh = Target(name=target.name, affiliations=list(target.affiliations), kind=target.kind)
            if target.kind == "company":
                services = build_services(use_openalex=False, use_crossref=False)
                report = enrich_company(fresh, services.edgar, query="AAPL" if "Apple" in target.name else target.name)
                st.success(f"EDGAR: инсайдеров {report['insiders']}")
            else:
                services = build_services(use_edgar=False)
                report = enrich_person(fresh, services.publications)
                st.success(f"Работ: {report['works']}")
            st.session_state[f"target_{demo_key}"] = fresh
            st.rerun()
        except Exception as exc:
            st.error(f"Не удалось обновить: {exc}")
    if f"target_{demo_key}" in st.session_state and st.button("Вернуть снимок", key="live_reset"):
        del st.session_state[f"target_{demo_key}"]
        st.rerun()


# ------------------------------------------------------------------------------- main

def sidebar():
    st.header("1. Набор данных")
    dataset = st.selectbox("Источник контактов", [DS_APPLE, DS_HINTON, DS_MINE])
    limit = st.number_input("Сколько путей показать", 1, 100, 10)
    mine = None
    if dataset == DS_MINE:
        st.caption("Нет своего экспорта? Скачайте синтетические файлы и загрузите их ниже — "
                   "или поставьте галочку «встроенный демо-ZIP».")
        if DEMO_ZIP.exists():
            c1, c2 = st.columns(2)
            c1.download_button("Демо-ZIP", DEMO_ZIP.read_bytes(), DEMO_ZIP.name, "application/zip", key="dl_zip")
            if DEMO_ENRICHMENT.exists():
                c2.download_button("enrichment.csv", DEMO_ENRICHMENT.read_bytes(), "enrichment_demo.csv",
                                   "text/csv", key="dl_enr")
        use_demo = st.checkbox("Использовать встроенный демо-ZIP (синтетика)", disabled=not DEMO_ZIP.exists())
        upload = st.file_uploader("ZIP экспорта LinkedIn", type="zip", disabled=use_demo)
        enrichment = st.file_uploader("enrichment.csv (необязательно)", type="csv")
        st.header("2. Цель")
        kind_label = st.radio("Тип цели", ["Компания", "Человек"], horizontal=True)
        kind = "company" if kind_label == "Компания" else "person"
        name = st.text_input("Название компании" if kind == "company" else "Имя человека",
                             help="Для демо: Apple Inc (компания) или Geoffrey Hinton (человек)")
        ticker = st.text_input("Тикер (необязательно)") if kind == "company" else ""
        st.header("3. Источники")
        offline = st.checkbox("Офлайн-снимки SEC/OpenAlex (демо: Apple Inc, Geoffrey Hinton)",
                              disabled=not DEMO_TARGETS.exists())
        st.caption("Имя цели уходит во внешние сервисы. Контакты — только в глубоком режиме.")
        use_edgar = st.checkbox("SEC EDGAR", disabled=kind != "company" or offline)
        use_openalex = st.checkbox("OpenAlex", disabled=kind != "person" or offline)
        use_crossref = st.checkbox("Crossref", disabled=kind != "person" or offline)
        deep = st.checkbox("Глубокий режим: проверить топ-N контактов", disabled=offline)
        top = st.number_input("N", 1, 100, 20, disabled=not deep)
        go = st.button("Найти пути", type="primary", disabled=not (name.strip() and (upload or use_demo)))
        mine = dict(upload=upload, enrichment=enrichment, kind=kind, name=name.strip(), ticker=ticker.strip(),
                    use_edgar=use_edgar, use_openalex=use_openalex, use_crossref=use_crossref,
                    deep=deep, top=int(top), go=go, use_demo=use_demo, offline=offline)
    return dataset, int(limit), mine


def main() -> None:
    load_env()
    st.set_page_config(page_title="Intro-Path Discovery", layout="wide")
    st.title("Intro-Path Discovery")
    st.caption("Кто из ваших контактов может представить вас нужной компании или человеку — и почему.")

    with st.sidebar:
        dataset, limit, mine = sidebar()

    demo_key = DEMO_FILES.get(dataset)
    scenarios: dict = {}
    if demo_key:
        network, target, scenarios = load_demo(demo_key)
        target = st.session_state.get(f"target_{demo_key}", target)
        today = DEMO_TODAY
        st.warning("СИНТЕТИЧЕСКИЕ ДАННЫЕ: связи, должности и переписка контактов вымышлены. "
                   "Имена публичных людей взяты из SEC/OpenAlex только для сопоставления. "
                   "См. data/demo/DISCLAIMER.md.")
    else:
        if mine["go"]:
            try:
                if mine["use_demo"]:
                    zip_bytes = DEMO_ZIP.read_bytes()
                    enrichment_bytes = (mine["enrichment"].getvalue() if mine["enrichment"]
                                        else DEMO_ENRICHMENT.read_bytes())
                else:
                    zip_bytes = mine["upload"].getvalue()
                    enrichment_bytes = mine["enrichment"].getvalue() if mine["enrichment"] else None
                with tempfile.TemporaryDirectory() as tmp:
                    net = load_network_from_zip(zip_bytes, enrichment_bytes, Path(tmp))
                status = st.status("Ищу пути…", expanded=False)
                today_mine = DEMO_TODAY if mine["use_demo"] else date.today()
                res = run_search(net, kind=mine["kind"], name=mine["name"], ticker=mine["ticker"],
                                 use_edgar=mine["use_edgar"], use_openalex=mine["use_openalex"],
                                 use_crossref=mine["use_crossref"], deep=mine["deep"], top=mine["top"],
                                 limit=limit, progress=status.write, today=today_mine,
                                 snapshot_dir=DEMO_TARGETS if mine["offline"] else None)
                status.update(label="Готово", state="complete")
                st.session_state["mine"] = (res.network, res.target, res.notes, today_mine, mine["use_demo"])
            except (ValueError, FileNotFoundError, zipfile.BadZipFile) as exc:
                st.error(f"Ошибка: {exc}")
                return
            except Exception as exc:  # сеть и внешние API
                st.error(f"Не удалось обратиться к внешнему источнику: {exc}")
                return
        if "mine" not in st.session_state:
            st.info("Загрузите ZIP экспорта LinkedIn, укажите цель и нажмите «Найти пути». "
                    "Или выберите синтетический набор в боковой панели.")
            return
        network, target, notes, today, synthetic = st.session_state["mine"]
        if synthetic:
            st.warning("СИНТЕТИЧЕСКИЕ ДАННЫЕ: демо-ZIP вымышлен (см. data/demo/DISCLAIMER.md).")
        for note in notes:
            st.caption(note)

    names = ["Обзор", "Данные", "Граф", "Пути", "Как считается", "Источники и приватность"]
    tabs = st.tabs(names)
    with tabs[4]:  # веса читаем первыми: от них зависят остальные вкладки
        cfg = weights_ui()
    has_data = network_has_interactions(network.contacts.values())
    paths = find_intro_paths(network, target, today=today, limit=limit, cfg=cfg)
    chains = find_chain_paths(network, target, today=today, cfg=cfg, limit=10)
    overview_graph = build_graph(network, target, paths[:5], chains[:5], today=today, cfg=cfg)

    with tabs[0]:
        tab_overview(network, target, overview_graph, has_data, demo_key is not None)
    with tabs[1]:
        tab_data(network, target, scenarios)
    with tabs[2]:
        tab_graph(network, target, paths, chains, cfg, today)
    with tabs[3]:
        tab_paths(network, target, paths, chains, today, has_data, dataset)
    with tabs[4]:
        tab_how(cfg, network, target, today, limit, paths)
    with tabs[5]:
        tab_sources(demo_key, network, target)


if __name__ == "__main__":
    main()
