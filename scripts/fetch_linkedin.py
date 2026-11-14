"""Эксперимент: обогащение контактов через неофициальный open-linkedin-api.

ВНИМАНИЕ: нарушает ToS LinkedIn, аккаунт может быть ограничен. Риск принят владельцем.

Логин/пароль читаются из .env (LINKEDIN_USERNAME, LINKEDIN_PASSWORD) — в код и чат не попадают.

Шаги:
  python scripts/fetch_linkedin.py me
      войти и показать, что API отвечает (имя владельца, число позиций)
  python scripts/fetch_linkedin.py enrich --export-dir <папка с Connections.csv> --limit 20 --out data/enrichment_live.csv
      по --limit контактам получить историю работы/учёбы -> CSV в формате enrichment.csv

Защита аккаунта: случайные паузы 6–15 с, малый --limit, остановка при первой ошибке/challenge,
повторный запуск пропускает уже обработанных (кэш data/.fetched.json).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from open_linkedin_api import Linkedin

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / ".fetched.json"
FIELDS = ["person_id", "kind", "name", "start", "end", "role"]


def login() -> Linkedin:
    load_dotenv(ROOT / ".env")
    li_at, jsid = os.getenv("LINKEDIN_LI_AT"), os.getenv("LINKEDIN_JSESSIONID")
    if li_at and jsid:  # вход через Google: пароля нет, берём cookies браузера
        from requests.cookies import RequestsCookieJar

        jar = RequestsCookieJar()
        jar.set("li_at", li_at.strip('"'), domain=".linkedin.com", path="/")
        jar.set("JSESSIONID", jsid.strip('"'), domain=".linkedin.com", path="/")
        return Linkedin("", "", cookies=jar)
    user, pwd = os.getenv("LINKEDIN_USERNAME"), os.getenv("LINKEDIN_PASSWORD")
    if not (user and pwd):
        sys.exit("Заполните в .env либо LINKEDIN_USERNAME+LINKEDIN_PASSWORD, либо LINKEDIN_LI_AT+LINKEDIN_JSESSIONID")
    return Linkedin(user, pwd, cookies_dir=str(ROOT / ".cookies"))


def fmt(period: dict | None, key: str) -> str:
    d = (period or {}).get(key) or {}
    if not d.get("year"):
        return ""
    return f"{d['year']}-{int(d['month']):02d}" if d.get("month") else str(d["year"])


def rows_for(person_id: str, profile: dict) -> list[dict]:
    rows = []
    for e in profile.get("experience", []):
        if e.get("companyName"):
            p = e.get("timePeriod")
            rows.append({"person_id": person_id, "kind": "position", "name": e["companyName"],
                         "start": fmt(p, "startDate"), "end": fmt(p, "endDate"),
                         "role": e.get("title", "")})
    for e in profile.get("education", []):
        if e.get("schoolName"):
            p = e.get("timePeriod")
            rows.append({"person_id": person_id, "kind": "education", "name": e["schoolName"],
                         "start": fmt(p, "startDate"), "end": fmt(p, "endDate"),
                         "role": e.get("degreeName", "")})
    return rows


def cmd_me(_: argparse.Namespace) -> None:
    api = login()
    me = api.get_user_profile()
    print("Вход успешен. Ответ API (ключи):", sorted(me.keys())[:10])
    mini = me.get("miniProfile", {})
    print("Владелец:", mini.get("firstName"), mini.get("lastName"), "| public_id:", mini.get("publicIdentifier"))


def cmd_enrich(a: argparse.Namespace) -> None:
    from intro_paths.linkedin_export import read_connections

    people = read_connections(Path(a.export_dir) / "Connections.csv")
    slugs = []
    for p in people:
        m = re.search(r"/in/([^/?#]+)", p.profile_url or "")
        if m:
            slugs.append(m.group(1))
    cache = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    todo = [s for s in slugs if s not in cache][: a.limit]
    print(f"Контактов с URL: {len(slugs)}, уже в кэше: {len(cache)}, сейчас обработаем: {len(todo)}")
    if not todo:
        return
    api = login()
    try:
        for i, slug in enumerate(todo, 1):
            profile = api.get_profile(public_id=slug)
            if not profile:
                print(f"[{i}/{len(todo)}] {slug}: пустой ответ — останавливаюсь (возможен challenge/лимит)")
                break
            cache[slug] = rows_for(slug, profile)
            print(f"[{i}/{len(todo)}] {slug}: {len(cache[slug])} записей")
            CACHE.parent.mkdir(exist_ok=True)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
            if i < len(todo):
                time.sleep(random.uniform(6, 15))
    except Exception as exc:  # любой сбой = стоп, чтобы не злить LinkedIn повторами
        print(f"Остановка из-за ошибки: {type(exc).__name__}: {exc}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for rows in cache.values():
            w.writerows(rows)
    print(f"Записано: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("me").set_defaults(fn=cmd_me)
    e = sub.add_parser("enrich")
    e.add_argument("--export-dir", required=True)
    e.add_argument("--limit", type=int, default=20)
    e.add_argument("--out", default=str(ROOT / "data" / "enrichment_live.csv"))
    e.set_defaults(fn=cmd_enrich)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
