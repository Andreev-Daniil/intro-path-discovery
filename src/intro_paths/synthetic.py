"""Демо-экспорт LinkedIn, согласованный с публичными данными SEC EDGAR и OpenAlex.

ВСЁ, ЧТО СВЯЗАНО С ЛЮДЬМИ В ЭТОМ НАБОРЕ, ВЫМЫШЛЕНО. Имена публичных людей (директоры Apple,
соавторы Джеффри Хинтона) взяты из открытых снимков только затем, чтобы сопоставление с
живыми данными срабатывало. Должности, связи, переписка, даты — генерируются и не
отражают реальность. URL профилей — `/in/demo-…`, настоящих профилей по ним нет.

Генератор детерминирован: один и тот же seed и снимки дают одинаковые файлы.
"""

from __future__ import annotations

import csv
import json
import random
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .models import Affiliation, Coauthor, Insider, Period, Target, Work
from .names import is_same_person
from .network import build_network
from .normalize import normalize_org
from .store import load_target, save_network, save_target

TODAY = date(2026, 11, 15)
OWNER = ("Alex", "Demo")

DISCLAIMER = """# ДЕМО-ДАННЫЕ

Этот набор создан генератором `intro-paths synth` для демонстрации и тестов.

- Все связи, должности, места учёбы, сообщения и приглашения **вымышлены**.
- Имена директоров/офицеров Apple и соавторов Geoffrey Hinton взяты из открытых источников
  (SEC EDGAR, OpenAlex) только для проверки сопоставления с живыми данными.
- У реальных людей подставлены вымышленные работодатели (Northwind Capital, Contoso Labs,
  Tailspin Toys), чтобы не утверждать фактов о них.
- Цели Northwind Capital, Contoso Labs, Elena Smirnova и Globex Corporation, их сотрудники,
  директора, статьи и соавторы вымышлены полностью (названия компаний взяты из учебных примеров).
- Ссылки на профили имеют вид `https://www.linkedin.com/in/demo-…` и не ведут на реальные профили.
- Не публикуйте этот набор как реальную сеть знакомств.
"""

FIRST = ["Mia", "Noah", "Liam", "Emma", "Oliver", "Ava", "Lucas", "Zoe", "Ethan", "Nora",
         "Leo", "Iris", "Max", "Ruby", "Finn", "Cora", "Theo", "Lena", "Hugo", "Maya",
         "Owen", "Ella", "Jack", "Ada", "Sam", "Eva", "Ian", "Uma", "Kai", "Lia"]
LAST = ["Ashford", "Bellamy", "Calloway", "Dunmore", "Eastwood", "Fairchild", "Galloway",
        "Hargrove", "Ingram", "Jessup", "Kensington", "Lockhart", "Marlowe", "Northcott",
        "Oakley", "Pemberton", "Quill", "Radcliffe", "Sterling", "Thornton", "Underhill",
        "Vandermeer", "Whitlock", "Yarrow", "Zeller"]

# (работодатель, сколько вымышленных людей)
EMPLOYERS = [("Apple Inc", 6), ("Google", 8), ("Meta", 5), ("University of Toronto", 7),
             ("Vector Institute", 6), ("MIT", 3), ("Northwind Capital", 6),
             ("Contoso Labs", 5), ("Tailspin Toys", 2), ("Fabrikam", 1), ("Litware", 1)]
SCHOOLS = ["University of Toronto", "MIT", "Stanford University", "Higher School of Economics"]

APPLE_OFFICERS = [("Kevan", "Parekh", "CFO", 6), ("Sabih", "Khan", "COO", 2),
                  ("Jennifer", "Newstead", "General Counsel", 0), ("Deirdre", "O'Brien", "SVP", 3),
                  ("Timothy", "Cook", "Executive Chair", 0), ("John", "Ternus", "CEO", 8)]
APPLE_DIRECTORS = [("Arthur", "Levinson", "Northwind Capital", 4), ("Susan", "Wagner", "Contoso Labs", 0),
                   ("Andrea", "Jung", "Contoso Labs", 1), ("Alex", "Gorsky", "Northwind Capital", 0)]
