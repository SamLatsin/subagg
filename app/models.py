from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Subscription(Base):
    """Внешняя подписка: URL плюс заголовки, которыми к ней ходим."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    user_agent: Mapped[str] = mapped_column(String(128), default="clash.meta")
    # Произвольные заголовки, в том числе HWID: {"x-hwid": "...", "x-device-os": "OpenWrt"}
    headers: Mapped[dict] = mapped_column(JSON, default=dict)

    # Принудительный тег для всех нод подписки, например WL
    force_tags: Mapped[list] = mapped_column(JSON, default=list)

    last_fetch_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_node_count: Mapped[int] = mapped_column(Integer, default=0)

    # Данные из заголовка subscription-userinfo, если панель его отдает
    info_upload: Mapped[int | None] = mapped_column(Integer)
    info_download: Mapped[int | None] = mapped_column(Integer)
    info_total: Mapped[int | None] = mapped_column(Integer)
    info_expire: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    nodes: Mapped[list[Node]] = relationship(back_populates="subscription", cascade="all, delete-orphan")


class Node(Base):
    """Одна нода. Приходит из подписки либо заведена руками (source=own)."""

    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_nodes_fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(16), default="sub")  # sub | own

    # server:port:type:uuid|password - для дедупликации между подписками
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)

    original_name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(8))
    tags: Mapped[list] = mapped_column(JSON, default=list)

    proto: Mapped[str] = mapped_column(String(24))
    server: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    # Полный proxy-объект в формате clash/mihomo
    config: Mapped[dict] = mapped_column(JSON)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # ручное выключение
    pinned_alive: Mapped[bool] = mapped_column(Boolean, default=False)  # отдавать всегда, минуя проверку

    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    fail_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Куда нода выводит на самом деле: IP и страна по геобазе, как их видят сайты.
    # country выше - то, что заявлено в названии
    exit_ip: Mapped[str | None] = mapped_column(String(64))
    exit_country: Mapped[str | None] = mapped_column(String(8))
    exit_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Открыт ли порт сервера напрямую, без прокси. Отличает «сервер лежит или
    # заблокирован» от «порт открыт, но прокси не пускает». None - не проверяли
    # (UDP-протоколы) или проверки еще не было
    tcp_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subscription: Mapped[Subscription | None] = relationship(back_populates="nodes")
    results: Mapped[list[CheckResult]] = relationship(back_populates="node", cascade="all, delete-orphan")


class CheckTarget(Base):
    """Куда ходим через ноду, чтобы решить, живая она или нет."""

    __tablename__ = "check_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(Text)
    expected_status: Mapped[int] = mapped_column(Integer, default=204)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=6000)
    # required: провал делает ноду мертвой независимо от остальных целей
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("check_targets.id", ondelete="CASCADE"), index=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    node: Mapped[Node] = relationship(back_populates="results")


class Token(Base):
    """Токен выдачи. Каждому устройству свой, с собственным фильтром."""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(24))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    fmt: Mapped[str] = mapped_column(String(16), default="clash")  # clash | base64 | uri
    include_regex: Mapped[str | None] = mapped_column(Text)
    exclude_regex: Mapped[str | None] = mapped_column(Text)
    only_alive: Mapped[bool] = mapped_column(Boolean, default=True)
    # id нод, вручную исключенных из выдачи этого токена. Черный список,
    # чтобы новые ноды из подписок попадали в выдачу без ручной правки
    excluded_nodes: Mapped[list] = mapped_column(JSON, default=list)

    # Публикация статикой на внешний сервер по SSH: user@host[:port]:/path/file
    push_target: Mapped[str | None] = mapped_column(Text)
    push_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    push_error: Mapped[str | None] = mapped_column(Text)
    push_nodes: Mapped[int | None] = mapped_column(Integer)

    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
