"""Сборка итоговой подписки."""

from __future__ import annotations

import base64
import re

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Node, Token
from .uri import node_to_uri


def _order(n: Node):
    # Сначала свои, потом по стране и задержке - стабильный порядок в выдаче
    return (
        0 if n.source == "own" else 1,
        n.country or "ZZ",
        n.last_latency_ms if n.last_latency_ms is not None else 99999,
        n.display_name,
    )


async def explain_nodes(session: AsyncSession, token: Token) -> list[tuple[Node, str | None]]:
    """Все ноды с причиной, по которой нода не попадает в выдачу токена.

    None вместо причины - нода уходит в выдачу.
    """
    nodes = (await session.execute(select(Node))).scalars().all()
    inc = re.compile(token.include_regex, re.IGNORECASE) if token.include_regex else None
    exc = re.compile(token.exclude_regex, re.IGNORECASE) if token.exclude_regex else None
    excluded = set(token.excluded_nodes or [])

    out = []
    for n in sorted(nodes, key=_order):
        if not n.enabled:
            reason = "выключена для всех"
        elif n.id in excluded:
            reason = "выключена для этого токена"
        elif token.only_alive and not (n.alive or n.pinned_alive):
            reason = "мертвая, а токен отдает только живые"
        elif inc and not inc.search(n.display_name):
            reason = "не подходит под include"
        elif exc and exc.search(n.display_name):
            reason = "попала под exclude"
        else:
            reason = None
        out.append((n, reason))
    return out


async def select_nodes(session: AsyncSession, token: Token) -> list[Node]:
    return [n for n, reason in await explain_nodes(session, token) if reason is None]


def _proxy_dicts(nodes: list[Node]) -> list[dict]:
    out = []
    used: set[str] = set()
    for n in nodes:
        cfg = dict(n.config or {})
        cfg.pop("_warn", None)
        name = n.display_name
        # На всякий случай гарантируем уникальность имен
        if name in used:
            name = f"{name}·{n.id}"
        used.add(name)
        cfg["name"] = name
        out.append(cfg)
    return out


def render_clash(nodes: list[Node]) -> str:
    """Формат proxy-provider: только список proxies."""
    data = {"proxies": _proxy_dicts(nodes)}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=4096)


def render_uri_list(nodes: list[Node]) -> str:
    lines = []
    for cfg in _proxy_dicts(nodes):
        uri = node_to_uri(cfg)
        if uri:
            lines.append(uri)
    return "\n".join(lines)


def render_base64(nodes: list[Node]) -> str:
    return base64.b64encode(render_uri_list(nodes).encode()).decode()