# (имя, фамилия, сценарий, место, сообщений, прикрепить общие работы)
HINTON_CAST = [
    ("Ilya", "Sutskever", "edu:University of Toronto", 5, False),
    ("Alex", "Krizhevsky", "edu:University of Toronto", 0, False),
    ("Zoubin", "Ghahramani", "edu:University of Toronto", 2, False),
    ("Yoshua", "Bengio", "edu:Université de Montréal", 1, False),
    ("Ruslan", "Salakhutdinov", "job:Northwind Capital", 3, True),
    ("George", "Dahl", "edu:University of Toronto", 0, True),
    ("Abdelrahman", "Mohamed", "job:Contoso Labs", 0, False),
]


def _slug(*parts: str) -> str:
    text = "-".join(parts).lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


@dataclass
class Spec:
    first: str
    last: str
    company: str
    position: str
    tag: str
    note: str
    connected: date
    messages: int = 0
    last_msg: date | None = None
    invited: bool = True
    history: list[tuple] = field(default_factory=list)  # (kind, name, start, end, role)
    attach_works: bool = False
    works: list[Work] = field(default_factory=list)  # явно приложенные общие работы (как после deep)
    slug: str = ""


def _fmt(d: date | None, pattern: str) -> str:
    return d.strftime(pattern) if d else ""


def _snapshot(path: Path):
    if not Path(path).exists():
        raise FileNotFoundError(f"нет снимка {path}: выполните enrich-target и сохраните в data/demo/snapshots")
    return load_target(path)


def _require(names: list[str], pairs: list[tuple[str, str]], what: str) -> None:
    for first, last in pairs:
        if not any(is_same_person(f"{first} {last}", n) for n in names):
            raise ValueError(f"{what}: {first} {last} нет в снимке — обновите снимок или состав")


def _specs(rnd: random.Random, apple, hinton) -> list[Spec]:
    specs: list[Spec] = []
    real_last = {x[1].lower() for x in [*APPLE_OFFICERS, *APPLE_DIRECTORS]}
    real_last |= {x[1].lower() for x in HINTON_CAST} | {"khan"}

    def when(year_lo=2015, year_hi=2024) -> date:
        return date(rnd.randint(year_lo, year_hi), rnd.randint(1, 12), rnd.randint(1, 28))

    def warm(n: int) -> date | None:
        return date(2026, rnd.randint(7, 11), rnd.randint(1, 14)) if n >= 3 else (
            date(2024, rnd.randint(1, 12), rnd.randint(1, 28)) if n else None)

    insider_names = [i.name for i in apple.insiders]
    _require(insider_names, [(f, l) for f, l, *_ in APPLE_OFFICERS], "Apple")
    _require(insider_names, [(f, l) for f, l, *_ in APPLE_DIRECTORS], "Apple")
    coauthors = [c.name for w in hinton.works for c in w.coauthors]
    _require(coauthors, [(f, l) for f, l, *_ in HINTON_CAST], "Hinton")

    for first, last, title, msgs in APPLE_OFFICERS:
        specs.append(Spec(first, last, "Apple Inc", title, "insider_corroborated",
                          "офицер/директор Apple; место работы подтверждает совпадение имени",
                          when(2018, 2022), msgs, warm(msgs),
                          history=[("position", "Apple Inc", date(2012, 1, 1), None, title)]))
    for first, last, employer, msgs in APPLE_DIRECTORS:
        specs.append(Spec(first, last, employer, "Partner", "insider_name_only",
                          "директор Apple, но вымышленный работодатель: совпало только имя (×0.5)",
                          when(2016, 2022), msgs, warm(msgs),
                          history=[("position", employer, date(2014, 1, 1), None, "Partner")]))
    specs.append(Spec("Sabih", "Khan", "Tailspin Toys", "Engineer", "homonym",
                      "однофамилец реального офицера: ложное совпадение по имени",
                      when(2019, 2023), 0, None,
                      history=[("position", "Tailspin Toys", date(2019, 1, 1), None, "Engineer")]))

    for first, last, where, msgs, attach in HINTON_CAST:
        kind, _, place = where.partition(":")
        if kind == "edu":
            spec = Spec(first, last, "Vector Institute" if msgs else "Northwind Capital", "Researcher",
                        "coauthor_confirmed_institution" if not attach else "coauthor_confirmed_works",
                        f"соавтор Хинтона; подтверждён вузом {place}", when(2017, 2023), msgs, warm(msgs),
                        history=[("education", place, date(2004, 9, 1), date(2010, 6, 1), "PhD")],
                        attach_works=attach)
        else:
            spec = Spec(first, last, place, "Consultant",
                        "coauthor_confirmed_works" if attach else "coauthor_name_only",
                        "соавтор Хинтона; подтверждён общими работами (deep)" if attach
                        else "соавтор Хинтона без подтверждения: только имя (×0.5)",
                        when(2017, 2023), msgs, warm(msgs),
                        history=[("position", place, date(2015, 1, 1), None, "Consultant")],
                        attach_works=attach)
        specs.append(spec)

    used: set[tuple[str, str]] = set()
    for employer, count in EMPLOYERS:
        for _ in range(count):
            while True:
                first, last = rnd.choice(FIRST), rnd.choice(LAST)
                if (first, last) not in used and last.lower() not in real_last:
                    used.add((first, last))
                    break
            start = rnd.randint(2009, 2022)
            history = [("position", employer, date(start, rnd.randint(1, 12), 1), None, "Staff")]
            if rnd.random() < 0.6:
                school = rnd.choice(SCHOOLS)
                ys = rnd.randint(2003, 2014)
                history.append(("education", school, date(ys, 9, 1), date(ys + 4, 6, 1), "BSc"))
            if rnd.random() < 0.4:
                history.append(("position", rnd.choice([e for e, _ in EMPLOYERS]),
                                date(start - 4, 1, 1), date(start - 1, 12, 1), "Staff"))
            msgs = rnd.choice([0, 0, 0, 1, 2, 4, 6, 9])
            specs.append(Spec(first, last, employer, "Staff", "filler",
                              "вымышленный контакт для плотности графа", when(), msgs, warm(msgs),
                              invited=rnd.random() < 0.75, history=history))
    return specs


