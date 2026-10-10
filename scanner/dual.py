from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .bootstrap import (
    confirm_no_active_vms,
    ensure_project_resolved,
    render_whitelist_banner,
)
from .client import SelectelScannerClient
from .config import ScannerConfig, load_scanner_config
from .dashboard import (
    MATCHES_RIGHT_PANEL_MIN_LINES,
    MISS_CHURN_TABLE_MAX_ROWS,
    RIGHT_PANEL_MATCHES_RATIO,
    RIGHT_PANEL_MISS_CHURN_RATIO,
    format_uptime,
    region_result_style,
    regions_panel_layout_height,
    render_events,
)
from .ip_frequency import (
    MissChurnSnapshot,
    merge_miss_churn_snapshots,
    persist_miss_churn_text,
    render_miss_churn_table,
)
from .main import SelectelScannerApp, _normalize_regions, build_settings, parse_args
from .models import EventRecord, MatchRecord, RegionRunState, ScannerSettings
from .paths import DEFAULT_WHITELIST_PATH
from .rich_ui import DASHBOARD_PANEL_PADDING, DASHBOARD_TABLE_BOX, DASHBOARD_TABLE_PADDING, dashboard_console, dashboard_console_stderr
from .setup_wizard import needs_setup, run_setup_wizard


ONLY_REGIONS = ("ru-1", "ru-2", "ru-3")


def _region_sort_key(region: str) -> tuple[str, int | str]:
    prefix, _, suffix = region.partition("-")
    try:
        return prefix, int(suffix)
    except ValueError:
        return prefix, suffix


def _select_regions(regions: tuple[str, ...]) -> tuple[str, ...]:
    available = set(regions)
    selected = tuple(region for region in ONLY_REGIONS if region in available)
    if not selected:
        raise RuntimeError(
            "None of ru-1, ru-2, ru-3 are available in the Selectel service catalog for this project"
        )
    return selected


def _regions_from_env_var(name: str) -> tuple[str, ...] | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return _normalize_regions(raw.split(","))


async def _resolve_available_regions(settings: ScannerSettings) -> tuple[str, ...]:
    client = SelectelScannerClient(
        username=settings.username,
        password=settings.password,
        account_id=settings.account_id,
        project_name=settings.project_name,
        project_id=settings.project_id,
        regions=(),
    )
    try:
        await client.ensure_authenticated()
        regions = _select_regions(tuple(sorted(client.available_regions(), key=_region_sort_key)))
    finally:
        await client.close()

    if not regions:
        raise RuntimeError("Selectel returned no available network regions for this account")
    return regions


# Верхняя граница сканирования аккаунтов SEL{n}_ в .env. Защищает от бесконечного
# цикла enumerate и задаёт «разумный потолок» на число воркеров (аккаунтов можно
# задать сколько угодно до этого числа — SEL_, SEL2_, SEL3_, …, SEL64_).
MAX_ACCOUNTS = 64
_ACCOUNT_ENV_KEYS = ("USERNAME", "PASSWORD", "ACCOUNT_ID", "PROJECT_NAME", "PROJECT_ID")


def _account_env_prefix(index: int) -> str:
    """SEL_ для первого аккаунта, SEL2_/SEL3_/… для последующих (единая конвенция с hunt.py)."""
    return "SEL_" if index == 1 else f"SEL{index}_"


def _account_label_env_key(index: int) -> str:
    """Имя env-переменной с подписью воркера: SEL1_LABEL, SEL2_LABEL, SEL3_LABEL, …"""
    return f"SEL{index}_LABEL"


def _account_has_env(prefix: str) -> bool:
    return any(os.getenv(f"{prefix}{key}", "").strip() for key in _ACCOUNT_ENV_KEYS)


def _config_account_has_values(acc: object | None) -> bool:
    if acc is None:
        return False
    return any(
        (getattr(acc, field_name, "") or "").strip()
        for field_name in ("username", "password", "account_id", "project_name", "project_id")
    )


@dataclass
class AccountSpec:
    """Один аккаунт для мульти-дашборда: порядковый номер, env-префикс и (опц.) запись config.json."""

    index: int
    prefix: str
    config_account: object | None


