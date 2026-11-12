"""Модели БД. Всё состояние панели живёт здесь — никаких .env/json со списками.

Поля с суффиксом _enc зашифрованы (app.core.crypto) и наружу не отдаются.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TZDateTime(TypeDecorator):
    """SQLite теряет таймзону — пишем в UTC, читаем всегда aware UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


# ── Вход и безопасность ──────────────────────────────────────────────────────

class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Резервные коды 2FA: список argon2-хешей, использованный код удаляется.
    backup_codes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"))
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class ExtensionToken(Base):
    """Bearer-токен для браузерного расширения. Не cookie: расширение ходит из
    background-воркера с host_permissions, минуя CORS и CSRF-модель панели."""

    __tablename__ = "extension_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Account(Base):
    """Учётка Selectel для входа: единый источник вместо TXT в расширении.

    password_enc отдаётся только расширению под его bearer-токеном — для
    автоподстановки в форму входа Selectel; в панель и в статусы не попадает.
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), default="")
    password_enc: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str] = mapped_column(String(200), default="")
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)  # есть ли уже ключ в Roller
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    # Живой прогресс входа через расширение — панель показывает его построчно.
    login_stage: Mapped[str] = mapped_column(String(24), default="")
    login_message: Mapped[str] = mapped_column(Text, default="")
    login_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_ip_ts", "ip", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    ip: Mapped[str] = mapped_column(String(64))
    login: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    ip: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(200), default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ── Охота ────────────────────────────────────────────────────────────────────

class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(primary_key=True)
    url_enc: Mapped[str] = mapped_column(Text)
    # Отпечаток URL (sha256) — чтобы отсекать дубликаты, не расшифровывая всё подряд.
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    display: Mapped[str] = mapped_column(String(200))  # scheme://host:port без кредов
    label: Mapped[str] = mapped_column(String(100), default="")
    kind: Mapped[str] = mapped_column(String(16), default="http")
    # none — как есть; password_suffix — липкая сессия «_session-<id>» в пароле (proxxxymiron).
    session_mode: Mapped[str] = mapped_column(String(32), default="none")
    status: Mapped[str] = mapped_column(String(16), default="unchecked")  # ok / partial / dead / unchecked
    exit_ip: Mapped[str] = mapped_column(String(64), default="")
    exit_country: Mapped[str] = mapped_column(String(8), default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    regions_ok: Mapped[list[Any]] = mapped_column(JSON, default=list)
    regions_failed: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_error: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class SelectelKey(Base):
    __tablename__ = "selectel_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    auth_type: Mapped[str] = mapped_column(String(16), default="service_user")
    username: Mapped[str] = mapped_column(String(100), default="")
    password_enc: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[str] = mapped_column(String(64), default="")
    project_name: Mapped[str] = mapped_column(String(100), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # starting / hunting / sleeping / paused / error / banned / auth_failed / done
    status: Mapped[str] = mapped_column(String(16), default="paused")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    proxy_mode: Mapped[str] = mapped_column(String(8), default="auto")  # auto / fixed / none
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))
    proxy_session_gen: Mapped[int] = mapped_column(Integer, default=0)
    ip_goal: Mapped[int | None] = mapped_column(Integer)  # None = из общих настроек
    attempts_total: Mapped[int] = mapped_column(Integer, default=0)
    caught_total: Mapped[int] = mapped_column(Integer, default=0)
    last_ok_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    last_caught_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Subnet(Base):
    __tablename__ = "subnets"

    id: Mapped[int] = mapped_column(primary_key=True)
    cidr: Mapped[str] = mapped_column(String(32), unique=True)
    region: Mapped[str] = mapped_column(String(16), default="")  # заполняется по пулам Selectel
    white_count: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # import / manual
    note: Mapped[str] = mapped_column(String(200), default="")
    caught_total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class RegionPool(Base):
    """Кэш пулов floating IP по регионам: подсети внешних сетей Selectel.

    Пулы одинаковы для всех аккаунтов региона; кэш выручает, когда API региона
    недоступен, и даёт региону подсети без отдельного запроса.
    """

    __tablename__ = "region_pools"
    __table_args__ = (UniqueConstraint("region", "os_subnet_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    region: Mapped[str] = mapped_column(String(16))
    os_subnet_id: Mapped[str] = mapped_column(String(64))
    network_id: Mapped[str] = mapped_column(String(64))
    cidr: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class CaughtIP(Base):
    __tablename__ = "caught_ips"

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    region: Mapped[str] = mapped_column(String(16))
    key_id: Mapped[int | None] = mapped_column(ForeignKey("selectel_keys.id", ondelete="SET NULL"))
    account_id: Mapped[str] = mapped_column(String(32), default="")  # переживает удаление ключа
    subnet_id: Mapped[int | None] = mapped_column(ForeignKey("subnets.id", ondelete="SET NULL"))
    pool_cidr: Mapped[str] = mapped_column(String(32), default="")
    floating_ip_id: Mapped[str] = mapped_column(String(64), default="")
    caught_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    attempts_spent: Mapped[int | None] = mapped_column(Integer)
    protected: Mapped[bool] = mapped_column(Boolean, default=True)
    on_account: Mapped[bool] = mapped_column(Boolean, default=True)
    released_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    check_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / ok / bad / error / skipped
    check_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    note: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16), default="hunt")  # hunt / import / adopted


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(8), default="info")  # debug / info / success / warn / error
    kind: Mapped[str] = mapped_column(String(32), default="log")
    key_id: Mapped[int | None] = mapped_column(ForeignKey("selectel_keys.id", ondelete="SET NULL"), index=True)
    region: Mapped[str] = mapped_column(String(16), default="")
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MetricPoint(Base):
    """Счётчики охоты за минуту по ключу и региону."""

    __tablename__ = "metric_points"
    __table_args__ = (UniqueConstraint("bucket", "key_id", "region"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket: Mapped[datetime] = mapped_column(TZDateTime(), index=True)
    key_id: Mapped[int] = mapped_column(Integer, index=True)
    region: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    busy: Mapped[int] = mapped_column(Integer, default=0)
    misses: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    timeouts: Mapped[int] = mapped_column(Integer, default=0)
    proxy_errors: Mapped[int] = mapped_column(Integer, default=0)
    caught: Mapped[int] = mapped_column(Integer, default=0)
    latency_sum_ms: Mapped[float] = mapped_column(Float, default=0.0)
    latency_count: Mapped[int] = mapped_column(Integer, default=0)


class AppSettings(Base):
    """Одна строка (id=1): все настройки панели в JSON, см. app.settings_store.DEFAULTS."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)