def _assign_slugs(specs: list[Spec]) -> None:
    seen: dict[str, int] = {}
    for s in specs:
        base = _slug(s.first, s.last)
        seen[base] = seen.get(base, 0) + 1
        s.slug = f"demo-{base}" + (f"-{seen[base]}" if seen[base] > 1 else "")


# ----------------------------------------------------------------------------------------
# Дополнение: «очевидные» сценарии на полностью вымышленных целях и больше контактов.
# Отдельный генератор случайных чисел — результаты Apple и Hinton не сдвигаются.

NEW_FIRST = ["Anton", "Bella", "Carlos", "Dina", "Egor", "Fatima", "Gleb", "Hana", "Igor", "Julia",
             "Karim", "Lara", "Misha", "Nina", "Oscar", "Polina", "Ravil", "Sofia", "Timur", "Ulla",
             "Vera", "Wei", "Xenia", "Yusuf", "Zara"]
NEW_LAST = ["Abramov", "Castillo", "Dragan", "Ek", "Fedorov", "Gruber", "Haddad", "Ivanchenko",
            "Jovanovic", "Kuznetsov", "Lund", "Mironov", "Nakamura", "Osei", "Petrenko", "Rahimi",
            "Sandoval", "Tanaka", "Urban", "Volkov", "Wojcik", "Yamada", "Zielinski"]
NEW_EMPLOYERS = [("Adventure Works", 7), ("Wingtip Toys", 5), ("Alpine Ski House", 5), ("Proseware", 4),
                 ("Woodgrove Bank", 5), ("Lucerne Publishing", 4), ("Northwind Capital", 3),
                 ("Contoso Labs", 3), ("Fabrikam", 3)]
ALL_SCHOOLS = [*SCHOOLS, "Université de Montréal", "Technical University of Munich"]