def _enumerate_accounts(config: ScannerConfig) -> list[AccountSpec]:
    """Все заданные аккаунты по порядку.

    Аккаунт 1 — primary (`SEL_*` / `config.selectel.api`), далее `SEL2_*`, `SEL3_*`, …
    и/или `config.selectel.additional_accounts`. Аккаунт включается, если у него задан
    хотя бы один из USERNAME/PASSWORD/ACCOUNT_ID/PROJECT_NAME/PROJECT_ID (в env или config).
    Пропуски допускаются: можно задать только SEL_ и SEL4_ — вернутся оба.
    """
    extra = config.selectel.additional_accounts
    specs: list[AccountSpec] = []

    if _account_has_env("SEL_") or _config_account_has_values(config.selectel.api):
        specs.append(AccountSpec(index=1, prefix="SEL_", config_account=config.selectel.api))

    upper = max(MAX_ACCOUNTS, len(extra) + 1)
    for index in range(2, upper + 1):
        prefix = _account_env_prefix(index)
        cfg_acc = extra[index - 2] if (index - 2) < len(extra) else None
        if _account_has_env(prefix) or _config_account_has_values(cfg_acc):
            specs.append(AccountSpec(index=index, prefix=prefix, config_account=cfg_acc))

    return specs


def _pick_account_value(prefix: str, config_value: str, key: str, fallback: str = "") -> str:
    env_value = os.getenv(f"{prefix}{key}", "").strip()
    if env_value:
        return env_value
    config_value = (config_value or "").strip()
    if config_value:
        return config_value
    return fallback


def _account_credentials(spec: AccountSpec) -> dict[str, str]:
    cfg = spec.config_account
    # Пароль НЕ .strip() — по краям могут быть значимые пробелы.
    password = os.getenv(f"{spec.prefix}PASSWORD", "") or (getattr(cfg, "password", "") or "")
    return {
        "username": _pick_account_value(spec.prefix, getattr(cfg, "username", ""), "USERNAME"),
        "password": password,
        "account_id": _pick_account_value(spec.prefix, getattr(cfg, "account_id", ""), "ACCOUNT_ID"),
        # Проект у каждого аккаунта свой: не наследуем от другого аккаунта. Если не задан —
        # bootstrap-этап резолвнёт его через VPC Resell API и запишет SEL{n}_PROJECT_ID.
        "project_name": _pick_account_value(spec.prefix, getattr(cfg, "project_name", ""), "PROJECT_NAME"),
        "project_id": _pick_account_value(spec.prefix, getattr(cfg, "project_id", ""), "PROJECT_ID"),
    }


def _validate_account_credentials(
    worker_title: str,
    creds: dict[str, str],
    prefix: str,
) -> str | None:
    """Возвращает текст ошибки для Recent Events или None, если всё ок."""
    missing: list[str] = []
    if not creds.get("username"):
        missing.append(f"логин ({prefix}USERNAME / config)")
    if not creds.get("password"):
        missing.append(f"пароль ({prefix}PASSWORD / config)")
    if not creds.get("account_id"):
        missing.append(f"{prefix}ACCOUNT_ID")
    if missing:
        return f"{worker_title} не запущен: не задано — {', '.join(missing)}"
    # Проект не обязателен: если не задан — попробуем автодискавери через VPC Resell API.
    return None


def _error_events(message: str) -> deque[EventRecord]:
    return deque([EventRecord.create("error", message)], maxlen=48)


@dataclass
class WorkerSlot:
    """Один воркер в dual-дашборде: либо запущенный app, либо только событие об ошибке конфигурации."""

    label: str
    app: SelectelScannerApp | None
    events: deque[EventRecord]


def _slot_label(
    env_key: str,
    fallback: str,
    settings: ScannerSettings | None,
    *,
    err: str | None,
) -> str:
    explicit = os.getenv(env_key, "").strip()
    if explicit:
        return explicit
    if err is None and settings and (settings.username or "").strip():
        return f"{settings.username.strip()} / {(settings.account_id or '').strip()}".strip()
    return fallback


def _slot_events(app: SelectelScannerApp | None, err: str | None) -> deque[EventRecord]:
    if app is not None:
        return app.events
    assert err is not None
    return _error_events(err)


def _emit_log_flags(
    args: argparse.Namespace, console: Console, interactive_ui: bool
) -> tuple[bool, bool, bool, str | None]:
    rich_logs = bool(getattr(args, "rich_logs", False))
    log_file_arg = getattr(args, "log_file", None) or None
    emit_out = (not interactive_ui) or rich_logs or bool(log_file_arg)
    log_err = rich_logs and interactive_ui
    suppress_console = bool(interactive_ui and log_file_arg and not rich_logs)
    return emit_out, log_err, suppress_console, log_file_arg


