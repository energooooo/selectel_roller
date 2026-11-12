"""API единого списка аккаунтов и сопряжённого браузерного расширения.

Два контура авторизации:
  • accounts_router — управление списком из панели (cookie-сессия администратора);
  • router (/extension) — то, чем пользуется расширение по bearer-токену, плюс
    выдача/отзыв этих токенов из панели.

Пароли аккаунтов отдаются ТОЛЬКО расширению (bearer) для автоподстановки в форму
Selectel. В панель, статусы, аудит и логи они не попадают.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bus, extension
from app.auth.deps import Current, audit, current_admin
from app.core.crypto import decrypt, encrypt
from app.db.models import Account, AuditLog, ExtensionToken, SelectelKey, utcnow
from app.db.session import get_db, session_scope
from app.mailyget import MailError, check_mail
from app.onboarding import OnboardingError, OnboardingPaymentRequired, onboard_account

log = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["extension"])
accounts_router = APIRouter(prefix="/accounts", tags=["accounts"])

ACCOUNT_ID = r"^[0-9]{3,12}$"
EMAIL = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


# ── Список аккаунтов (панель, cookie) ────────────────────────────────────────

# Стадии входа через расширение — для отображения прогресса в панели.
LOGIN_STAGES = {"", "filling", "filled", "login", "otp_sent", "otp_wait", "code",
                "verifying", "connecting", "done", "error"}


def account_view(account: Account) -> dict[str, Any]:
    return {"id": account.id, "account_id": account.account_id, "email": account.email,
            "note": account.note, "onboarded": account.onboarded,
            "has_password": bool(account.password_enc), "last_login_at": account.last_login_at,
            "login_stage": account.login_stage, "login_message": account.login_message,
            "login_at": account.login_at, "created_at": account.created_at}


class AccountRow(BaseModel):
    account_id: str = Field(pattern=ACCOUNT_ID)
    email: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=256)
    note: str = Field(default="", max_length=200)


class ImportIn(BaseModel):
    accounts: list[AccountRow] = Field(min_length=1, max_length=2000)
    replace: bool = False


@accounts_router.get("")
async def list_accounts(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Account).order_by(Account.created_at))).scalars().all()
    return [account_view(row) for row in rows]


@accounts_router.post("/import")
async def import_accounts(body: ImportIn, current: Current = Depends(current_admin),
                          db: AsyncSession = Depends(get_db)) -> dict:
    existing = {a.account_id: a for a in (await db.execute(select(Account))).scalars().all()}
    if body.replace:
        keep = {row.account_id for row in body.accounts}
        for account_id, account in list(existing.items()):
            if account_id not in keep:
                await db.delete(account)
                existing.pop(account_id)
    added = updated = 0
    for row in body.accounts:
        email = row.email.strip()
        if email and not re.match(EMAIL, email):
            email = ""
        account = existing.get(row.account_id)
        if account is None:
            account = Account(account_id=row.account_id, email=email, note=row.note.strip(),
                              password_enc=encrypt(row.password) if row.password else None)
            db.add(account)
            existing[row.account_id] = account
            added += 1
        else:
            if email:
                account.email = email
            if row.note.strip():
                account.note = row.note.strip()
            if row.password:
                account.password_enc = encrypt(row.password)
            updated += 1
    await db.commit()
    await audit(current, "accounts.import", f"+{added} / ~{updated}",
                {"added": added, "updated": updated, "replace": body.replace})
    return {"added": added, "updated": updated}


@accounts_router.delete("/{account_id}")
async def delete_account(account_id: str, current: Current = Depends(current_admin),
                         db: AsyncSession = Depends(get_db)) -> dict:
    account = (await db.execute(select(Account).where(Account.account_id == account_id))).scalar_one_or_none()
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аккаунт не найден.")
    await db.delete(account)
    await db.commit()
    await audit(current, "accounts.delete", account_id)
    return {"ok": True}


# ── Сопряжение и токены расширения (панель, cookie) ──────────────────────────

@router.post("/pair")
async def create_pairing(response: Response, current: Current = Depends(current_admin)) -> dict:
    code, expires_in = extension.create_pairing(current.admin.id)
    response.headers["Cache-Control"] = "no-store"
    return {"code": code, "expires_in": expires_in}


@router.get("/tokens")
async def list_tokens(current: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(ExtensionToken).where(
        ExtensionToken.admin_id == current.admin.id, ExtensionToken.revoked.is_(False),
    ).order_by(ExtensionToken.created_at.desc()))).scalars().all()
    now = utcnow()
    return [{"id": r.id, "label": r.label, "created_at": r.created_at,
             "last_seen_at": r.last_seen_at, "expires_at": r.expires_at,
             "expired": r.expires_at <= now} for r in rows]


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: int, current: Current = Depends(current_admin),
                       db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(ExtensionToken, token_id)
    if row is None or row.admin_id != current.admin.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Токен расширения не найден.")
    row.revoked = True
    await db.commit()
    await audit(current, "extension.revoke", row.label or str(token_id))
    return {"ok": True}


# ── Контур расширения (bearer) ───────────────────────────────────────────────

class ClaimIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)
    label: str = Field(default="Браузерное расширение", max_length=100)


@router.post("/claim")
async def claim(body: ClaimIn, request: Request, response: Response,
                db: AsyncSession = Depends(get_db)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    label = body.label.strip() or "Браузерное расширение"
    token = await extension.claim_pairing(db, body.code, label)
    # Панель ждёт расширение на экране с кодом — сообщаем, что оно подключилось.
    bus.publish("extension", {"label": label})
    return {"token": token}


async def extension_auth(authorization: str = Header(default=""),
                         db: AsyncSession = Depends(get_db)) -> ExtensionToken:
    return await extension.current_extension(db, authorization)


@router.get("/accounts")
async def extension_accounts(response: Response, token: ExtensionToken = Depends(extension_auth),
                             db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Список аккаунтов С паролями — для автоподстановки в форму входа Selectel."""
    response.headers["Cache-Control"] = "no-store"
    rows = (await db.execute(select(Account).order_by(Account.created_at))).scalars().all()
    return [{"account_id": r.account_id, "email": r.email,
             "password": decrypt(r.password_enc) if r.password_enc else "",
             "onboarded": r.onboarded} for r in rows]