# Сценарии, которые можно проверить на глаз. Таблица самопроверяемая: тест сверяет её с движком.
DEMO_SCENARIOS = [
    {"id": "apple", "title": "Офицер Apple, с которым вы переписываетесь", "target": "Apple Inc", "kind": "company",
     "what": "директор или офицер из SEC и общий работодатель; переписка поднимает силу",
     "top": "John Ternus", "codes": ["insider_director", "same_employer"]},
    {"id": "hinton", "title": "Соавтор и общий работодатель", "target": "Geoffrey Hinton", "kind": "person",
     "what": "совместные работы из OpenAlex плюс общее место работы", "in_top": ("Ilya Sutskever", 3),
     "codes": ["coauthor", "same_employer"]},
    {"id": "northwind", "title": "Нынешние и бывшие коллеги", "target": "Northwind Capital", "kind": "company",
     "what": "руководитель компании в ваших контактах, коллеги из прошлого и настоящего",
     "top": "Marina Kovalenko", "codes": ["insider_officer", "same_employer"]},
    {"id": "contoso", "title": "Член совета директоров", "target": "Contoso Labs", "kind": "company",
     "what": "контакт сидит в совете директоров цели — самый весомый сигнал (0.9)",
     "top": "Ravi Menon", "codes": ["same_board"]},
    {"id": "smirnova", "title": "Однокурсница, коллега и соавтор", "target": "Elena Smirnova", "kind": "person",
     "what": "общий вуз и общие статьи: несколько сигналов складываются", "top": "Anna Orlova",
     "codes": ["same_school", "coauthor"]},
    {"id": "globex", "title": "Путей нет", "target": "Globex Corporation", "kind": "company",
     "what": "ни у кого из контактов нет связи с целью: система честно говорит «ничего не найдено»",
     "found": 0, "codes": []},
]


def _named(first, last, company, position, tag, note, msgs, last_msg, connected, history, **kw) -> Spec:
    return Spec(first, last, company, position, tag, note, connected, msgs, last_msg, history=history, **kw)