def _account_region_env(index: int) -> tuple[str, ...] | None:
    """Регионы воркера из env: SEL1_SCANNER_REGIONS / SEL_SCANNER_REGIONS для аккаунта 1,
    SEL{n}_SCANNER_REGIONS для остальных. None — переменные не заданы."""
    names = ["SEL1_SCANNER_REGIONS", "SEL_SCANNER_REGIONS"] if index == 1 else [f"SEL{index}_SCANNER_REGIONS"]
    for name in names:
        regions = _regions_from_env_var(name)
        if regions is not None:
            return regions
    return None


def _build_account_settings(
    base: ScannerSettings,
    spec: AccountSpec,
    *,
    regions: tuple[str, ...],
) -> ScannerSettings:
    """Строит ScannerSettings одного аккаунта: его креды + общие тайминги/пути из `base`.

    `base` — настройки, собранные `build_settings(args, ...)` (общие значения SEL_* таймингов,
    whitelist/state пути). Каждому аккаунту даётся своя секция state (`account-{n}`).
    """
    creds = _account_credentials(spec)
    if not creds["username"] or not creds["password"] or not creds["account_id"]:
        raise ValueError(
            f"Аккаунт {spec.index}: задайте {spec.prefix}USERNAME / {spec.prefix}PASSWORD / "
            f"{spec.prefix}ACCOUNT_ID в .env (или config.json)"
        )
    # project_name / project_id может отсутствовать — bootstrap-этап запросит через Resell API.
    return ScannerSettings(
        username=creds["username"],
        password=creds["password"],
        account_id=creds["account_id"],
        project_name=creds["project_name"],
        project_id=creds["project_id"],
        whitelist_path=base.whitelist_path,
        state_path=base.state_path,
        state_section=f"account-{spec.index}",
        regions=regions,
        target_count=base.target_count,
        min_batch_size=base.min_batch_size,
        max_batch_size=base.max_batch_size,
        delete_concurrency=base.delete_concurrency,
        live_refresh_per_second=base.live_refresh_per_second,
        allocation_poll_attempts=base.allocation_poll_attempts,
        allocation_poll_delay=base.allocation_poll_delay,
        cooldown_base=base.cooldown_base,
        cooldown_max=base.cooldown_max,
        cleanup_on_exit=base.cleanup_on_exit,
        cleanup_existing_non_matches=base.cleanup_existing_non_matches,
        reconcile_interval=base.reconcile_interval,
        max_floating_ips_per_minute=base.max_floating_ips_per_minute,
    )


async def _prepare_account_regions(
    args: argparse.Namespace,
    spec: AccountSpec,
    settings: ScannerSettings,
) -> tuple[str, ...]:
    """Определяет финальные регионы воркера и проставляет их в `settings` inplace.

    Приоритет: --regions из CLI > SEL{n}_SCANNER_REGIONS из env > все доступные проекту.
    """
    available = await _resolve_available_regions(settings)
    env_regions = _account_region_env(spec.index)

    if args.regions:
        chosen = tuple(r for r in settings.regions if r in available) or available
    elif env_regions is not None:
        chosen = tuple(r for r in env_regions if r in available) or available
    else:
        chosen = available

    settings.regions = chosen
    return chosen


def _compact_label(label: str) -> str:
    primary = label.split(" / ", 1)[0].strip() or label.strip()
    if len(primary) <= 16:
        return primary
    return f"{primary[:13]}..."


