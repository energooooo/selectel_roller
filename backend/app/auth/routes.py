"""Вход в панель.

Защита:
- argon2id-хеш пароля, минимум 12 символов, отсев слабых;
- 2FA TOTP + резервные коды;
- блокировка IP на 15 мин после 5 неудач, растущая задержка ответа на каждую неудачу,
  глобальный тормоз при массовом переборе с разных адресов;
- одинаковый ответ на «нет логина» и «не тот пароль»;
- сессии в БД, cookie httpOnly + SameSite=Strict (+ Secure на проде), отзыв сессий;
- первый админ создаётся только с одноразовым setup-токеном из лога сервера;
- уведомления в Telegram о входе с нового адреса и о блокировке.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import COOKIE_NAME, SESSION_TTL, Current, audit, csrf_guard, current_admin
from app.auth.security import (
    DUMMY_HASH, client_ip, hash_password, new_backup_codes, new_token, new_totp_secret, password_problem,
    token_hash, totp_uri, verify_password, verify_totp,
)
from app.core.config import BACKEND_DIR, get_settings
from app.core.crypto import decrypt, encrypt
from app.db.models import Admin, AuditLog, LoginAttempt, Session, utcnow
from app.db.session import get_db
from app.notify.telegram import esc, notify

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILS = 5
LOCK_WINDOW = timedelta(minutes=15)
GLOBAL_WINDOW = timedelta(minutes=10)
GLOBAL_FAILS = 40
SETUP_TOKEN_PATH = BACKEND_DIR / "data" / "setup-token.txt"
_setup_token: str | None = None


async def ensure_setup_token(db: AsyncSession) -> None:
    """Пока админа нет — печатаем одноразовый токен создания (лог + файл в data/)."""
    global _setup_token
    has_admin = (await db.execute(select(func.count(Admin.id)))).scalar_one() > 0
    if has_admin:
        _setup_token = None
        SETUP_TOKEN_PATH.unlink(missing_ok=True)
        return
    _setup_token = secrets.token_urlsafe(18)
    SETUP_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETUP_TOKEN_PATH.write_text(_setup_token, encoding="ascii")
    log.warning("Админа ещё нет. Токен создания: %s (он же в %s)", _setup_token, SETUP_TOKEN_PATH)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=get_settings().production,
        samesite="strict",
        path="/",
    )


async def _open_session(db: AsyncSession, admin: Admin, request: Request, response: Response) -> None:
    token = new_token()
    now = utcnow()
    db.add(Session(token_hash=token_hash(token), admin_id=admin.id, ip=client_ip(request),
                   user_agent=request.headers.get("user-agent", "")[:300], created_at=now,
                   last_seen_at=now, expires_at=now + SESSION_TTL))
    await db.commit()
    _set_cookie(response, token)


async def _record(db: AsyncSession, request: Request, login: str, ok: bool, reason: str) -> None:
    db.add(LoginAttempt(ip=client_ip(request), login=login[:64], ok=ok, reason=reason,
                        user_agent=request.headers.get("user-agent", "")[:300]))
    await db.commit()


async def _recent_fails(db: AsyncSession, ip: str) -> tuple[int, int]:
    """(неудач с этого IP после последнего успеха за окно, неудач со всех IP за глобальное окно)."""
    now = utcnow()
    last_ok = (await db.execute(select(func.max(LoginAttempt.ts)).where(
        LoginAttempt.ip == ip, LoginAttempt.ok.is_(True)))).scalar()
    since = max(filter(None, [now - LOCK_WINDOW, last_ok]))
    ip_fails = (await db.execute(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.ip == ip, LoginAttempt.ok.is_(False), LoginAttempt.ts > since))).scalar_one()
    all_fails = (await db.execute(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.ok.is_(False), LoginAttempt.ts > now - GLOBAL_WINDOW))).scalar_one()
    return ip_fails, all_fails


# ── Схемы ────────────────────────────────────────────────────────────────────

class SetupIn(BaseModel):
    setup_token: str
    login: str = Field(min_length=3, max_length=64)
    password: str = Field(max_length=256)


class LoginIn(BaseModel):
    login: str = Field(max_length=64)
    password: str = Field(max_length=256)
    code: str = Field(default="", max_length=32)  # TOTP или резервный код


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(max_length=256)


class TotpEnableIn(BaseModel):
    code: str


class TotpDisableIn(BaseModel):
    password: str
    code: str


# ── Маршруты ────────────────────────────────────────────────────────────────

@router.get("/state")
async def state(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Что показать на экране входа: мастер создания или обычный вход, и вошёл ли уже."""
    has_admin = (await db.execute(select(func.count(Admin.id)))).scalar_one() > 0
    if not has_admin and _setup_token is None:
        await ensure_setup_token(db)
    authed = False
    token = request.cookies.get(COOKIE_NAME)
    if token:
        session = (await db.execute(select(Session).where(Session.token_hash == token_hash(token)))).scalar()
        authed = bool(session and not session.revoked and session.expires_at > utcnow())
    return {"needs_setup": not has_admin, "authenticated": authed}