def _extra_specs(rnd: random.Random, existing: list[Spec]) -> tuple[list[Spec], dict[str, Target]]:
    """Очевидные сценарии на вымышленных целях + дополнительные контакты для плотности графа."""
    def d(y, m=1, day=1) -> date:
        return date(y, m, day)

    recent = d(2026, 11, 10)
    stale = d(2024, 5, 20)
    toronto = ("education", "University of Toronto", d(2010, 9, 1), d(2014, 6, 1), "BSc")
    specs: list[Spec] = [
        # --- Northwind Capital: руководитель, нынешние и бывшие коллеги ---
        _named("Marina", "Kovalenko", "Northwind Capital", "CEO", "obvious_insider_current",
               "руководитель цели и ваш контакт: место работы подтверждает имя", 7, recent, d(2019, 3, 14),
               [("position", "Northwind Capital", d(2012, 1, 1), None, "CEO")]),
        _named("Dmitri", "Sokolov", "Northwind Capital", "Analyst", "obvious_current_colleague",
               "работает в цели сейчас, регулярно переписываетесь", 9, recent, d(2020, 6, 2),
               [("position", "Northwind Capital", d(2019, 1, 1), None, "Analyst")]),
        _named("Olga", "Volkova", "Adventure Works", "Product Lead", "obvious_former_colleague",
               "работала в цели в те же годы, что и вы; сейчас в другой компании", 6, recent, d(2021, 2, 11),
               [("position", "Northwind Capital", d(2016, 1, 1), d(2020, 12, 1), "Analyst"),
                ("position", "Adventure Works", d(2021, 1, 1), None, "Product Lead")]),
        _named("Sven", "Aldrich", "Northwind Capital", "Associate", "obvious_cold_colleague",
               "работает в цели, но вы почти не общались: сила ниже из-за холодной связи", 0, None, d(2018, 9, 5),
               [("position", "Northwind Capital", d(2017, 1, 1), None, "Associate")]),
        _named("Irina", "Belova", "Adventure Works", "Consultant", "obvious_name_only",
               "тёзка директора цели: совпало только имя, вес ×0.5", 3, d(2026, 8, 3), d(2022, 4, 18),
               [("position", "Adventure Works", d(2020, 1, 1), None, "Consultant")]),
        # --- Contoso Labs: совет директоров ---
        _named("Ravi", "Menon", "Woodgrove Bank", "Managing Director", "obvious_board_member",
               "сидит в совете директоров цели: самый сильный сигнал (0.9)", 8, recent, d(2017, 5, 9),
               [("position", "Woodgrove Bank", d(2014, 1, 1), None, "Managing Director"),
                ("board", "Contoso Labs", d(2019, 1, 1), None, "Член совета директоров")]),
        _named("Hannah", "Brooks", "Lucerne Publishing", "Editor", "obvious_former_board",
               "была в совете директоров цели в 2013–2017", 4, d(2026, 9, 1), d(2018, 7, 21),
               [("position", "Lucerne Publishing", d(2017, 6, 1), None, "Editor"),
                ("board", "Contoso Labs", d(2013, 1, 1), d(2017, 12, 1), "Член совета директоров")]),
        # --- Elena Smirnova: однокурсники, коллеги, соавторы ---
        _named("Anna", "Orlova", "Alpine Ski House", "Data Scientist", "obvious_classmate_coauthor",
               "училась с целью в Торонто и написала с ней две статьи", 8, recent, d(2015, 10, 3),
               [toronto, ("position", "Alpine Ski House", d(2019, 1, 1), None, "Data Scientist")]),
        _named("Victor", "Hale", "Contoso Labs", "Researcher", "obvious_colleague_coauthor",
               "коллега цели в Contoso Labs и соавтор; общие работы приложены (как после deep)", 4, d(2026, 9, 9),
               d(2019, 11, 4), [("position", "Contoso Labs", d(2018, 6, 1), None, "Researcher")],
               works=[Work("DEMO-W1", "Ranking introduction paths in professional networks", 2023),
                      Work("DEMO-W3", "Warm introductions at scale", 2022)]),
        _named("Priya", "Nair", "Proseware", "ML Engineer", "obvious_classmate_coauthor",
               "однокурсница цели и соавтор одной статьи", 2, stale, d(2016, 2, 8),
               [toronto, ("position", "Proseware", d(2018, 1, 1), None, "ML Engineer")]),
        _named("Leo", "Brandt", "Wingtip Toys", "Analyst", "obvious_coauthor_name_only",
               "соавтор из списка статей, но места работы и вуза не совпадают: только имя, ×0.5", 1, stale,
               d(2021, 8, 30), [("position", "Wingtip Toys", d(2020, 1, 1), None, "Analyst")]),
        _named("Katya", "Morozova", "Woodgrove Bank", "Risk Analyst", "obvious_classmate",
               "однокурсница цели: общий вуз и годы учёбы", 5, d(2026, 10, 2), d(2016, 9, 12),
               [toronto, ("position", "Woodgrove Bank", d(2016, 1, 1), None, "Risk Analyst")]),
        _named("Timur", "Haddad", "Adventure Works", "Engineer", "obvious_classmate",
               "однокурсник цели", 0, None, d(2017, 3, 3),
               [toronto, ("position", "Adventure Works", d(2015, 1, 1), None, "Engineer")]),
    ]
    people = {(s.first, s.last) for s in [*existing, *specs]}
    for employer, count in NEW_EMPLOYERS:
        for _ in range(count):
            while True:
                first, last = rnd.choice(NEW_FIRST), rnd.choice(NEW_LAST)
                if (first, last) not in people:
                    people.add((first, last))
                    break
            start = rnd.randint(2010, 2023)
            history = [("position", employer, d(start, rnd.randint(1, 12), 1), None, "Staff")]
            if rnd.random() < 0.55:
                school = rnd.choice(ALL_SCHOOLS)
                ys = rnd.randint(2003, 2016)
                history.append(("education", school, d(ys, 9, 1), d(ys + 4, 6, 1), "BSc"))
            if rnd.random() < 0.35:
                history.append(("position", rnd.choice([e for e, _ in NEW_EMPLOYERS]),
                                d(start - 4, 1, 1), d(start - 1, 12, 1), "Staff"))
            msgs = rnd.choice([0, 0, 0, 1, 2, 4, 6, 9])
            last_msg = (date(2026, rnd.randint(7, 11), rnd.randint(1, 14)) if msgs >= 3
                        else date(2024, rnd.randint(1, 12), rnd.randint(1, 28)) if msgs else None)
            specs.append(Spec(first, last, employer, "Staff", "filler",
                              "вымышленный контакт для плотности графа",
                              date(rnd.randint(2015, 2024), rnd.randint(1, 12), rnd.randint(1, 28)),
                              msgs, last_msg, invited=rnd.random() < 0.75, history=history))

    open_end = Period(d(2009, 1, 1), None)

    def pos(name, start=None):
        return Affiliation("position", name, normalize_org(name), Period(start, None), "")

    def person_work(id_, title, year, *authors):
        return Work(id_, title, year, "", "", tuple(Coauthor(n, "", tuple(i)) for n, i in authors))

    northwind = Target("Northwind Capital", [Affiliation("position", "Northwind Capital", normalize_org("Northwind Capital"), open_end, "")],
                       kind="company", insiders=[
        Insider("Marina Kovalenko", False, True, "CEO", d(2019, 1, 10), d(2026, 6, 2)),
        Insider("Peter Lindqvist", False, True, "CFO", d(2019, 1, 10), d(2026, 6, 2)),
        Insider("Irina Belova", True, False, "", d(2020, 3, 1), d(2026, 3, 15)),
        Insider("Tomas Reyes", True, False, "", d(2021, 5, 1), d(2026, 3, 15)),
    ])
    contoso = Target("Contoso Labs", [
        Affiliation("position", "Contoso Labs", normalize_org("Contoso Labs"), Period(d(2011, 1, 1), None), ""),
        Affiliation("board", "Contoso Labs", normalize_org("Contoso Labs"), Period(d(2011, 1, 1), None), "")],
        kind="company")
    smirnova = Target("Elena Smirnova", [
        Affiliation("education", "University of Toronto", normalize_org("University of Toronto"),
                    Period(d(2010, 9, 1), d(2014, 6, 1)), "BSc"),
        Affiliation("position", "Contoso Labs", normalize_org("Contoso Labs"), Period(d(2018, 6, 1), None), "CTO")],
        kind="person", works=[
        person_work("DEMO-W1", "Ranking introduction paths in professional networks", 2023,
                    ("Anna Orlova", ["University of Toronto"]), ("Victor Hale", ["Contoso Labs"]),
                    ("Priya Nair", ["University of Toronto"])),
        person_work("DEMO-W2", "Noisy-OR for weak social ties", 2024,
                    ("Anna Orlova", ["University of Toronto"]), ("Leo Brandt", [])),
        person_work("DEMO-W3", "Warm introductions at scale", 2022,
                    ("Victor Hale", ["Contoso Labs"]), ("Samuel Okoye", ["Contoso Labs"])),
    ])
    globex = Target("Globex Corporation", [pos("Globex Corporation")], kind="company")
    targets = {"northwind": northwind, "contoso": contoso, "smirnova": smirnova, "globex": globex}
    return specs, targets


