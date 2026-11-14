"""Синтетический экспорт LinkedIn, согласованный с публичными данными SEC EDGAR и OpenAlex.

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

from .models import Affiliation, Period, Work
from .names import is_same_person
from .network import build_network
from .normalize import normalize_org
from .store import load_target, save_network, save_target

TODAY = date(2026, 11, 15)
OWNER = ("Alex", "Demo")

DISCLAIMER = """# СИНТЕТИЧЕСКИЕ ДАННЫЕ

Этот набор создан генератором `intro-paths synth` для демонстрации и тестов.

- Все связи, должности, места учёбы, сообщения и приглашения **вымышлены**.
- Имена директоров/офицеров Apple и соавторов Geoffrey Hinton взяты из открытых источников
  (SEC EDGAR, OpenAlex) только для проверки сопоставления с живыми данными.
- У реальных людей подставлены вымышленные работодатели (Northwind Capital, Contoso Labs,
  Tailspin Toys), чтобы не утверждать фактов о них.
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
    seen: dict[str, int] = {}
    for s in specs:
        base = _slug(s.first, s.last)
        seen[base] = seen.get(base, 0) + 1
        s.slug = f"demo-{base}" + (f"-{seen[base]}" if seen[base] > 1 else "")
    return specs


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

    export = out / "export"
    export.mkdir(parents=True, exist_ok=True)
    (out / "targets").mkdir(exist_ok=True)
    owner_url = f"https://www.linkedin.com/in/demo-{_slug(*OWNER)}"
    url = lambda s: f"https://www.linkedin.com/in/{s.slug}/"  # noqa: E731

    _write_csv(export / "Profile.csv",
               ["First Name", "Last Name", "Maiden Name", "Address", "Birth Date", "Headline",
                "Summary", "Industry", "Zip Code", "Geo Location", "Twitter Handles", "Websites",
                "Instant Messengers"],
               [[OWNER[0], OWNER[1], "", "", "", "Research Engineer", "СИНТЕТИЧЕСКИЙ профиль для демо",
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
               preamble='Notes:\n"Синтетический экспорт: все данные вымышлены."\n\n')

    messages, invites = [], []
    for s in specs:
        for k in range(s.messages):
            sent = date(2026, 11, 14) if s.last_msg and s.last_msg.year == 2026 else s.last_msg
            d = date(sent.year, max(1, sent.month - (k % 3)), max(1, sent.day - k % 7)) if sent else TODAY
            mine = k % 2 == 0
            messages.append([f"conv-{s.slug}", "", OWNER[0] if mine else s.first,
                             owner_url if mine else url(s), s.first if mine else OWNER[0],
                             url(s) if mine else owner_url, d.strftime("%Y-%m-%d 10:00:00 UTC"),
                             "", "(синтетическое сообщение)", "INBOX"])
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

    _write_export_zip(export, out / "linkedin_export_demo.zip")

    network = build_network(export, out / "enrichment.csv")
    by_slug = {s.slug: s for s in specs}
    for contact in network.contacts.values():
        spec = by_slug[contact.id]
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