@router.post("/setup")
async def setup(body: SetupIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    csrf_guard(request)
    if (await db.execute(select(func.count(Admin.id)))).scalar_one() > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Админ уже создан")
    if not _setup_token or not secrets.compare_digest(body.setup_token.strip(), _setup_token):
        await _record(db, request, body.login, False, "bad_setup_token")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный код создания. Он лежит в файле backend/data/setup-token.txt")
    problem = password_problem(body.password, body.login)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    admin = Admin(login=body.login.strip(), password_hash=hash_password(body.password))
    db.add(admin)
    await db.commit()
    await ensure_setup_token(db)
    await _record(db, request, admin.login, True, "setup")
    await _open_session(db, admin, request, response)
    await audit(None, "admin.setup", admin.login, ip=client_ip(request))
    return {"ok": True}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    csrf_guard(request)
    ip = client_ip(request)
    ip_fails, all_fails = await _recent_fails(db, ip)
    if ip_fails >= MAX_FAILS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Слишком много неудачных попыток. Вход с этого адреса закрыт на 15 минут.")
    # Растущая задержка: перебор становится бессмысленно медленным ещё до блокировки.
    await asyncio.sleep(min(0.4 * (2 ** ip_fails), 6.0) + (2.0 if all_fails >= GLOBAL_FAILS else 0.0))

    admin = (await db.execute(select(Admin).where(Admin.login == body.login.strip()))).scalar_one_or_none()
    ok = verify_password(admin.password_hash if admin else DUMMY_HASH, body.password)
    if not admin or not ok:
        await _record(db, request, body.login, False, "bad_credentials")
        if ip_fails + 1 >= MAX_FAILS:
            await notify("login", f"🚫 Перебор пароля: адрес <code>{esc(ip)}</code> заблокирован на 15 минут.")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    if admin.totp_enabled:
        code = body.code.strip()
        if not code:
            # Пароль верный, нужен второй фактор. Попытку не считаем неудачной.
            return {"ok": False, "need_code": True}
        secret = decrypt(admin.totp_secret_enc)
        if not verify_totp(secret, code):
            used = next((h for h in admin.backup_codes or [] if verify_password(h, code.lower())), None)
            if used is None:
                await _record(db, request, body.login, False, "bad_code")
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный код")
            admin.backup_codes = [h for h in admin.backup_codes if h != used]
            await audit(None, "auth.backup_code_used", admin.login, ip=ip)

    known_ip = (await db.execute(select(func.count(LoginAttempt.id)).where(
        LoginAttempt.ip == ip, LoginAttempt.ok.is_(True)))).scalar_one() > 0
    await _record(db, request, admin.login, True, "ok")
    await _open_session(db, admin, request, response)
    if not known_ip:
        ua = request.headers.get("user-agent", "")[:120]
        await notify("login", f"🔐 Вход в панель с нового адреса <code>{esc(ip)}</code>\n{esc(ua)}")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response, current: Current = Depends(current_admin),
                 db: AsyncSession = Depends(get_db)) -> dict:
    session = await db.get(Session, current.session.id)
    if session:
        session.revoked = True
        await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(current: Current = Depends(current_admin)) -> dict:
    return {
        "login": current.admin.login,
        "totp_enabled": current.admin.totp_enabled,
        "backup_codes_left": len(current.admin.backup_codes or []),
        "password_changed_at": current.admin.password_changed_at,
        "session_id": current.session.id,
    }


