"""Сборка итоговой подписки."""

from __future__ import annotations

import base64
import re

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Node, Token
from .uri import node_to_uri


async def select_nodes(session: AsyncSession, token: Token) -> list[Node]:
    q = select(Node).where(Node.enabled.is_(True))
    nodes = (await session.execute(q)).scalars().all()

    if token.only_alive:
        nodes = [n for n in nodes if n.alive or n.pinned_alive]

    if token.include_regex:
        rx = re.compile(token.include_regex, re.IGNORECASE)
        nodes = [n for n in nodes if rx.search(n.display_name)]
    if token.exclude_regex:
        rx = re.compile(token.exclude_regex, re.IGNORECASE)
        nodes = [n for n in nodes if not rx.search(n.display_name)]

    # Сначала свои, потом по стране и задержке - стабильный порядок в выдаче
    def key(n: Node):
        return (
            0 if n.source == "own" else 1,
            n.country or "ZZ",
            n.last_latency_ms if n.last_latency_ms is not None else 99999,
            n.display_name,
        )

    return sorted(nodes, key=key)


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
