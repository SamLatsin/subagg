"""Скачивание подписок и обновление таблицы нод."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Node, Subscription, utcnow
from .normalize import normalize_node
from .parsers import fingerprint, is_junk, parse_subscription_body

log = logging.getLogger(__name__)

USERINFO_RE = re.compile(r"(\w+)=(-?\d+)")


def _parse_userinfo(header: str | None) -> dict[str, int]:
    if not header:
        return {}
    return {k.lower(): int(v) for k, v in USERINFO_RE.findall(header)}


def _slot(sub_id: int | None, fp: str) -> str:
    prefix = f"s{sub_id}" if sub_id else "my"
    return f"{prefix}-{fp[:4]}"


def _name_hash(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()


async def fetch_subscription(sub: Subscription, timeout: float = 30.0) -> tuple[list[dict], dict]:
    """Скачать подписку, вернуть (ноды, заголовки-инфо)."""
    headers = {"User-Agent": sub.user_agent or "clash.meta", "Accept-Encoding": "identity"}
    for k, v in (sub.headers or {}).items():
        if v:
            headers[str(k)] = str(v)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(sub.url, headers=headers)
        resp.raise_for_status()
        body = resp.text
        info = _parse_userinfo(resp.headers.get("subscription-userinfo"))

    nodes = parse_subscription_body(body)
    return nodes, info


async def sync_subscription(session: AsyncSession, sub: Subscription) -> dict:
    """Обновить ноды одной подписки. Вернуть сводку."""
    summary = {"name": sub.name, "fetched": 0, "kept": 0, "junk": 0, "new": 0, "error": None}
    sub.last_fetch_at = utcnow()

    try:
        raw_nodes, info = await fetch_subscription(sub)
    except Exception as exc:  # noqa: BLE001 - любая сетевая беда не должна ронять джоб
        sub.last_error = f"{type(exc).__name__}: {exc}"
        summary["error"] = sub.last_error
        log.warning("подписка %s не обновилась: %s", sub.name, sub.last_error)
        await session.commit()
        return summary

    summary["fetched"] = len(raw_nodes)
    if info:
        sub.info_upload = info.get("upload")
        sub.info_download = info.get("download")
        sub.info_total = info.get("total")
        sub.info_expire = info.get("expire")

    clean = [n for n in raw_nodes if not is_junk(n)]
    summary["junk"] = len(raw_nodes) - len(clean)

    if not clean:
        sub.last_error = "подписка отдала 0 пригодных нод, старый список сохранен"
        summary["error"] = sub.last_error
        await session.commit()
        return summary

    existing = (await session.execute(select(Node).where(Node.subscription_id == sub.id))).scalars().all()
    by_fp = {n.fingerprint: n for n in existing}
    by_name: dict[str, list[Node]] = {}
    for n in sorted(existing, key=lambda n: n.id):
        by_name.setdefault(n.original_name, []).append(n)
    # Отпечатки старых нод сбрасываем: провайдер мог раздать их параметры
    # другим нодам, и уникальный индекс не даст переложить отпечаток
    for n in existing:
        n.fingerprint = f"tmp-{n.id}"
    await session.flush()

    seen_fp: set[str] = set()
    keep: set[int] = set()
    name_count: dict[str, int] = {}

    for raw in clean:
        fp = fingerprint(raw)
        if fp in seen_fp:
            continue
        seen_fp.add(fp)

        # Та же нода могла прийти из другой подписки или быть своей - не дублируем
        other = (
            await session.execute(select(Node).where(Node.fingerprint == fp))
        ).scalar_one_or_none()
        if other is not None:
            continue

        # Провайдеры меняют SNI/short-id на каждый запрос, поэтому старую
        # ноду ищем по исходному имени: так не теряется история проверок
        # и не меняется имя в выдаче
        name = str(raw.get("name", ""))
        name_count[name] = name_count.get(name, 0) + 1
        key = name if name_count[name] == 1 else f"{name}#{name_count[name]}"

        candidates = by_name.get(name) or []
        node = candidates.pop(0) if candidates else by_fp.get(fp)
        if node is not None and node.id in keep:
            node = None

        meta = normalize_node(raw, "sub", _slot(sub.id, _name_hash(key)), sub.force_tags or [])
        cfg = dict(raw)
        cfg["name"] = meta["display_name"]

        if node is None:
            node = Node(subscription_id=sub.id, source="sub")
            session.add(node)
            summary["new"] += 1
        node.fingerprint = fp
        node.original_name = name
        node.display_name = meta["display_name"]
        node.country = meta["country"]
        node.tags = meta["tags"]
        node.proto = str(raw.get("type", ""))
        node.server = str(raw.get("server", ""))
        node.port = int(raw.get("port", 0) or 0)
        node.config = cfg
        node.last_seen_at = utcnow()
        await session.flush()
        keep.add(node.id)

    # Ноды, пропавшие из подписки, удаляем
    gone = [n for n in existing if n.id not in keep]
    for n in gone:
        await session.delete(n)

    summary["kept"] = len(keep)
    sub.last_node_count = len(keep)
    sub.last_ok_at = utcnow()
    sub.last_error = None
    await session.commit()
    log.info("подписка %s: %d нод, +%d новых, -%d ушло", sub.name, len(keep), summary["new"], len(gone))
    return summary


async def sync_all(session: AsyncSession) -> list[dict]:
    subs = (
        await session.execute(select(Subscription).where(Subscription.enabled.is_(True)))
    ).scalars().all()
    out = []
    for sub in subs:
        out.append(await sync_subscription(session, sub))
    return out


async def add_own_nodes(session: AsyncSession, proxies: list[dict], force_tags: list[str] | None = None) -> int:
    """Добавить свои ноды (уже разобранные в формат mihomo)."""
    added = 0
    for raw in proxies:
        if is_junk(raw):
            continue
        fp = fingerprint(raw)
        node = (await session.execute(select(Node).where(Node.fingerprint == fp))).scalar_one_or_none()
        meta = normalize_node(raw, "own", _slot(None, fp), force_tags or [])
        cfg = dict(raw)
        cfg["name"] = meta["display_name"]
        if node is None:
            node = Node(subscription_id=None, source="own", fingerprint=fp)
            session.add(node)
            added += 1
        node.source = "own"
        node.subscription_id = None
        node.original_name = str(raw.get("name", ""))
        node.display_name = meta["display_name"]
        node.country = meta["country"]
        node.tags = meta["tags"]
        node.proto = str(raw.get("type", ""))
        node.server = str(raw.get("server", ""))
        node.port = int(raw.get("port", 0) or 0)
        node.config = cfg
        node.last_seen_at = utcnow()
    await session.commit()
    return added


def expire_date(ts: int | None) -> dt.datetime | None:
    if not ts:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)