@router.post("/password")
async def change_password(body: PasswordIn, current: Current = Depends(current_admin),
                          db: AsyncSession = Depends(get_db)) -> dict:
    admin = await db.get(Admin, current.admin.id)
    if not verify_password(admin.password_hash, body.current_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Текущий пароль не тот")
    problem = password_problem(body.new_password, admin.login)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    admin.password_hash = hash_password(body.new_password)
    admin.password_changed_at = utcnow()
    # Все прочие сессии — завершить: смена пароля обычно значит «кто-то мог узнать старый».
    for session in (await db.execute(select(Session).where(
            Session.admin_id == admin.id, Session.id != current.session.id))).scalars():
        session.revoked = True
    await db.commit()
    await audit(current, "auth.password_changed")
    return {"ok": True}


@router.post("/totp/setup")
async def totp_setup(current: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    admin = await db.get(Admin, current.admin.id)
    if admin.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA уже включена")
    secret = new_totp_secret()
    admin.totp_secret_enc = encrypt(secret)
    await db.commit()
    return {"secret": secret, "uri": totp_uri(secret, admin.login)}


@router.post("/totp/enable")
async def totp_enable(body: TotpEnableIn, current: Current = Depends(current_admin),
                      db: AsyncSession = Depends(get_db)) -> dict:
    admin = await db.get(Admin, current.admin.id)
    if not admin.totp_secret_enc or not verify_totp(decrypt(admin.totp_secret_enc), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код не подошёл. Проверьте, что время на телефоне выставлено точно")
    codes = new_backup_codes()
    admin.totp_enabled = True
    admin.backup_codes = [hash_password(c) for c in codes]
    await db.commit()
    await audit(current, "auth.totp_enabled")
    return {"backup_codes": codes}


@router.post("/totp/disable")
async def totp_disable(body: TotpDisableIn, current: Current = Depends(current_admin),
                       db: AsyncSession = Depends(get_db)) -> dict:
    admin = await db.get(Admin, current.admin.id)
    if not verify_password(admin.password_hash, body.password) or not verify_totp(
            decrypt(admin.totp_secret_enc), body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пароль или код не подошли")
    admin.totp_enabled = False
    admin.totp_secret_enc = None
    admin.backup_codes = []
    await db.commit()
    await audit(current, "auth.totp_disabled")
    return {"ok": True}


@router.get("/sessions")
async def sessions(current: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Session).where(
        Session.admin_id == current.admin.id, Session.revoked.is_(False), Session.expires_at > utcnow(),
    ).order_by(Session.last_seen_at.desc()))).scalars()
    return [{"id": s.id, "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at,
             "last_seen_at": s.last_seen_at, "current": s.id == current.session.id} for s in rows]


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: int, current: Current = Depends(current_admin),
                         db: AsyncSession = Depends(get_db)) -> dict:
    session = await db.get(Session, session_id)
    if not session or session.admin_id != current.admin.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такой сессии")
    session.revoked = True
    await db.commit()
    await audit(current, "auth.session_revoked", str(session_id))
    return {"ok": True}


@router.get("/attempts")
async def attempts(limit: int = 100, _: Current = Depends(current_admin),
                   db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(LoginAttempt).order_by(LoginAttempt.ts.desc()).limit(min(limit, 500)))).scalars()
    return [{"id": a.id, "ts": a.ts, "ip": a.ip, "login": a.login, "ok": a.ok, "reason": a.reason,
             "user_agent": a.user_agent} for a in rows]


@router.get("/audit")
async def audit_log(limit: int = 200, _: Current = Depends(current_admin),
                    db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(min(limit, 1000)))).scalars()
    return [{"id": a.id, "ts": a.ts, "ip": a.ip, "action": a.action, "target": a.target, "details": a.details}
            for a in rows]
