"""Служебные команды: python -m app.cli <команда>

    import-legacy   перенести данные старого CLI-сканера (.env, .secrets/proxies.txt,
                    seed/whitelist*.txt, temp/*.json) в БД. Повторный запуск безопасен:
                    существующие записи не дублируются.
    onboard-account подключить аккаунт Selectel через IAM-токен и выбранный прокси.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import re
import sys
import warnings
from getpass import GetPassWarning, getpass
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select

from app.core.config import BACKEND_DIR
from app.core.crypto import encrypt
from app.db.migrate import upgrade_db
from app.db.models import Proxy, RegionPool, SelectelKey, Subnet
from app.db.session import session_scope
from app.engine.proxy import ProxyParseError, parse_proxy_line
from app.onboarding import OnboardingError, onboard_account

ROOT = BACKEND_DIR.parent


def _legacy_accounts(env: dict[str, str | None]) -> list[dict[str, str]]:
    accounts = []
    for n in range(1, 50):
        prefix = "SEL_" if n == 1 else f"SEL{n}_"
        account_id = (env.get(f"{prefix}ACCOUNT_ID") or "").strip()
        username = (env.get(f"{prefix}USERNAME") or "").strip()
        password = env.get(f"{prefix}PASSWORD") or ""
        if not (account_id and username and password):
            continue
        accounts.append({
            "account_id": account_id,
            "username": username,
            "password": password,
            "project_name": (env.get(f"{prefix}PROJECT_NAME") or "").strip(),
            "project_id": (env.get(f"{prefix}PROJECT_ID") or "").strip(),
            "proxy": (env.get(f"{prefix}PROXY") or "").strip() if n > 1 else "",
        })
    return accounts


def _whitelist_entries() -> list[ipaddress.IPv4Network]:
    entries: list[ipaddress.IPv4Network] = []
    path = BACKEND_DIR / "seed" / "whitelist.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                net = ipaddress.ip_network(line, strict=False)
            except ValueError:
                continue
            if net.version == 4:
                entries.append(net)
    return entries


async def import_legacy() -> None:
    await upgrade_db()
    env = dotenv_values(ROOT / ".env")
    report: dict[str, int] = {}

    async with session_scope() as db:
        # Прокси: общий SEL_PROXY (липкие сессии в пароле) + список .secrets/proxies.txt
        proxy_lines: list[tuple[str, str, str]] = []
        if env.get("SEL_PROXY"):
            proxy_lines.append((env["SEL_PROXY"].strip(), "proxxxymiron (из .env)", "password_suffix"))
        secrets_list = ROOT / ".secrets" / "proxies.txt"
        if secrets_list.exists():
            for line in secrets_list.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    proxy_lines.append((line.strip(), "", "none"))
        added = 0
        for line, label, mode in proxy_lines:
            try:
                parsed = parse_proxy_line(line)
            except ProxyParseError:
                continue
            exists = (await db.execute(select(Proxy).where(Proxy.fingerprint == parsed.fingerprint))).scalar()
            if exists:
                continue
            db.add(Proxy(url_enc=encrypt(parsed.url), fingerprint=parsed.fingerprint, display=parsed.display,
                         label=label or parsed.label, kind=parsed.kind, session_mode=mode))
            added += 1
        report["прокси"] = added

        # Ключи (аккаунты SEL_, SEL2_, …). Импортируются выключенными — запуск из панели.
        added = 0
        for i, acc in enumerate(_legacy_accounts(env), start=1):
            exists = (await db.execute(
                select(SelectelKey).where(SelectelKey.account_id == acc["account_id"],
                                          SelectelKey.username == acc["username"]))).scalar()
            if exists:
                continue
            db.add(SelectelKey(
                name=acc["username"] or f"Аккаунт {i}", account_id=acc["account_id"], username=acc["username"],
                password_enc=encrypt(acc["password"]), project_name=acc["project_name"],
                project_id=acc["project_id"], enabled=False, status="paused",
                status_reason="Перенесён из старой версии. Нажмите «Запустить», когда будете готовы.",
            ))
            added += 1
        report["ключи"] = added

        # Кэш пулов floating IP по регионам
        pools_path = ROOT / "temp" / "floating-pools.json"
        pools: dict[str, list[dict]] = {}
        if pools_path.exists():
            pools = json.loads(pools_path.read_text(encoding="utf-8"))
        added = 0
        for region, items in pools.items():
            for item in items:
                if not item.get("subnet_id"):
                    continue
                exists = (await db.execute(select(RegionPool).where(
                    RegionPool.region == region, RegionPool.os_subnet_id == item["subnet_id"]))).scalar()
                if exists:
                    continue
                db.add(RegionPool(region=region, os_subnet_id=item["subnet_id"],
                                  network_id=item.get("network_id") or "", cidr=item.get("cidr") or ""))
                added += 1
        report["пулы регионов"] = added

        # Подсети из whitelist: регион — по пулу Selectel, который её покрывает.
        pool_nets = []
        for region, items in pools.items():
            for item in items:
                try:
                    pool_nets.append((region, ipaddress.ip_network(item.get("cidr", ""), strict=False)))
                except ValueError:
                    pass
        added = 0
        for net in _whitelist_entries():
            if net.prefixlen == 32:
                # Отдельные адреса: Selectel не даёт запрашивать конкретный IP (403 Policy) — не цели.
                continue
            exists = (await db.execute(select(Subnet).where(Subnet.cidr == str(net)))).scalar()
            if exists:
                continue
            region = next((r for r, p in pool_nets if net.subnet_of(p)), "")
            db.add(Subnet(cidr=str(net), region=region, enabled=bool(region), source="import",
                          note="" if region else "Selectel не выдаёт эту подсеть как floating IP"))
            added += 1
        report["подсети"] = added

    for name, count in report.items():
        print(f"{name}: +{count}")


def _onboard_account(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli onboard-account",
                                     description="Создать сервисный ключ Selectel через IAM API.")
    parser.add_argument("account_id", help="номер аккаунта Selectel")
    parser.add_argument("--proxy-id", type=int, required=True, help="ID прокси в Toffee Roller")
    parser.add_argument("--project-name", help="имя проекта, по умолчанию toffee-roller-<номер аккаунта>")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]{3,12}", args.account_id):
        parser.error("номер аккаунта должен содержать от 3 до 12 цифр")
    if args.proxy_id < 1:
        parser.error("ID прокси должен быть положительным")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            token = getpass("IAM-токен аккаунта Selectel: ").strip()
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        print("Введите IAM-токен в интерактивном терминале.", file=sys.stderr)
        return 2
    try:
        key, added = asyncio.run(onboard_account(args.account_id, args.proxy_id, args.project_name or "", token))
    except OnboardingError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError):
        print("Не удалось прочитать ответ Selectel или параметры прокси.", file=sys.stderr)
        return 1
    if added:
        print(f"Добавлен ключ №{key.id} для проекта «{key.project_name}». Ключ на паузе.")
    else:
        print(f"Ключ №{key.id} для проекта «{key.project_name}» уже добавлен.")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    if argv[0] == "import-legacy":
        asyncio.run(import_legacy())
        return 0
    if argv[0] == "onboard-account":
        return _onboard_account(argv[1:])
    print(f"неизвестная команда: {argv[0]}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
