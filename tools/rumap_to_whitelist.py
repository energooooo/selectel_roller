"""Собирает whitelist.txt из среза rumap.

Источник — HTML-страница среза (rumap-....html) или голый список подсетей (rumap-....txt).

Из HTML берётся вес каждой /24: какие адреса прошли пробы ICMP/TCP и помечена ли
подсеть «узкой» (через фильтр прошли 1–2 конкретных адреса, а не вся /24). Поэтому:
  • широкая подсеть, где прошло не меньше --min-passed адресов, пишется целиком (/24);
  • узкая — только прошедшими адресами (/32): случайный адрес из такой /24 почти
    наверняка не пройдёт;
  • подсеть без единого прошедшего адреса пропускается.
Из .txt весов нет — все подсети пишутся целиком (/24), как раньше.

Floating IP в Selectel выдаются только из сетей самого Selectel, поэтому остаются
подсети, анонсируемые AS Selectel (по данным RIPEstat).

Использование:
    python tools/rumap_to_whitelist.py путь/к/rumap-....html
    python tools/rumap_to_whitelist.py rumap.html --subnets-only   # все /24 целиком
    python tools/rumap_to_whitelist.py rumap.txt --all             # без фильтра по AS
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "whitelist.txt"
MANUAL_PATH = PROJECT_ROOT / "whitelist.manual.txt"

# AS49505 — SELECTEL, AS50340 — SELECTEL-MSK.
SELECTEL_ASNS = (49505, 50340)
RIPESTAT_URL = "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{asn}"


def read_networks(path: Path) -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    seen: set[ipaddress.IPv4Network] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = raw_line.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            print(f"пропущена строка: {raw_line!r}", file=sys.stderr)
            continue
        if network.version == 4 and network not in seen:
            seen.add(network)
            networks.append(network)
    return networks


def read_weights(path: Path) -> dict[str, dict]:
    """Вес строк из HTML-среза: <script id="weights" type="application/json">."""
    html = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="weights" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        raise SystemExit(f"{path.name}: блок весов (script#weights) не найден — это точно срез rumap?")
    return json.loads(match.group(1))


def passed_octets(weight: dict) -> tuple[set[int], set[int]]:
    """(адреса, прошедшие пробы в этом снятии; все известные прошедшие, включая окно за 6 суток)."""
    probed: set[int] = set()
    for proto in ("icmp", "tcp"):
        probe = weight.get(proto) or {}
        probed.update(probe.get("addrs") or [])
    return probed, probed | set(weight.get("window") or [])


def read_manual(path: Path) -> tuple[list[ipaddress.IPv4Network], list[ipaddress.IPv4Network]]:
    """whitelist.manual.txt: строки «-подсеть» (исключить) и «+подсеть» (добавить целиком)."""
    exclude: list[ipaddress.IPv4Network] = []
    include: list[ipaddress.IPv4Network] = []
    if not path.exists():
        return exclude, include
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = raw_line.split("#", 1)[0].strip()
        if len(value) < 2 or value[0] not in "+-":
            continue
        network = ipaddress.ip_network(value[1:].strip(), strict=False)
        (exclude if value[0] == "-" else include).append(network)
    return exclude, include


def fetch_announced_prefixes(asn: int) -> list[ipaddress.IPv4Network]:
    with urllib.request.urlopen(RIPESTAT_URL.format(asn=asn), timeout=30) as response:
        payload = json.load(response)
    prefixes = []
    for item in payload["data"]["prefixes"]:
        network = ipaddress.ip_network(item["prefix"])
        if network.version == 4:
            prefixes.append(network)
    return prefixes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="срез rumap: .html (с весами) или .txt")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="куда писать (по умолчанию whitelist.txt)")
    parser.add_argument("--all", action="store_true", help="не фильтровать по AS Selectel")
    parser.add_argument("--subnets-only", action="store_true", help="игнорировать веса, писать все /24 целиком")
    parser.add_argument("--min-passed", type=int, default=3,
                        help="сколько адресов широкой /24 должно пройти, чтобы взять её целиком (по умолчанию 3)")
    args = parser.parse_args()

    weights: dict[str, dict] = {}
    if args.source.suffix.lower() in (".html", ".htm"):
        weights = read_weights(args.source)
        networks = sorted({ipaddress.ip_network(k) for k in weights}, key=lambda n: int(n.network_address))
    else:
        networks = read_networks(args.source)
    if not networks:
        print("в файле не найдено ни одной подсети", file=sys.stderr)
        return 1

    if args.all:
        selected = networks
        note = "без фильтра по AS"
    else:
        selectel: list[ipaddress.IPv4Network] = []
        for asn in SELECTEL_ASNS:
            prefixes = fetch_announced_prefixes(asn)
            print(f"AS{asn}: {len(prefixes)} анонсированных префиксов")
            selectel.extend(prefixes)
        selected = [n for n in networks if any(n.subnet_of(p) for p in selectel)]
        note = "только сети AS" + ", AS".join(map(str, SELECTEL_ASNS))

    whole: list[ipaddress.IPv4Network] = []
    singles: list[ipaddress.IPv4Network] = []
    dropped = 0
    for network in selected:
        weight = weights.get(str(network))
        if args.subnets_only or weight is None:
            whole.append(network)
            continue
        probed, known = passed_octets(weight)
        if not weight.get("narrow") and len(probed) >= args.min_passed:
            whole.append(network)
        elif known:
            base = int(network.network_address)
            singles.extend(ipaddress.ip_network(base + octet) for octet in sorted(known))
        else:
            dropped += 1

    exclude, include = read_manual(MANUAL_PATH)
    if exclude or include:
        removed = [n for n in whole + singles if any(n.subnet_of(x) for x in exclude)]
        whole = [n for n in whole if not any(n.subnet_of(x) for x in exclude)]
        singles = [n for n in singles if not any(n.subnet_of(x) for x in exclude)]
        added = [n for n in include if n not in whole]
        # Отдельные адреса внутри добавленной целиком подсети больше не нужны.
        singles = [n for n in singles if not any(n.subnet_of(x) for x in added)]
        whole = sorted(whole + added, key=lambda n: int(n.network_address))
        print(f"{MANUAL_PATH.name}: исключено записей {len(removed)}, добавлено подсетей {len(added)}")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Сгенерировано из {args.source.name} ({generated})",
        f"# {len(selected)} из {len(networks)} подсетей, {note}",
        "# Перегенерировать: python tools/rumap_to_whitelist.py <rumap.html>",
        f"# Ручные правки: {MANUAL_PATH.name}",
        "",
        f"# Широкие подсети — проходит вся /24 ({len(whole)})",
        *[str(n) for n in whole],
    ]
    if singles:
        lines += ["", f"# Узкие подсети — только прошедшие адреса ({len(singles)})", *[str(n.network_address) for n in singles]]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"записано: {len(whole)} подсетей целиком, {len(singles)} отдельных адресов, "
          f"пропущено без прошедших адресов: {dropped} → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