def _render_mode_banner(console: Console, specs: list[AccountSpec]) -> None:
    """Явно сообщает пользователю, сколько аккаунтов будет работать и под какими префиксами.

    Отделено от dashboard-а, чтобы даже в non-interactive режиме было понятно,
    один, два или больше воркеров запускается.
    """
    prefixes = ", ".join(f"{spec.prefix}*" for spec in specs)
    if len(specs) >= 2:
        text = (
            f"[bold]Режим:[/bold] [cyan]{len(specs)} аккаунта(-ов)[/cyan] "
            f"({prefixes}).\n"
            "Интерфейс покажет все колонки воркеров параллельно."
        )
        border = "cyan"
    else:
        next_prefix = _account_env_prefix(specs[0].index + 1) if specs else "SEL2_"
        text = (
            f"[bold]Режим:[/bold] [green]один аккаунт[/green] ({prefixes}).\n"
            "Другие аккаунты не заданы — запускается single-интерфейс.\n"
            f"[dim]Чтобы добавить ещё один, пропишите {next_prefix}USERNAME / {next_prefix}PASSWORD / "
            f"{next_prefix}ACCOUNT_ID в .env (можно хоть до SEL{MAX_ACCOUNTS}_).[/dim]"
        )
        border = "green"

    console.print(
        Panel(
            text,
            title="ip-roller",
            border_style=border,
            padding=DASHBOARD_PANEL_PADDING,
        )
    )


def _disabled_worker_regions_panel(label: str) -> Panel:
    compact = _compact_label(label)
    return Panel(
        Text("Воркер не запущен — см. Recent Events ниже.", style="dim"),
        title=f"Region Workers | {compact}",
        subtitle="—",
        border_style="red",
        padding=DASHBOARD_PANEL_PADDING,
    )


def _render_compact_regions_table(app: SelectelScannerApp) -> Table:
    table = Table(expand=True, box=DASHBOARD_TABLE_BOX, padding=DASHBOARD_TABLE_PADDING)
    table.add_column("Rg", style="bold", no_wrap=True)
    table.add_column("B", justify="right")
    table.add_column("In", justify="right")
    table.add_column("A∪", justify="right")
    table.add_column("Miss", justify="right")
    table.add_column("Del#", justify="right")
    table.add_column("E", justify="right")
    table.add_column("Cd", justify="right")
    table.add_column("Last", overflow="fold")

    now = time.monotonic()
    for region in app.region_states.values():
        last_value = region.last_ip or region.last_error or "-"
        cooldown = f"{region.cooldown_remaining(now):.0f}s" if region.cooldown_until > now else "-"
        table.add_row(
            region.region,
            str(region.batch_size),
            str(region.inflight),
            str(app.unique_alloc_by_region(region.region)),
            str(app.whitelist_miss_events_in_region(region.region)),
            str(app.deleted_miss_ops_in_region(region.region)),
            str(region.errors),
            cooldown,
            f"[{region_result_style(region)}]{last_value}[/{region_result_style(region)}]",
        )
    return table


def _match_sort_key(match: MatchRecord) -> tuple[str, str, str]:
    return (match.discovered_at, match.region, match.address)


def _render_combined_matches_table(accounts: list[tuple[str, SelectelScannerApp]]) -> Table:
    table = Table(expand=True, box=DASHBOARD_TABLE_BOX, padding=DASHBOARD_TABLE_PADDING)
    table.add_column("Acct", style="bold")
    table.add_column("IP", style="bold green")
    table.add_column("Rg", no_wrap=True)
    table.add_column("When")

    rows: list[tuple[str, MatchRecord]] = []
    for label, app in accounts:
        compact_label = _compact_label(label)
        for match in app.matches.values():
            rows.append((compact_label, match))

    if not rows:
        table.add_row("-", "-", "-", "No matches yet")
        return table

    for compact_label, match in sorted(rows, key=lambda item: _match_sort_key(item[1]), reverse=True):
        discovered = match.discovered_at.replace("T", " ").split(".", 1)[0] if match.discovered_at else "-"
        table.add_row(compact_label, match.address, match.region, discovered)
    return table


def _merged_global_stats(
    accounts: list[tuple[str, SelectelScannerApp]],
) -> tuple[int, float, MissChurnSnapshot, int]:
    union: set[str] = set()
    del_ops_total = 0
    ips_pm = 0.0
    churn_snaps: list[MissChurnSnapshot] = []
    for _, app in accounts:
        union |= app.distinct_allocated_ips()
        del_ops_total += app.deleted_miss_ops_total()
        ips_pm += app.allocations_per_minute_recent()
        churn_snaps.append(app.miss_churn_snapshot())
    miss_merged = merge_miss_churn_snapshots(*churn_snaps)
    return len(union), ips_pm, miss_merged, del_ops_total


