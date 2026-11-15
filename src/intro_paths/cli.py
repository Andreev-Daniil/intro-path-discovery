"""Командная строка: импорт экспорта LinkedIn и поиск путей знакомства."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from .config import build_services, load_env
from .models import Target
from .network import build_network
from .paths import IntroPath, find_intro_paths
from .sources.http import HttpError
from .store import load_network, load_target, save_network, save_target
from .synthetic import generate
from .target_enrich import confirm_candidates, enrich_company, enrich_person


def _cmd_import(args: argparse.Namespace) -> int:
    network = build_network(args.export_dir, args.enrichment)
    save_network(network, args.out)
    with_affiliations = sum(1 for p in network.contacts.values() if p.affiliations)
    print(f"Контактов: {len(network.contacts)}, с известными аффилиациями: {with_affiliations}")
    print(f"Сеть сохранена: {args.out}")
    return 0


def _path_to_dict(rank: int, path: IntroPath) -> dict:
    c = path.connector
    return {
        "rank": rank,
        "connector": {"id": c.id, "full_name": c.full_name, "profile_url": c.profile_url},
        "strength": path.strength,
        "signals": [
            {"code": s.code, "detail": s.detail, "weight": s.weight, "evidence_url": s.evidence_url}
            for s in path.signals
        ],
    }


def _ask_human(prompt: str) -> bool:
    """Подтверждение действия человеком; без интерактивного ввода — отказ."""
    print(f"\n>>> ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ\n{prompt}")
    try:
        return input("Выполнить? [y/N] ").strip().lower() in ("y", "yes", "д", "да")
    except EOFError:
        print("(нет интерактивного ввода — отказ)")
        return False


def _cmd_agent(args: argparse.Namespace) -> int:
    from .agent.config import load_config
    from .agent.loop import run_from_config

    load_env()
    cfg = load_config(args.config)
    result = run_from_config(cfg, args.request, _ask_human, print)
    print(f"\n=== Итог ({result.stop_reason}, шагов: {result.steps}, вызовов инструментов: {len(result.tool_calls)}) ===")
    print(result.final_text)
    return 0 if result.stop_reason in ("end_turn",) else 1


def _cmd_ui(args: argparse.Namespace) -> int:
    """Запуск интерфейса тем же интерпретатором, из которого вызвана команда (нужное окружение)."""
    import importlib.util
    import subprocess

    if importlib.util.find_spec("streamlit") is None:
        print("Ошибка: не установлен streamlit. Установите: python -m pip install -e \".[app]\"", file=sys.stderr)
        return 2
    app = Path(__file__).with_name("app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    if args.no_browser:
        cmd += ["--server.headless", "true"]
    print(f"Интерфейс: http://localhost:{args.port}  (остановить: Ctrl+C)")
    return subprocess.call(cmd)


def _cmd_synth(args: argparse.Namespace) -> int:
    report = generate(args.out_dir, seed=args.seed, snapshots_dir=args.snapshots_dir)
    print(f"Демо-набор: {args.out_dir} (контактов {report['contacts']}, "
          f"сообщений {report['messages']}, приглашений {report['invitations']})")
    print("ВСЕ связи вымышлены; см. DISCLAIMER.md")
    return 0


def _cmd_enrich_target(args: argparse.Namespace) -> int:
    load_env()
    if bool(args.target_company) == bool(args.target_person):
        raise ValueError("укажите ровно одно: --target-company или --target-person")
    is_company = bool(args.target_company)
    name = args.target_company or args.target_person
    services = build_services(
        use_edgar=is_company and not args.no_edgar,
        use_openalex=not is_company and not args.no_openalex,
        use_crossref=not is_company and not args.no_crossref,
    )
    target = load_target(args.base_target) if args.base_target else Target(name=name)
    target.name = name

    if is_company:
        if services.edgar is None:
            raise ValueError("для цели-компании нужен EDGAR (уберите --no-edgar)")
        report = enrich_company(target, services.edgar, query=args.ticker or name, progress=print)
        print(f"Инсайдеров найдено: {report['insiders']}")
    else:
        if not services.publications:
            raise ValueError("нет источников публикаций (--no-openalex и --no-crossref)")
        report = enrich_person(target, services.publications, progress=print)
        print(f"Работ найдено: {report['works']}")

    if args.deep:
        if not (args.network and args.out_network):
            raise ValueError("--deep требует --network и --out-network")
        if not services.publications:
            services = build_services(use_edgar=False)
        network = load_network(args.network)
        confirmed = confirm_candidates(
            network, target, services.publications, top=args.top, progress=print
        )
        save_network(network, args.out_network)
        print(f"Проверено контактов: {confirmed}; сеть сохранена: {args.out_network}")

    save_target(target, args.out_target)
    print(f"Цель сохранена: {args.out_target}")
    return 0


def _cmd_paths(args: argparse.Namespace) -> int:
    network = load_network(args.network)
    target = load_target(args.target)
    paths = find_intro_paths(
        network, target, min_strength=args.min_strength, limit=args.limit
    )

    if args.json:
        payload = [_path_to_dict(i, p) for i, p in enumerate(paths, start=1)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Цель: {target.name}")
    if not paths:
        print("Путей не найдено.")
        return 0
    for i, path in enumerate(paths, start=1):
        c = path.connector
        print(f"\n{i}. {c.full_name} — сила {path.strength:.2f}")
        if c.profile_url:
            print(f"   профиль: {c.profile_url}")
        for s in path.signals:
            print(f"   - {s.detail} (вес {s.weight:.2f})")
            if s.evidence_url:
                print(f"     источник: {s.evidence_url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(
        prog="intro-paths",
        description="Пути знакомства по экспорту LinkedIn (только данные, которые вы загрузили).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="разобрать экспорт и сохранить сеть в JSON")
    p_import.add_argument("--export-dir", required=True, type=Path,
                          help="папка с распакованным экспортом LinkedIn")
    p_import.add_argument("--enrichment", type=Path,
                          help="CSV с дополнительными аффилиациями контактов")
    p_import.add_argument("--out", required=True, type=Path, help="файл network.json")
    p_import.set_defaults(func=_cmd_import)

    p_paths = sub.add_parser("paths", help="найти пути знакомства к цели")
    p_paths.add_argument("--network", required=True, type=Path, help="network.json из import")
    p_paths.add_argument("--target", required=True, type=Path, help="JSON-описание цели")
    p_paths.add_argument("--limit", type=int, default=10)
    p_paths.add_argument("--min-strength", type=float, default=0.0)
    p_paths.add_argument("--json", action="store_true", help="вывод в JSON")
    p_paths.set_defaults(func=_cmd_paths)

    p_ui = sub.add_parser("ui", help="запустить веб-интерфейс (граф, картинки, пути, агент)")
    p_ui.add_argument("--port", type=int, default=8501)
    p_ui.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    p_ui.set_defaults(func=_cmd_ui)

    p_agent = sub.add_parser("agent", help="LLM-агент: найти путь знакомства (инструменты, лимит шагов, подтверждение)")
    p_agent.add_argument("--config", type=Path, default=Path("config/agent.toml"),
                         help="TOML с провайдером, моделью и лимитами (демо: config/agent.demo.toml)")
    p_agent.add_argument("--request", required=True, help="запрос пользователя на естественном языке")
    p_agent.set_defaults(func=_cmd_agent)

    p_synth = sub.add_parser(
        "synth", help="сгенерировать демо-экспорт LinkedIn под данные EDGAR и OpenAlex"
    )
    p_synth.add_argument("--out-dir", type=Path, default=Path("data/demo"))
    p_synth.add_argument("--snapshots-dir", type=Path, help="по умолчанию <out-dir>/snapshots")
    p_synth.add_argument("--seed", type=int, default=7)
    p_synth.set_defaults(func=_cmd_synth)

    p_enrich = sub.add_parser(
        "enrich-target",
        help="обогатить цель из SEC EDGAR (компания) или OpenAlex/Crossref (человек)",
    )
    p_enrich.add_argument("--target-company", help="название или тикер компании (EDGAR, только США)")
    p_enrich.add_argument("--target-person", help="имя человека (OpenAlex, затем Crossref)")
    p_enrich.add_argument("--ticker", help="тикер для точного поиска компании")
    p_enrich.add_argument("--base-target", type=Path, help="JSON цели с уже известными аффилиациями")
    p_enrich.add_argument("--out-target", required=True, type=Path, help="куда сохранить цель")
    p_enrich.add_argument("--network", type=Path, help="network.json (для --deep)")
    p_enrich.add_argument("--out-network", type=Path, help="куда сохранить сеть после --deep")
    p_enrich.add_argument("--deep", action="store_true",
                          help="проверить топ-N контактов во внешних сервисах (имена уходят наружу)")
    p_enrich.add_argument("--top", type=int, default=20)
    p_enrich.add_argument("--no-edgar", action="store_true")
    p_enrich.add_argument("--no-openalex", action="store_true")
    p_enrich.add_argument("--no-crossref", action="store_true")
    p_enrich.set_defaults(func=_cmd_enrich_target)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, KeyError, HttpError, tomllib.TOMLDecodeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
