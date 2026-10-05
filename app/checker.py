"""Проверка нод настоящим ядром mihomo.

Поднимаем mihomo подпроцессом со всеми нодами, дергаем его delay API
по каждой цели проверки и по ответам решаем, живая нода или нет.
Проверка идет из того же места, где крутится сервис, поэтому вердикт
отражает доступность ноды именно отсюда.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import shutil
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import CheckResult, CheckTarget, Node, utcnow

log = logging.getLogger(__name__)

# Ядро ругается на проблемную ноду либо по её порядковому номеру
# ("proxy 3: invalid REALITY public key"), либо по имени ("n42").
BAD_INDEX_RE = re.compile(r"proxy\s+(\d+)\s*:", re.IGNORECASE)
BAD_NAME_RE = re.compile(r"\b(n\d+)\b")


class MihomoRunner:
    """Временный экземпляр ядра только для замеров задержки."""

    def __init__(self, proxies: list[dict], work_dir: Path, port: int):
        self.proxies = proxies
        self.work_dir = work_dir
        self.port = port
        self.secret = secrets.token_urlsafe(16)
        self.proc: asyncio.subprocess.Process | None = None
        self.dropped: list[str] = []

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"}

    def _config(self, proxies: list[dict]) -> dict:
        dns_servers = [s.strip() for s in settings.checker_dns.split(",") if s.strip()]
        return {
            "log-level": "warning",
            "mode": "rule",
            "ipv6": False,
            "mixed-port": 0,
            "external-controller": f"127.0.0.1:{self.port}",
            "secret": self.secret,
            "dns": {
                "enable": True,
                "ipv6": False,
                "listen": "",
                "enhanced-mode": "redir-host",
                "default-nameserver": dns_servers,
                "nameserver": dns_servers,
            },
            "proxies": proxies,
            "proxy-groups": [],
            "rules": ["MATCH,DIRECT"],
        }

    def _write(self, proxies: list[dict]) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        path = self.work_dir / "config.yaml"
        path.write_text(
            yaml.safe_dump(self._config(proxies), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path

    async def _test_config(self, proxies: list[dict]) -> tuple[bool, str]:
        path = self._write(proxies)
        proc = await asyncio.create_subprocess_exec(
            settings.mihomo_bin,
            "-t",
            "-d",
            str(self.work_dir),
            "-f",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return proc.returncode == 0, out.decode("utf-8", "ignore")

    async def _prune_broken(self) -> list[dict]:
        """Ядро отказывается стартовать целиком из-за одной кривой ноды,
        поэтому выбрасываем такие по одной, пока конфиг не станет валидным."""
        proxies = list(self.proxies)
        limit = len(proxies) + 5
        for _ in range(limit):
            if not proxies:
                return []
            ok, output = await self._test_config(proxies)
            if ok:
                return proxies

            victims: set[str] = set()
            for idx in BAD_INDEX_RE.findall(output):
                i = int(idx)
                if 0 <= i < len(proxies):
                    victims.add(proxies[i]["name"])
            if not victims:
                victims = {m for m in BAD_NAME_RE.findall(output) if m in {p["name"] for p in proxies}}
            if not victims:
                log.error("конфиг не валиден, виновник не опознан: %s", output.strip()[-500:])
                return []

            reason = output.strip().splitlines()[-2:] if output.strip() else []
            for v in victims:
                self.dropped.append(v)
            log.warning("ядро не приняло ноды %s: %s", ", ".join(sorted(victims)), " | ".join(reason)[:200])
            proxies = [p for p in proxies if p["name"] not in victims]
        return proxies

    async def __aenter__(self) -> "MihomoRunner":
        if not shutil.which(settings.mihomo_bin) and not Path(settings.mihomo_bin).exists():
            raise RuntimeError(f"не найден бинарник mihomo: {settings.mihomo_bin}")

        proxies = await self._prune_broken()
        self.proxies = proxies
        if not proxies:
            raise RuntimeError("не осталось ни одной пригодной ноды для проверки")

        path = self._write(proxies)
        self.proc = await asyncio.create_subprocess_exec(
            settings.mihomo_bin,
            "-d",
            str(self.work_dir),
            "-f",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async with httpx.AsyncClient(timeout=3.0) as client:
            for _ in range(60):
                if self.proc.returncode is not None:
                    out = await self.proc.stdout.read() if self.proc.stdout else b""
                    raise RuntimeError(f"mihomo упал при старте: {out.decode('utf-8','ignore')[:500]}")
                try:
                    r = await client.get(f"{self.api}/version", headers=self.headers)
                    if r.status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise RuntimeError("mihomo не поднял API за отведенное время")

    async def __aexit__(self, *exc) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()

    async def delay(self, client: httpx.AsyncClient, name: str, url: str, timeout_ms: int,
                    expected: int) -> tuple[int | None, str | None]:
        params = {"url": url, "timeout": str(timeout_ms), "expected": str(expected)}
        try:
            r = await client.get(
                f"{self.api}/proxies/{quote(name, safe='')}/delay",
                params=params,
                headers=self.headers,
                timeout=timeout_ms / 1000 + 5,
            )
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}"
        if r.status_code == 200:
            try:
                return int(r.json().get("delay")), None
            except (ValueError, TypeError):
                return None, "некорректный ответ ядра"
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        return None, str(msg)[:200]


async def run_check(session: AsyncSession, node_ids: list[int] | None = None) -> dict:
    """Прогнать проверку и записать вердикты. Вернуть сводку."""
    q = select(Node).where(Node.enabled.is_(True))
    if node_ids:
        q = q.where(Node.id.in_(node_ids))
    nodes = (await session.execute(q)).scalars().all()

    targets = (
        await session.execute(select(CheckTarget).where(CheckTarget.enabled.is_(True)))
    ).scalars().all()

    summary = {"nodes": len(nodes), "targets": len(targets), "alive": 0, "dead": 0,
               "dropped": 0, "error": None}
    if not nodes or not targets:
        summary["error"] = "нет нод или нет включенных целей проверки"
        return summary

    by_key = {f"n{n.id}": n for n in nodes}
    proxies = []
    for key, n in by_key.items():
        cfg = dict(n.config or {})
        cfg.pop("_warn", None)
        cfg["name"] = key
        proxies.append(cfg)

    sem = asyncio.Semaphore(settings.check_concurrency)
    results: dict[str, list[tuple[CheckTarget, int | None, str | None]]] = {k: [] for k in by_key}

    try:
        async with MihomoRunner(proxies, settings.work_dir, settings.checker_api_port) as runner:
            alive_keys = {p["name"] for p in runner.proxies}
            summary["dropped"] = len(runner.dropped)

            async with httpx.AsyncClient() as client:

                async def probe(key: str, target: CheckTarget) -> None:
                    async with sem:
                        latency, err = await runner.delay(
                            client, key, target.url,
                            target.timeout_ms or settings.check_timeout_ms,
                            target.expected_status,
                        )
                        results[key].append((target, latency, err))

                await asyncio.gather(
                    *(probe(k, t) for k in alive_keys for t in targets),
                    return_exceptions=True,
                )

            # ноды, которые ядро не приняло, считаем мертвыми
            for key in by_key:
                if key not in alive_keys:
                    results[key] = [(t, None, "ядро не приняло конфиг ноды") for t in targets]
    except RuntimeError as exc:
        summary["error"] = str(exc)
        log.error("проверка не состоялась: %s", exc)
        return summary

    # Старые результаты этих нод убираем, храним только последний прогон
    await session.execute(delete(CheckResult).where(CheckResult.node_id.in_([n.id for n in nodes])))

    now = utcnow()
    for key, rows in results.items():
        node = by_key[key]
        ok_any = False
        required_ok = True
        best: int | None = None

        for target, latency, err in rows:
            ok = latency is not None
            session.add(
                CheckResult(
                    node_id=node.id, target_id=target.id, ok=ok,
                    latency_ms=latency, error=err, checked_at=now,
                )
            )
            if ok:
                ok_any = True
                best = latency if best is None else min(best, latency)
            elif target.required:
                required_ok = False

        verdict = ok_any and required_ok
        # Задержку сохраняем в любом случае: у мертвой ноды видно,
        # что она отвечала хотя бы куда-то
        node.last_latency_ms = best
        if verdict:
            node.fail_streak = 0
            node.alive = True
        else:
            node.fail_streak += 1
            # Отсрочка в dead_after_fails прогонов нужна, чтобы разовый сбой
            # не выкинул рабочую ноду. Новой ноде, которая еще ни разу не
            # проверялась, верить не в чем: мертва с первого провала
            if node.last_check_at is None or node.fail_streak >= settings.dead_after_fails:
                node.alive = False
        node.last_check_at = now

        if node.alive:
            summary["alive"] += 1
        else:
            summary["dead"] += 1

    await session.commit()
    log.info("проверка: %(alive)d живых, %(dead)d мертвых, %(dropped)d отброшено ядром", summary)
    return summary