def _merged_region_alloc_delete_totals(accounts: list[tuple[str, SelectelScannerApp]]) -> tuple[int, int]:
    """Сумма счётчиков RegionRunState по всем регионам и аккаунтам (выдачи / удаления в воркерах)."""
    alloc = 0
    deleted = 0
    for _, app in accounts:
        for st in app.region_states.values():
            alloc += st.allocations
            deleted += st.deleted
    return alloc, deleted


def _build_dual_header(slots: list[WorkerSlot]) -> Panel:
    active = [s.app for s in slots if s.app is not None]
    if not active:
        line = "Нет активных воркеров — проверьте учётные данные в .env / config"
        return Panel(
            Text(line, style="bold yellow"),
            border_style="yellow",
            padding=DASHBOARD_PANEL_PADDING,
        )
    started_at = min(a.started_at for a in active)
    accounts = [(s.label, s.app) for s in slots if s.app is not None]
    _, ips_pm, _, _ = _merged_global_stats(accounts)
    total_alloc, total_deleted = _merged_region_alloc_delete_totals(accounts)
    line = (
        f"Время работы: {format_uptime(started_at)}    "
        f"IP/мин: {ips_pm:.1f}    "
        f"Всего IP: {total_alloc}    "
        f"Удалено IP: {total_deleted}"
    )
    return Panel(Text(line, style="bold cyan"), border_style="cyan", padding=DASHBOARD_PANEL_PADDING)


def _build_dual_dashboard(slots: list[WorkerSlot]) -> Layout:
    accounts = [(s.label, s.app) for s in slots if s.app is not None]
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )
    layout["body"].split_row(
        Layout(name="workers", ratio=3),
        Layout(name="right", ratio=2, minimum_size=40),
    )
    # Динамическое количество воркеров-колонок: по числу слотов, которые пользователь
    # действительно хотел запустить. Если активен только один — не тратим место на пустую половину.
    if len(slots) <= 1:
        layout["workers"].split_column(
            Layout(name="account_1_workers"),
        )
    else:
        layout["workers"].split_column(
            *[Layout(name=f"account_{i}_workers") for i in range(1, len(slots) + 1)]
        )
    layout["header"].update(_build_dual_header(slots))

    for index, slot in enumerate(slots, start=1):
        compact_label = _compact_label(slot.label)
        region_h = (
            regions_panel_layout_height(len(slot.app.region_states))
            if slot.app is not None
            else 8
        )
        layout[f"account_{index}_workers"].split_column(
            Layout(name=f"account_{index}_regions", size=region_h),
            Layout(name=f"account_{index}_events", ratio=1),
        )
        if slot.app is not None:
            layout[f"account_{index}_regions"].update(
                Panel(
                    _render_compact_regions_table(slot.app),
                    title=f"Region Workers | {compact_label}",
                    subtitle=slot.app.project_label,
                    border_style="blue" if index == 1 else "cyan",
                    padding=DASHBOARD_PANEL_PADDING,
                )
            )
        else:
            layout[f"account_{index}_regions"].update(_disabled_worker_regions_panel(slot.label))
        layout[f"account_{index}_events"].update(
            Panel(
                render_events(slot.events),
                title=f"Recent Events | {compact_label}",
                border_style="magenta",
                padding=DASHBOARD_PANEL_PADDING,
            )
        )

    layout["right"].split_column(
        Layout(
            name="matches_pane",
            ratio=RIGHT_PANEL_MATCHES_RATIO,
            minimum_size=MATCHES_RIGHT_PANEL_MIN_LINES,
        ),
        Layout(
            name="miss_churn",
            ratio=RIGHT_PANEL_MISS_CHURN_RATIO,
            minimum_size=12,
        ),
    )
    _, _, miss_merged, _ = _merged_global_stats(accounts)
    persist_miss_churn_text(miss_merged)
    layout["matches_pane"].update(
        Panel(
            _render_combined_matches_table(accounts),
            title="Matches",
            border_style="green",
        )
    )
    layout["miss_churn"].update(
        Panel(
            render_miss_churn_table(miss_merged, max_rows=MISS_CHURN_TABLE_MAX_ROWS),
            title="Выдачи вне белого списка",
            border_style="yellow",
            padding=DASHBOARD_PANEL_PADDING,
        )
    )
    return layout


