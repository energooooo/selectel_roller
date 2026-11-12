"""Пароли, токены сессий, 2FA, адрес клиента."""
from __future__ import annotations

import hashlib
import ipaddress
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request

_hasher = PasswordHasher()  # argon2id с параметрами по умолчанию (OWASP-совместимые)

# Пароли, которые не принимаем даже длинными.
_WEAK = {
    "password1234", "qwertyuiop12", "123456789012", "adminadmin12", "toffeeroller",
    "qwerty123456", "passwordpassword", "111111111111", "selectel1234",
}
MIN_PASSWORD_LEN = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Фиктивный хеш: проверка «нет такого логина» тратит столько же времени, сколько настоящая.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(password: str, login: str = "") -> str:
    if len(password) < MIN_PASSWORD_LEN:
        return f"Пароль короче {MIN_PASSWORD_LEN} символов"
    lowered = password.lower()
    if lowered in _WEAK or (login and login.lower() in lowered) or len(set(password)) < 5:
        return "Слишком простой пароль"
    return ""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, login: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=login, issuer_name="Toffee Roller")


def verify_totp(secret: str, code: str) -> bool:
    code = "".join(ch for ch in code if ch.isdigit())
    return len(code) == 6 and pyotp.TOTP(secret).verify(code, valid_window=1)


def new_backup_codes(count: int = 8) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)) for _ in range(count)]


def _is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def client_ip(request: Request) -> str:
    """Адрес клиента. X-Forwarded-For доверяем только от своего прокси (web/Caddy в локальной сети)."""
    peer = request.client.host if request.client else ""
    if _is_private(peer):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # Берём ближайший к нам публичный адрес справа: левые части клиент мог подделать.
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            for hop in reversed(hops):
                if not _is_private(hop):
                    return hop
            return hops[0] if hops else peer
    return peer or "unknown"
