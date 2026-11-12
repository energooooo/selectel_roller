"""Ключи Selectel: список, автодобавление, управление, floating IP на аккаунте."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import auto_onboarding, browser_onboarding, bus, metrics, settings_store
from app.auth.deps import Current, audit, current_admin
from app.core.crypto import decrypt, encrypt
from app.db.models import CaughtIP, Proxy, SelectelKey, Subnet, utcnow
from app.db.session import get_db, session_scope
from app.engine.manager import TERMINAL, manager
from app.engine.proxy import choose_random_proxy, with_session
from app.engine.selectel import SelectelClient
from app.engine.worker import resolve_project, short_exc
from app.mailyget import MailError, check_mail
from app.onboarding import OnboardingError, OnboardingPaymentRequired, onboard_account

log = logging.getLogger(__name__)

router = APIRouter(prefix="/keys", tags=["keys"])

def key_view(key: SelectelKey, proxy: Proxy | None = None, held: int = 0) -> dict[str, Any]:
    runtime = manager.runtime(key.id)
    return {
        "id": key.id, "name": key.name, "account_id": key.account_id, "username": key.username,
        "auth_type": key.auth_type, "project_name": key.project_name, "project_id": key.project_id,
        "enabled": key.enabled, "status": key.status, "status_reason": key.status_reason,
        "proxy_mode": key.proxy_mode, "proxy_id": key.proxy_id,
        "proxy": ({"id": proxy.id, "display": proxy.display, "label": proxy.label, "status": proxy.status,
                   "exit_ip": proxy.exit_ip} if proxy else None),
        "ip_goal": key.ip_goal, "ip_goal_effective": key.ip_goal or int(settings_store.get("ip_goal") or 1),
        "attempts_total": key.attempts_total, "caught_total": key.caught_total,
        "held_ips": held, "last_ok_at": key.last_ok_at, "last_caught_at": key.last_caught_at,
        "created_at": key.created_at, "rate_per_min": metrics.rate_per_minute(key.id),
        "runtime": runtime,
        "pace": (runtime or {}).get("pace", key.pace or 1.0),
        "pace_reason": (runtime or {}).get("pace_reason", key.pace_reason or ""),
    }

async def _view(db: AsyncSession, key: SelectelKey) -> dict[str, Any]:
    proxy = await db.get(Proxy, key.proxy_id) if key.proxy_id else None
    held = (await db.execute(select(func.count(CaughtIP.id)).where(
        CaughtIP.key_id == key.id, CaughtIP.on_account.is_(True)))).scalar_one()
    return key_view(key, proxy, held)

@router.get("")
async def list_keys(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    held = (select(CaughtIP.key_id, func.count().label("total"))
            .where(CaughtIP.on_account.is_(True), CaughtIP.key_id.is_not(None))
            .group_by(CaughtIP.key_id).subquery())
    rows = await db.execute(
        select(SelectelKey, Proxy, func.coalesce(held.c.total, 0))
        .outerjoin(Proxy, Proxy.id == SelectelKey.proxy_id)
        .outerjoin(held, held.c.key_id == SelectelKey.id)
        .order_by(SelectelKey.id)
    )
    return [key_view(key, proxy, count) for key, proxy, count in rows]

class KeyIn(BaseModel):
    account_id: str = Field(pattern=r"^\d{3,12}$")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)
    project_name: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=100)
    proxy_mode: Literal["auto", "fixed", "none"] = "auto"
    proxy_id: int | None = None
    ip_goal: int | None = Field(default=None, ge=1, le=50)
    start: bool = True

async def _pick_proxy_url(db: AsyncSession, body: KeyIn) -> tuple[str | None, int | None]:
    if body.proxy_mode == "none":
        return None, None
    if body.proxy_mode == "fixed":
        proxy = await db.get(Proxy, body.proxy_id) if body.proxy_id else None
        if proxy is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Выберите прокси для ключа")
    else:
        proxies = [p for p in (await db.execute(select(Proxy))).scalars() if p.status in {"ok", "partial", "unchecked"}]
        if not proxies:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "Нет рабочих прокси. Добавьте прокси или выберите «Без прокси».")
        proxy = choose_random_proxy(proxies)
    url = with_session(decrypt(proxy.url_enc), proxy.session_mode, "tr" + secrets.token_hex(8))
    return url, proxy.id

async def _create_key(db: AsyncSession, body: KeyIn, current: Current) -> dict[str, Any]:
    """Автодобавление: вход в Selectel → проект (найти или создать) → проверка
    доступа к облаку → сохранение → сразу в охоту."""
    dup = (await db.execute(select(SelectelKey).where(
        SelectelKey.account_id == body.account_id, SelectelKey.username == body.username))).scalar()
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Такой ключ уже есть: {dup.name}")
    proxy_url, proxy_id = await _pick_proxy_url(db, body)
    try:
        project_id, project_name = await resolve_project(body.username, body.password, body.account_id,
                                                         proxy_url, body.project_name.strip())
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        msg = ("Selectel не принял данные. Проверьте номер аккаунта, имя сервисного пользователя и пароль"
               if code in (401, 403) else f"Selectel ответил {code} при поиске проекта")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg) from exc
    except httpx.TransportError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Нет связи с Selectel: {short_exc(exc)}") from exc

    client = SelectelClient(username=body.username, password=body.password, account_id=body.account_id,
                            project_id=project_id, request_timeout=20, connect_timeout=8, proxy=proxy_url)
    try:
        await client.ensure_authenticated()
        regions = sorted(client.available_regions())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "У пользователя нет доступа к проекту облака — дай ему роль в проекте "
                            f"«{project_name}» (Управление доступом)") from exc
    finally:
        await client.close()

    key = SelectelKey(
        name=body.name.strip() or body.username, account_id=body.account_id, username=body.username,
        password_enc=encrypt(body.password), project_id=project_id, project_name=project_name,
        enabled=body.start, status="starting" if body.start else "paused",
        status_reason="" if body.start else "Добавлен без запуска",
        proxy_mode=body.proxy_mode, proxy_id=proxy_id, ip_goal=body.ip_goal,
    )
    db.add(key)
    await db.commit()
    await audit(current, "key.create", f"{key.name} ({key.account_id})", {"project": project_name})
    await manager.reconcile()
    view = await _view(db, key)
    bus.publish("key", view)
    return {**view, "regions": regions}

@router.post("")
async def create_key(body: KeyIn, current: Current = Depends(current_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    return await _create_key(db, body, current)

class MailCodeIn(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    cursor: str | None = Field(default=None, min_length=1, max_length=100_000)

@router.post("/onboard/mail-code")
async def onboarding_mail_code(body: MailCodeIn, response: Response,
                               _: Current = Depends(current_admin)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await check_mail(body.email, body.cursor)
    except MailError as exc:
        raise HTTPException(422, str(exc), headers={"Cache-Control": "no-store"}) from exc

class AccountIn(BaseModel):
    account_id: str = Field(pattern=r"^[0-9]{3,12}$")

class OnboardIn(AccountIn):
    proxy_id: int | None = Field(default=None, gt=0)
    project_name: str = Field(default="", max_length=64)

class AutoOnboardIn(OnboardIn):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback

def _browser_available(request: Request) -> bool:
    if request.client is None or not _is_loopback(request.client.host):
        return False
    origin = request.headers.get("origin")
    forwarded_host = request.headers.get("x-forwarded-host")
    try:
        if not _is_loopback(request.url.hostname or ""):
            return False
        if origin and not _is_loopback(urlsplit(origin).hostname or ""):
            return False
        if forwarded_host and not _is_loopback(urlsplit(f"//{forwarded_host}").hostname or ""):
            return False
    except ValueError:
        return False
    return browser_onboarding.available()

@router.get("/onboard/browser")
async def browser_onboard_available(request: Request, _: Current = Depends(current_admin)) -> dict:
    return {"available": _browser_available(request)}

@router.post("/onboard/browser")
async def browser_onboard_start(body: AccountIn, request: Request, current: Current = Depends(current_admin)) -> dict:
    if not _browser_available(request):
        raise HTTPException(503, "Вход в Chromium доступен при локальном запуске панели на компьютере с Playwright.")
    try:
        return browser_onboarding.start(current.session.id, body.account_id).view()
    except browser_onboarding.BrowserBusy as exc:
        raise HTTPException(409, str(exc)) from exc

def _browser_job(job_id: str, current: Current) -> browser_onboarding.BrowserJob:
    job = browser_onboarding.jobs.get(job_id)
    if job is None or job.owner != current.session.id:
        raise HTTPException(404, "Окно входа не найдено. Откройте его снова.")
    return job

@router.get("/onboard/browser/{job_id}")
async def browser_onboard_status(job_id: str, current: Current = Depends(current_admin)) -> dict:
    return _browser_job(job_id, current).view()

@router.delete("/onboard/browser/{job_id}")
async def browser_onboard_cancel(job_id: str, current: Current = Depends(current_admin)) -> dict:
    job = _browser_job(job_id, current)
    await browser_onboarding.cancel(job)
    return job.view()


@router.post("/onboard")
async def onboard(
    body: OnboardIn,
    current: Current = Depends(current_admin),
    db: AsyncSession = Depends(get_db),
    authorization: str = Header(default=""),
) -> dict:
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or not token.isascii() or not token.isprintable() or " " in token:
        raise HTTPException(422, "Введите IAM-токен с областью доступа «Аккаунт».")
    try:
        key, added = await onboard_account(body.account_id, body.proxy_id, body.project_name, token)
    except OnboardingPaymentRequired as exc:
        raise HTTPException(402, str(exc)) from exc
    except OnboardingError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.error("IAM onboarding failed: %s", type(exc).__name__)
        raise HTTPException(502, "Не удалось подключить аккаунт Selectel.") from exc

    view = await _view(db, key)
    if added:
        await audit(current, "key.create", f"{key.name} ({key.account_id})", {"project": key.project_name})
        bus.publish("key", view)
    return {"key": view, "added": added}


@router.post("/onboard/auto")
async def auto_onboard(body: AutoOnboardIn, _: Current = Depends(current_admin)) -> dict:
    raise HTTPException(409, "Войдите в панель Selectel и подключите аккаунт через IAM-токен.")


def _auto_job(job_id: str, current: Current) -> auto_onboarding.Job:
    job = auto_onboarding.jobs.get(job_id)
    if job is None or job.owner != current.admin.id:
        raise HTTPException(404, "Подключение не найдено.")
    return job


@router.get("/onboard/auto/{job_id}")
async def auto_onboard_status(job_id: str, current: Current = Depends(current_admin)) -> dict:
    return _auto_job(job_id, current).view()


@router.delete("/onboard/auto/{job_id}")
async def auto_onboard_cancel(job_id: str, current: Current = Depends(current_admin)) -> dict:
    job = _auto_job(job_id, current)
    if job.stage == "connecting":
        raise HTTPException(409, "Дождитесь завершения подключения проекта.")
    if job.stage not in auto_onboarding.TERMINAL:
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        if job.stage not in auto_onboarding.TERMINAL:
            job.set("cancelled", "Подключение отменено.")
    return job.view()


class BulkIn(BaseModel):
    text: str = Field(max_length=20_000)
    proxy_mode: Literal["auto", "fixed", "none"] = "auto"
    start: bool = True


@router.post("/bulk")
async def create_keys_bulk(body: BulkIn, current: Current = Depends(current_admin),
                           db: AsyncSession = Depends(get_db)) -> list[dict]:
    """По ключу в строке: «аккаунт пользователь пароль [проект]» (через пробел, табуляцию или ;)."""
    results = []
    for n, raw in enumerate(body.text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(";", " ").replace("\t", " ").split()
        if len(parts) < 3:
            results.append({"line": n, "ok": False, "error": "нужно: аккаунт пользователь пароль [проект]"})
            continue
        try:
            view = await _create_key(db, KeyIn(account_id=parts[0], username=parts[1], password=parts[2],
                                               project_name=" ".join(parts[3:]), proxy_mode=body.proxy_mode,
                                               start=body.start), current)
            results.append({"line": n, "ok": True, "name": view["name"]})
        except HTTPException as exc:
            results.append({"line": n, "ok": False, "error": exc.detail})
        except Exception as exc:  # noqa: BLE001
            results.append({"line": n, "ok": False, "error": short_exc(exc)})
    return results


class KeyPatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    password: str | None = Field(default=None, max_length=256)
    proxy_mode: Literal["auto", "fixed", "none"] | None = None
    proxy_id: int | None = None
    ip_goal: int | None = Field(default=None, ge=0, le=50)  # 0 = из общих настроек


@router.patch("/{key_id}")
async def update_key(key_id: int, body: KeyPatch, current: Current = Depends(current_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    key = await db.get(SelectelKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого ключа")
    restart = False
    if body.name is not None:
        key.name = body.name.strip() or key.name
    if body.password:
        key.password_enc = encrypt(body.password)
        restart = True
        if key.status == "auth_failed":
            key.status, key.status_reason = "paused", "пароль обновлён — включи ключ"
    if body.proxy_mode is not None:
        key.proxy_mode = body.proxy_mode
        key.proxy_id = body.proxy_id if body.proxy_mode == "fixed" else (None if body.proxy_mode == "none" else key.proxy_id)
        key.proxy_session_gen = 0
        restart = True
    if body.ip_goal is not None:
        key.ip_goal = body.ip_goal or None
    await db.commit()
    await audit(current, "key.update", key.name, body.model_dump(exclude={"password"}, exclude_none=True))
    if restart and key.enabled:
        await manager.restart_key(key.id)
    view = await _view(db, key)
    bus.publish("key", view)
    return view


async def _set_enabled(db: AsyncSession, key_id: int, enabled: bool, current: Current) -> dict:
    key = await db.get(SelectelKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого ключа")
    key.enabled = enabled
    if enabled:
        key.status, key.status_reason = "starting", ""
    else:
        key.status, key.status_reason = "paused", "на паузе"
    await db.commit()
    await audit(current, "key.start" if enabled else "key.pause", key.name)
    await manager.restart_key(key.id) if enabled else await manager.reconcile()
    view = await _view(db, key)
    bus.publish("key", view)
    return view


@router.post("/{key_id}/start")
async def start_key(key_id: int, current: Current = Depends(current_admin),
                    db: AsyncSession = Depends(get_db)) -> dict:
    return await _set_enabled(db, key_id, True, current)


@router.post("/{key_id}/pause")
async def pause_key(key_id: int, current: Current = Depends(current_admin),
                    db: AsyncSession = Depends(get_db)) -> dict:
    return await _set_enabled(db, key_id, False, current)


class PauseKeysIn(BaseModel):
    # Только явный null означает «все»; пустой список не должен останавливать поиск.
    key_ids: list[int] | None = Field(min_length=1)


@router.post("/pause")
async def pause_keys(body: PauseKeysIn, current: Current = Depends(current_admin),
                     db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    stmt = update(SelectelKey).where(SelectelKey.enabled.is_(True), SelectelKey.status.not_in(TERMINAL))
    if body.key_ids is not None:
        stmt = stmt.where(SelectelKey.id.in_(set(body.key_ids)))
    result = await db.scalars(
        stmt.values(enabled=False, status="paused", status_reason="на паузе").returning(SelectelKey.id)
    )
    ids = sorted(result.all())
    await db.commit()
    if ids:
        await manager.reconcile()
        await audit(current, "key.pause_bulk", "all" if body.key_ids is None else "selected",
                    {"key_ids": ids, "count": len(ids)})
        bus.publish("keys_paused", {"count": len(ids)})
    return {"paused": len(ids)}


@router.post("/{key_id}/rotate-proxy")
async def rotate_proxy(key_id: int, current: Current = Depends(current_admin),
                       db: AsyncSession = Depends(get_db)) -> dict:
    """Пересадить ключ на другой прокси (auto) или на новую липкую сессию."""
    key = await db.get(SelectelKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого ключа")
    if key.proxy_mode == "auto":
        key.proxy_id = None
    key.proxy_session_gen = (key.proxy_session_gen or 0) + 1
    await db.commit()
    await audit(current, "key.rotate_proxy", key.name)
    if key.enabled:
        await manager.restart_key(key.id)
    return await _view(db, key)


@router.post("/{key_id}/reset-pace")
async def reset_pace(key_id: int, current: Current = Depends(current_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """Вернуть темп ключа к настройкам (×1) — например, когда бан точно снят."""
    key = await db.get(SelectelKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого ключа")
    key.pace, key.pace_reason = 1.0, ""
    await db.commit()
    await manager.reset_pace(key_id)
    await audit(current, "key.reset_pace", key.name)
    return await _view(db, key)


@router.delete("/{key_id}")
async def delete_key(key_id: int, current: Current = Depends(current_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """Ключ удаляется из панели. IP на аккаунте Selectel не трогаются, история поимок остаётся."""
    key = await db.get(SelectelKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого ключа")
    key.enabled = False
    await db.commit()
    await manager.reconcile()
    name = key.name
    await db.delete(key)
    await db.commit()
    await audit(current, "key.delete", name)
    bus.publish("key_deleted", {"id": key_id})
    return {"ok": True}


# ── floating IP на аккаунте ─────────────────────────────────────────────────

@router.get("/{key_id}/floating-ips")
async def floating_ips(key_id: int, _: Current = Depends(current_admin)) -> dict:
    """Живой список floating IP аккаунта + сверка с пойманными: ушедшие помечаются,
    белые адреса, которых не ловили (подобраны вручную), добавляются в историю."""
    try:
        client, owned = await manager.client_for_key(key_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Не удалось войти в Selectel: {short_exc(exc)}") from exc
    try:
        records = await client.list_floating_ips()
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Selectel не ответил: {short_exc(exc.__cause__ or exc)}") from exc
    finally:
        if owned:
            await client.close()

    async with session_scope() as db:
        key = await db.get(SelectelKey, key_id)
        subnets = [(s, ipaddress.ip_network(s.cidr, strict=False)) for s in (await db.execute(select(Subnet))).scalars()]
        caught = {c.floating_ip_id: c for c in (await db.execute(select(CaughtIP).where(
            CaughtIP.account_id == key.account_id))).scalars() if c.floating_ip_id}
        caught_by_ip = {c.ip: c for c in caught.values()}
        live_ids = {r.id for r in records}
        for c in caught.values():
            if c.on_account and c.floating_ip_id not in live_ids:
                c.on_account, c.released_at = False, utcnow()
        items = []
        for r in records:
            subnet = next((s for s, net in subnets if _in(r.address, net)), None)
            c = caught.get(r.id) or caught_by_ip.get(r.address)
            if c is None and subnet is not None:
                c = CaughtIP(ip=r.address, region=r.region, key_id=key.id, account_id=key.account_id,
                             subnet_id=subnet.id, floating_ip_id=r.id, protected=True, on_account=True,
                             source="adopted", note="найден на аккаунте")
                db.add(c)
                await db.flush()
            elif c is not None and not c.on_account:
                c.on_account, c.released_at, c.floating_ip_id = True, None, r.id
            items.append({
                "floating_ip_id": r.id, "ip": r.address, "region": r.region, "status": r.status,
                "attached": bool(r.port_id or r.fixed_ip_address), "white": subnet is not None,
                "subnet": subnet.cidr if subnet else None,
                "caught": ({"id": c.id, "caught_at": c.caught_at, "protected": c.protected,
                            "check_status": c.check_status, "source": c.source} if c else None),
            })
    items.sort(key=lambda i: (not i["white"], i["region"], i["ip"]))
    return {"key_id": key_id, "items": items}


def _in(address: str, net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    try:
        return ipaddress.ip_address(address) in net
    except ValueError:
        return False


class DeleteFipIn(BaseModel):
    confirm_ip: str


@router.post("/{key_id}/floating-ips/{fip_id}/delete")
async def delete_floating_ip(key_id: int, fip_id: str, body: DeleteFipIn,
                             current: Current = Depends(current_admin)) -> dict:
    """Удаление floating IP руками. Защищённый пойманный IP не удаляется,
    подтверждение — ввод самого адреса."""
    async with session_scope() as db:
        protected = (await db.execute(select(CaughtIP).where(
            CaughtIP.floating_ip_id == fip_id, CaughtIP.protected.is_(True)))).scalar()
    if protected:
        raise HTTPException(status.HTTP_409_CONFLICT, "IP защищён «не удалять» — сначала сними защиту")
    client, owned = await manager.client_for_key(key_id)
    try:
        record = await client.get_floating_ip(fip_id)
        if record.address != body.confirm_ip.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Адрес для подтверждения не совпал")
        deleted = await client.delete_floating_ip(fip_id)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Selectel не ответил: {short_exc(exc)}") from exc
    finally:
        if owned:
            await client.close()
    async with session_scope() as db:
        for c in (await db.execute(select(CaughtIP).where(CaughtIP.floating_ip_id == fip_id))).scalars():
            c.on_account, c.released_at = False, utcnow()
    await audit(current, "fip.delete", body.confirm_ip, {"key_id": key_id})
    if deleted:
        await manager.reconcile()
    return {"ok": deleted}


__all__ = ["router", "TERMINAL"]