def _print_help() -> int:
    exit_code = 0
    try:
        parse_args(["--help"])
    except SystemExit as exc:
        exit_code = int(exc.code or 0)

    console = dashboard_console()
    console.print()
    console.print("Account environment variables (unlimited accounts):", style="bold")
    console.print("  Account 1: SEL_USERNAME / SEL_PASSWORD / SEL_ACCOUNT_ID [/ SEL_PROJECT_NAME|SEL_PROJECT_ID]")
    console.print("  Account 2: SEL2_USERNAME / SEL2_PASSWORD / SEL2_ACCOUNT_ID [/ SEL2_PROJECT_NAME|SEL2_PROJECT_ID]")
    console.print(f"  Account N: SELN_USERNAME / SELN_PASSWORD / SELN_ACCOUNT_ID  (N = 2..{MAX_ACCOUNTS})")
    console.print("Or set variables in .env (see .env.example) or selectel.additional_accounts in config.json")
    console.print("Optional labels: SEL1_LABEL, SEL2_LABEL, SEL3_LABEL, …")
    console.print("Optional per-account regions: SEL1_SCANNER_REGIONS, SEL2_SCANNER_REGIONS, …")
    console.print("  --rich-logs     — логи событий в stderr вместе с полноэкранным Rich")
    console.print("  --log-file PATH — тот же лог в файл внутри проекта, напр. temp/scanner.log")
    return exit_code


async def _bootstrap_account(
    settings: ScannerSettings,
    *,
    console: Console,
    args: argparse.Namespace,
    account_label: str,
    env_prefix: str,
    interactive_ui: bool,
) -> None:
    """Автодискавери проекта + проверка VM. Меняет settings inplace.

    Эта фаза выполняется ДО запуска Rich Live-дашборда, поэтому здесь безопасно
    показывать интерактивные диалоги (questionary / Rich Confirm).
    """
    assume_yes = bool(getattr(args, "assume_yes", False))
    auto_create = bool(getattr(args, "auto_create_project", True))
    auto_project_name = getattr(args, "auto_project_name", None) or "ip-roller"
    # На bootstrap-фазе Live ещё не запущен, поэтому интерактивные диалоги доступны
    # везде, где есть настоящий TTY. interactive_ui здесь не мешает.
    _ = interactive_ui

    await ensure_project_resolved(
        settings,
        console=console,
        account_label=account_label,
        dotenv_env_prefix=env_prefix,
        desired_name=auto_project_name,
        auto_create=auto_create,
        interactive=True,
    )
    await confirm_no_active_vms(
        settings,
        console=console,
        account_label=account_label,
        assume_yes=assume_yes,
        interactive=True,
    )


async def _run_single_scanner_from_dual(
    settings: ScannerSettings,
    *,
    console: Console,
    args: argparse.Namespace,
    interactive_ui: bool,
) -> int:
    emit_out, log_err, suppress_console, log_file_arg = _emit_log_flags(args, console, interactive_ui)
    app = SelectelScannerApp(
        settings,
        console=console,
        interactive=interactive_ui,
        emit_console_output=emit_out,
        emit_summary=not interactive_ui,
        log_to_stderr=log_err,
        log_file=log_file_arg,
        suppress_console_log=suppress_console,
    )
    return await app.run()