class ExtMailCodeIn(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL)
    cursor: str | None = Field(default=None, min_length=1, max_length=100_000)


@router.post("/mail-code")
async def extension_mail_code(body: ExtMailCodeIn, response: Response,
                             token: ExtensionToken = Depends(extension_auth)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await check_mail(body.email, body.cursor)
    except MailError as exc:
        raise HTTPException(422, str(exc), headers={"Cache-Control": "no-store"}) from exc


class ProgressIn(BaseModel):
    account_id: str = Field(pattern=ACCOUNT_ID)
    stage: str = Field(max_length=24)
    message: str = Field(default="", max_length=300)


@router.post("/progress")
async def extension_progress(body: ProgressIn, response: Response,
                            token: ExtensionToken = Depends(extension_auth),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """Расширение сообщает шаг входа — панель показывает его живьём через SSE."""
    response.headers["Cache-Control"] = "no-store"
    if body.stage not in LOGIN_STAGES:
        raise HTTPException(422, "Неизвестная стадия входа.")
    account = (await db.execute(select(Account).where(Account.account_id == body.account_id))).scalar_one_or_none()
    if account is None:
        raise HTTPException(404, "Аккаунт не найден в панели.")
    account.login_stage = body.stage
    account.login_message = body.message
    account.login_at = utcnow()
    await db.commit()
    bus.publish("account", account_view(account))
    return {"ok": True}


class ExtOnboardIn(BaseModel):
    account_id: str = Field(pattern=ACCOUNT_ID)
    iam_token: str = Field(min_length=1, max_length=8192)
    project_name: str = Field(default="", max_length=64)


@router.post("/onboard")
async def extension_onboard(body: ExtOnboardIn, response: Response,
                            token: ExtensionToken = Depends(extension_auth),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """Расширение забрало IAM-токен после входа в Selectel — завершаем онбординг ключа."""
    response.headers["Cache-Control"] = "no-store"
    iam = body.iam_token.strip()
    if not iam or not iam.isascii() or not iam.isprintable() or " " in iam:
        raise HTTPException(422, "IAM-токен выглядит некорректно.")
    try:
        key, added = await onboard_account(body.account_id, None, body.project_name, iam)
    except OnboardingPaymentRequired as exc:
        raise HTTPException(402, str(exc)) from exc
    except OnboardingError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.error("Extension onboarding failed: %s", type(exc).__name__)
        raise HTTPException(502, "Не удалось подключить аккаунт Selectel.") from exc

    account = (await db.execute(select(Account).where(Account.account_id == body.account_id))).scalar_one_or_none()
    if account is not None:
        account.onboarded = True
        account.last_login_at = utcnow()
        account.login_stage = "done"
        account.login_message = (f"Ключ «{key.name}» подключён." if added
                                 else f"Ключ «{key.name}» уже был подключён.")
        account.login_at = utcnow()
        await db.commit()
        bus.publish("account", account_view(account))
    if added:
        async with session_scope() as audit_db:
            audit_db.add(AuditLog(admin_id=token.admin_id, ip="extension", action="key.create",
                                  target=f"{key.name} ({body.account_id})",
                                  details={"project": key.project_name, "method": "extension"}))
        bus.publish("key", {"id": key.id, "account_id": key.account_id, "name": key.name})
    return {"added": added, "key": {"id": key.id, "name": key.name, "account_id": key.account_id,
                                    "project_name": key.project_name}}
