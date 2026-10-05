"""Публикация подписки статикой на внешний сервер по SSH.

Нужна, когда клиенты не достают до дома: например, роутер на симке с
белым списком. Домашний сервис кладет готовый файл на сервер из белого
списка, а клиент тянет его оттуда обычной ссылкой.

Пишем во временный файл и переименовываем, так что клиент никогда не
получит недописанный файл. На той стороне нужен только sh.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import shutil

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import Token, utcnow
from .render import render_base64, render_clash, render_uri_list, select_nodes

log = logging.getLogger(__name__)

# user@host:/path, user@host:2222:/path
TARGET_RE = re.compile(r"^(?P<dest>[\w.-]+@[\w.-]+)(?::(?P<port>\d{1,5}))?:(?P<path>/\S+)$")


def ssh_dir():
    return settings.data_dir / "ssh"


def key_path():
    return ssh_dir() / "id_ed25519"


def parse_target(target: str) -> tuple[str, int, str] | None:
    m = TARGET_RE.match(target.strip())
    if not m:
        return None
    return m["dest"], int(m["port"] or 22), m["path"]


async def ensure_key() -> str | None:
    """Создать ключ при первом запуске. Вернуть публичную часть."""
    if not shutil.which("ssh-keygen"):
        return None
    key = key_path()
    if not key.exists():
        ssh_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        proc = await asyncio.create_subprocess_exec(
            "ssh-keygen", "-t", "ed25519", "-N", "", "-C", "subagg", "-f", str(key),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            log.error("не создался ssh-ключ: %s", err.decode("utf-8", "ignore"))
            return None
        log.info("создан ssh-ключ для публикации: %s", key)
    return public_key()


def public_key() -> str | None:
    pub = key_path().with_suffix(".pub")
    return pub.read_text().strip() if pub.exists() else None


def _render(token: Token, nodes) -> str:
    if token.fmt == "base64":
        return render_base64(nodes)
    if token.fmt == "uri":
        return render_uri_list(nodes)
    return render_clash(nodes)


async def _ssh_put(target: str, content: str) -> None:
    parsed = parse_target(target)
    if parsed is None:
        raise ValueError("адрес публикации: нужен вид user@host:/путь/файл или user@host:порт:/путь/файл")
    dest, port, path = parsed
    tmp = f"{path}.tmp"
    remote = (
        f"mkdir -p {shlex.quote(path.rsplit('/', 1)[0] or '/')}"
        f" && cat > {shlex.quote(tmp)} && mv -f {shlex.quote(tmp)} {shlex.quote(path)}"
    )
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-i", str(key_path()),
        "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        # Ключ сервера запоминаем при первом подключении, дальше сверяем
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={ssh_dir() / 'known_hosts'}",
        dest, remote,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(content.encode("utf-8")), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("ssh не ответил за 60 секунд") from None
    if proc.returncode != 0:
        msg = out.decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError(f"ssh код {proc.returncode}: {' | '.join(msg[-3:])[:300]}")


async def publish_token(session: AsyncSession, token: Token) -> bool:
    """Выложить выдачу токена по его push_target. Результат пишется в токен."""
    if not token.push_target:
        return False
    nodes = await select_nodes(session, token)
    try:
        if not token.enabled:
            raise RuntimeError("токен приостановлен, публикация пропущена")
        # Пустой файл на сервере оставил бы клиентов вообще без нод
        if not nodes:
            raise RuntimeError("0 нод в выдаче, старый файл на сервере не тронут")
        if not key_path().exists() and await ensure_key() is None:
            raise RuntimeError("нет ssh-ключа и не получилось его создать")
        await _ssh_put(token.push_target, _render(token, nodes))
    except Exception as exc:  # noqa: BLE001 - ошибка должна дожить до админки
        token.push_error = str(exc)
        await session.commit()
        log.warning("публикация %s не удалась: %s", token.name, exc)
        return False
    token.push_at = utcnow()
    token.push_error = None
    token.push_nodes = len(nodes)
    await session.commit()
    log.info("опубликован %s: %d нод -> %s", token.name, len(nodes), token.push_target)
    return True


async def publish_all(session: AsyncSession) -> None:
    tokens = (
        await session.execute(select(Token).where(Token.push_target.is_not(None)))
    ).scalars().all()
    for t in tokens:
        await publish_token(session, t)