async def run_async(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if any(flag in args_list for flag in ("-h", "--help")):
        return _print_help()

    args = parse_args(args_list)
    console = dashboard_console()

    # Подгружаем .env ДО проверки needs_setup(), чтобы уже сохранённые значения
    # корректно считались в os.environ и wizard не запускался повторно.
    from dotenv import load_dotenv as _load_dotenv

    from .paths import DOTENV_PATH as _DOTENV_PATH

    _load_dotenv(_DOTENV_PATH, override=False)

    # Если .env ещё не заполнен — запускаем интерактивный wizard прямо здесь,
    # до любых сетевых вызовов. `--setup` форсит повторный запуск wizard.
    if getattr(args, "force_setup", False) or needs_setup():
        run_setup_wizard(console)

    # Превью whitelist — пользователь сразу видит, что защищено (объясняется «как тупому»).
    render_whitelist_banner(console, DEFAULT_WHITELIST_PATH)

    config = load_scanner_config(args.config_path)

    specs = _enumerate_accounts(config)
    if not specs:
        raise ValueError(
            "Не задан ни один аккаунт (SEL_*, SEL2_*, SEL3_*, … / config → selectel.api / additional_accounts)."
        )

    _render_mode_banner(console, specs)

    # Общие тайминги/пути (и креды аккаунта 1) собираем один раз; остальным аккаунтам
    # эти значения отдаются как база. require_project=False — проект может резолвиться ниже.
    base_settings = build_settings(args, allow_incomplete_primary=True, require_project=False)

    interactive_ui = bool(args.rich) or (console.is_terminal and sys.stdout.isatty())

    # Проверяем креды всех аккаунтов заранее: у кого не хватает — воркер не стартует,
    # причина попадёт в Recent Events (в мульти-режиме) или остановит запуск (в single).
    account_errs: dict[int, str | None] = {
        spec.index: _validate_account_credentials(
            f"Аккаунт {spec.index}", _account_credentials(spec), spec.prefix
        )
        for spec in specs
    }

    # Ровно один заданный аккаунт — обычный single-дашборд.
    if len(specs) == 1:
        spec = specs[0]
        if account_errs[spec.index]:
            raise ValueError(account_errs[spec.index])
        settings = _build_account_settings(base_settings, spec, regions=())
        await _bootstrap_account(
            settings,
            console=console,
            args=args,
            account_label=f"Аккаунт {spec.index}",
            env_prefix=spec.prefix,
            interactive_ui=interactive_ui,
        )
        await _prepare_account_regions(args, spec, settings)
        return await _run_single_scanner_from_dual(
            settings,
            console=console,
            args=args,
            interactive_ui=interactive_ui,
        )

    # Два и более аккаунта — мульти-дашборд с колонкой на каждый воркер.
    emit_out, log_err, suppress_console, log_file_arg = _emit_log_flags(args, console, interactive_ui)

    slots: list[WorkerSlot] = []
    for spec in specs:
        err = account_errs[spec.index]
        label_key = _account_label_env_key(spec.index)
        fallback_label = f"Аккаунт {spec.index}"
        app: SelectelScannerApp | None = None
        label = _slot_label(label_key, fallback_label, None, err=err)
        if not err:
            settings = _build_account_settings(base_settings, spec, regions=())
            await _bootstrap_account(
                settings,
                console=console,
                args=args,
                account_label=fallback_label,
                env_prefix=spec.prefix,
                interactive_ui=interactive_ui,
            )
            await _prepare_account_regions(args, spec, settings)
            label = _slot_label(label_key, fallback_label, settings, err=None)
            app = SelectelScannerApp(
                settings,
                console=console,
                interactive=False,
                emit_console_output=emit_out,
                emit_summary=not interactive_ui,
                log_to_stderr=log_err,
                log_file=log_file_arg,
                suppress_console_log=suppress_console,
            )
        slots.append(WorkerSlot(label=label, app=app, events=_slot_events(app, err)))

    active_apps = [s.app for s in slots if s.app is not None]
    refresh_per_second = max(
        (a.settings.live_refresh_per_second for a in active_apps),
        default=4,
    )
    tasks = [asyncio.create_task(app.run()) for app in active_apps]
    results: list[object] = []

    try:
        if interactive_ui:
            with Live(
                _build_dual_dashboard(slots),
                console=console,
                screen=True,
                refresh_per_second=refresh_per_second,
            ) as live:
                if tasks:
                    while not all(task.done() for task in tasks):
                        live.update(_build_dual_dashboard(slots))
                        await asyncio.sleep(1.0 / refresh_per_second)
                else:
                    try:
                        while True:
                            live.update(_build_dual_dashboard(slots))
                            await asyncio.sleep(1.0 / refresh_per_second)
                    except KeyboardInterrupt:
                        pass

            results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        else:
            results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
    finally:
        for app in active_apps:
            app.stop_event.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    errors = [result for result in results if isinstance(result, Exception)]
    if interactive_ui:
        for slot in slots:
            if slot.app is not None:
                console.print(
                    Panel(
                        f"{slot.label} | {slot.app.project_label}",
                        border_style="cyan",
                        padding=DASHBOARD_PANEL_PADDING,
                    )
                )
                slot.app._print_summary()
    if errors:
        raise errors[0]
    if not tasks:
        return 1
    return 0 if all(result == 0 for result in results) else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--rich" not in argv:
        argv = ["--rich", *argv]
    try:
        return asyncio.run(run_async(argv))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        console = dashboard_console_stderr()
        console.print(f"[bold red]Dual Selectel Scanner failed:[/] {exc}")
        return 1