def _write_csv(path: Path, header: list[str], rows: list[list], preamble: str = "") -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        if preamble:
            f.write(preamble)
        w = csv.writer(f, lineterminator="\n")  # LF на любой ОС: файлы побайтово воспроизводимы
        w.writerow(header)
        w.writerows(rows)


def _write_export_zip(export: Path, zip_path: Path) -> None:
    """ZIP в том виде, в каком его отдаёт LinkedIn: файлы лежат во вложенной папке.

    Без сжатия и с фиксированной датой: архив побайтово воспроизводим на любой ОС.
    """
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in sorted(export.glob("*.csv")):
            info = zipfile.ZipInfo(f"Basic_LinkedInDataExport/{path.name}", date_time=(2026, 11, 15, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())


def generate(out_dir: Path, *, seed: int = 7, snapshots_dir: Path | None = None) -> dict:
    out = Path(out_dir)
    snaps = Path(snapshots_dir) if snapshots_dir else out / "snapshots"
    apple, hinton = _snapshot(snaps / "apple_target.json"), _snapshot(snaps / "hinton_target.json")
    rnd = random.Random(seed)
    specs = _specs(rnd, apple, hinton)
    extra, extra_targets = _extra_specs(random.Random(seed * 1000 + 1), specs)
    specs += extra
    _assign_slugs(specs)

    export = out / "export"
    export.mkdir(parents=True, exist_ok=True)
    (out / "targets").mkdir(exist_ok=True)
    owner_url = f"https://www.linkedin.com/in/demo-{_slug(*OWNER)}"
    url = lambda s: f"https://www.linkedin.com/in/{s.slug}/"  # noqa: E731

    _write_csv(export / "Profile.csv",
               ["First Name", "Last Name", "Maiden Name", "Address", "Birth Date", "Headline",
                "Summary", "Industry", "Zip Code", "Geo Location", "Twitter Handles", "Websites",
                "Instant Messengers"],
               [[OWNER[0], OWNER[1], "", "", "", "Research Engineer", "ДЕМО-профиль (вымышленный)",
                 "Information Technology", "", "Toronto", "", "", ""]])
    _write_csv(export / "Positions.csv",
               ["Company Name", "Title", "Description", "Location", "Started On", "Finished On"],
               [["Northwind Capital", "Analyst", "", "Toronto", "Jan 2016", "Dec 2020"],
                ["Vector Institute", "Research Engineer", "", "Toronto", "Jan 2021", ""]])
    _write_csv(export / "Education.csv",
               ["School Name", "Start Date", "End Date", "Notes", "Degree Name", "Activities"],
               [["University of Toronto", "2010", "2014", "", "BSc", ""]])
    _write_csv(export / "Connections.csv",
               ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"],
               [[s.first, s.last, url(s), "", s.company, s.position, _fmt(s.connected, "%d %b %Y")]
                for s in specs],
               preamble='Notes:\n"Демо-экспорт: все данные вымышлены."\n\n')

    messages, invites = [], []
    for s in specs:
        for k in range(s.messages):
            sent = date(2026, 11, 14) if s.last_msg and s.last_msg.year == 2026 else s.last_msg
            d = date(sent.year, max(1, sent.month - (k % 3)), max(1, sent.day - k % 7)) if sent else TODAY
            mine = k % 2 == 0
            messages.append([f"conv-{s.slug}", "", OWNER[0] if mine else s.first,
                             owner_url if mine else url(s), s.first if mine else OWNER[0],
                             url(s) if mine else owner_url, d.strftime("%Y-%m-%d 10:00:00 UTC"),
                             "", "(демо-сообщение)", "INBOX"])
        if s.invited:
            invites.append([OWNER[0], s.first, s.connected.strftime("%Y-%m-%d"), "", "OUTGOING",
                            owner_url, url(s)])
    _write_csv(export / "Messages.csv",
               ["CONVERSATION ID", "CONVERSATION TITLE", "FROM", "SENDER PROFILE URL", "TO",
                "RECIPIENT PROFILE URLS", "DATE", "SUBJECT", "CONTENT", "FOLDER"], messages)
    _write_csv(export / "Invitations.csv",
               ["From", "To", "Sent At", "Message", "Direction", "inviterProfileUrl", "inviteeProfileUrl"],
               invites)

    enrich_rows = [[s.slug, kind, name, _fmt(a, "%Y-%m"), _fmt(b, "%Y-%m"), role]
                   for s in specs for kind, name, a, b, role in s.history]
    _write_csv(out / "enrichment.csv", ["person_id", "kind", "name", "start", "end", "role"], enrich_rows)

    # цели: Apple как есть; Hinton — публичные аффилиации
    save_target(apple, out / "targets" / "apple.json")
    for name, start, end in [("University of Toronto", 1987, None), ("Google", 2013, 2023),
                             ("Vector Institute", 2017, None)]:
        hinton.affiliations.append(Affiliation(
            "position", name, normalize_org(name),
            Period(date(start, 1, 1), date(end, 12, 31) if end else None), ""))
    save_target(hinton, out / "targets" / "hinton.json")
    for key, target in extra_targets.items():  # вымышленные цели: очевидные сценарии
        save_target(target, out / "targets" / f"{key}.json")
    (out / "demo_scenarios.json").write_text(
        json.dumps(DEMO_SCENARIOS, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    _write_export_zip(export, out / "linkedin_export_demo.zip")

    network = build_network(export, out / "enrichment.csv")
    by_slug = {s.slug: s for s in specs}
    for contact in network.contacts.values():
        spec = by_slug[contact.id]
        if spec.works:
            contact.works = list(spec.works)
        if spec.attach_works:
            contact.works = [
                Work(w.id, w.title, w.year, w.doi, w.url)
                for w in hinton.works if any(is_same_person(spec.first + " " + spec.last, c.name)
                                             for c in w.coauthors)
            ]
    save_network(network, out / "network.json")

    scenarios = {s.slug: {"tag": s.tag, "note": s.note} for s in specs}
    (out / "scenarios.json").write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (out / "DISCLAIMER.md").write_text(DISCLAIMER, encoding="utf-8", newline="\n")
    return {"contacts": len(specs), "messages": len(messages), "invitations": len(invites),
            "scenarios": sorted({s.tag for s in specs})}
