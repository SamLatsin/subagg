from __future__ import annotations

import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .checker import run_check
from .config import settings
from .db import SessionLocal, get_session, init_db
from .fetcher import add_own_nodes, expire_date, sync_all, sync_subscription
from .models import CheckResult, CheckTarget, Node, Subscription, Token, utcnow
from .parsers import parse_subscription_body, parse_uri
from .render import render_base64, render_clash, render_uri_list, select_nodes
from .xray import parse_xray_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("subagg")

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

DEFAULT_TARGETS = [
    # required: если эта цель не прошла, нода считается мертвой
    {"name": "gstatic-204", "url": "https://www.gstatic.com/generate_204",
     "expected_status": 204, "required": True},
    {"name": "cloudflare-204", "url": "https://cp.cloudflare.com/generate_204",
     "expected_status": 204, "required": False},
    {"name": "yandex", "url": "https://ya.ru", "expected_status": 200, "required": False},
    {"name": "github", "url": "https://github.com", "expected_status": 200, "required": False},
]

scheduler = AsyncIOScheduler(timezone="UTC")


async def seed_targets() -> None:
    async with SessionLocal() as session:
        count = (await session.execute(select(func.count(CheckTarget.id)))).scalar_one()
        if count:
            return
        for t in DEFAULT_TARGETS:
            session.add(CheckTarget(timeout_ms=settings.check_timeout_ms, **t))
        await session.commit()
        log.info("созданы цели проверки по умолчанию")


async def job_fetch() -> None:
    async with SessionLocal() as session:
        await sync_all(session)


async def job_check() -> None:
    async with SessionLocal() as session:
        await run_check(session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_targets()
    scheduler.add_job(job_fetch, IntervalTrigger(minutes=settings.fetch_interval_min),
                      id="fetch", max_instances=1, coalesce=True)
    scheduler.add_job(job_check, IntervalTrigger(minutes=settings.check_interval_min),
                      id="check", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("сервис запущен, обновление подписок каждые %d мин, проверка каждые %d мин",
             settings.fetch_interval_min, settings.check_interval_min)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="subagg", lifespan=lifespan)
security = HTTPBasic(auto_error=True)


def require_admin(creds: HTTPBasicCredentials = Depends(security)) -> str:
    ok_user = secrets.compare_digest(creds.username, settings.admin_user)
    ok_pass = secrets.compare_digest(creds.password, settings.admin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username


# --------------------------------------------------------------------------- #
# публичная выдача
# --------------------------------------------------------------------------- #


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


@app.get("/sub/{token_value}")
async def serve_subscription(
    token_value: str,
    request: Request,
    format: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    token = (
        await session.execute(select(Token).where(Token.token == token_value))
    ).scalar_one_or_none()
    if token is None or not token.enabled:
        raise HTTPException(status_code=404, detail="not found")

    nodes = await select_nodes(session, token)

    token.last_used_at = utcnow()
    token.last_used_ip = request.client.host if request.client else None
    token.use_count += 1
    await session.commit()

    fmt = (format or token.fmt or "clash").lower()
    # Клиенты на ядре clash просят YAML, остальные обычно ждут base64
    ua = request.headers.get("user-agent", "").lower()
    if format is None and any(k in ua for k in ("clash", "mihomo", "nikki", "flclash", "stash")):
        fmt = "clash"

    headers = {
        "Profile-Update-Interval": str(max(1, settings.fetch_interval_min // 60)),
        "Subscription-Userinfo": f"upload=0; download=0; total=0; expire=0",
        "Content-Disposition": 'inline; filename="subagg"',
    }

    if fmt == "base64":
        return PlainTextResponse(render_base64(nodes), headers=headers)
    if fmt == "uri":
        return PlainTextResponse(render_uri_list(nodes), headers=headers)
    return PlainTextResponse(
        render_clash(nodes), media_type="text/yaml; charset=utf-8", headers=headers
    )


# --------------------------------------------------------------------------- #
# админка
# --------------------------------------------------------------------------- #


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    subs = (await session.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    nodes = (await session.execute(select(Node).order_by(Node.display_name))).scalars().all()
    targets = (await session.execute(select(CheckTarget).order_by(CheckTarget.id))).scalars().all()
    tokens = (await session.execute(select(Token).order_by(Token.id))).scalars().all()

    fails: dict[int, list[str]] = {}
    rows = (await session.execute(select(CheckResult).where(CheckResult.ok.is_(False)))).scalars().all()
    tnames = {t.id: t.name for t in targets}
    for r in rows:
        fails.setdefault(r.node_id, []).append(tnames.get(r.target_id, "?"))

    stats = {
        "nodes": len(nodes),
        "alive": sum(1 for n in nodes if n.alive),
        "own": sum(1 for n in nodes if n.source == "own"),
        "ru": sum(1 for n in nodes if "RU" in (n.tags or [])),
        "tor": sum(1 for n in nodes if "TOR" in (n.tags or [])),
    }

    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "subs": subs,
            "nodes": nodes,
            "targets": targets,
            "tokens": tokens,
            "stats": stats,
            "fails": fails,
            "settings": settings,
            "expire_date": expire_date,
            "base_url": str(request.base_url).rstrip("/"),
        },
    )


def _back() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


@app.post("/subs")
async def add_sub(
    name: str = Form(...),
    url: str = Form(...),
    user_agent: str = Form("clash.meta"),
    headers_json: str = Form(""),
    force_tags: str = Form(""),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        headers = json.loads(headers_json) if headers_json.strip() else {}
        if not isinstance(headers, dict):
            raise ValueError("ожидается JSON-объект")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"заголовки: {exc}") from exc

    sub = Subscription(
        name=name.strip(),
        url=url.strip(),
        user_agent=user_agent.strip() or "clash.meta",
        headers=headers,
        force_tags=[t.strip().upper() for t in force_tags.split(",") if t.strip()],
    )
    session.add(sub)
    await session.commit()
    await sync_subscription(session, sub)
    return _back()


@app.post("/subs/{sub_id}/refresh")
async def refresh_sub(
    sub_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="нет такой подписки")
    await sync_subscription(session, sub)
    return _back()


@app.post("/subs/{sub_id}/toggle")
async def toggle_sub(
    sub_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="нет такой подписки")
    sub.enabled = not sub.enabled
    await session.commit()
    return _back()


@app.post("/subs/{sub_id}/delete")
async def delete_sub(
    sub_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    sub = await session.get(Subscription, sub_id)
    if sub:
        await session.delete(sub)
        await session.commit()
    return _back()


@app.post("/own")
async def add_own(
    payload: str = Form(...),
    force_tags: str = Form(""),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Свои ноды: ссылки, clash YAML или xray outbound JSON."""
    text = payload.strip()
    tags = [t.strip().upper() for t in force_tags.split(",") if t.strip()]
    proxies: list[dict] = []

    if text.startswith("{") or text.startswith("["):
        try:
            proxies = parse_xray_json(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"не разобран JSON: {exc}") from exc
    elif "proxies:" in text[:2048]:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            proxies = [p for p in data.get("proxies", []) if isinstance(p, dict)]
    else:
        for line in text.splitlines():
            node = parse_uri(line)
            if node:
                proxies.append(node)
        if not proxies:
            proxies = parse_subscription_body(text)

    if not proxies:
        raise HTTPException(status_code=400, detail="ничего не разобрано из вставленного текста")

    added = await add_own_nodes(session, proxies, tags)
    log.info("добавлено своих нод: %d (всего разобрано %d)", added, len(proxies))
    return _back()


@app.post("/nodes/{node_id}/toggle")
async def toggle_node(
    node_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="нет такой ноды")
    node.enabled = not node.enabled
    await session.commit()
    return _back()


@app.post("/nodes/{node_id}/pin")
async def pin_node(
    node_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="нет такой ноды")
    node.pinned_alive = not node.pinned_alive
    await session.commit()
    return _back()


@app.post("/nodes/{node_id}/delete")
async def delete_node(
    node_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    node = await session.get(Node, node_id)
    if node:
        await session.delete(node)
        await session.commit()
    return _back()


@app.post("/targets")
async def add_target(
    name: str = Form(...),
    url: str = Form(...),
    expected_status: int = Form(204),
    required: bool = Form(False),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    session.add(
        CheckTarget(
            name=name.strip(),
            url=url.strip(),
            expected_status=expected_status,
            required=required,
            timeout_ms=settings.check_timeout_ms,
        )
    )
    await session.commit()
    return _back()


@app.post("/targets/{target_id}/toggle")
async def toggle_target(
    target_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    t = await session.get(CheckTarget, target_id)
    if not t:
        raise HTTPException(status_code=404, detail="нет такой цели")
    t.enabled = not t.enabled
    await session.commit()
    return _back()


@app.post("/targets/{target_id}/delete")
async def delete_target(
    target_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    t = await session.get(CheckTarget, target_id)
    if t:
        await session.delete(t)
        await session.commit()
    return _back()


@app.post("/tokens")
async def add_token(
    name: str = Form(...),
    fmt: str = Form("clash"),
    include_regex: str = Form(""),
    exclude_regex: str = Form(""),
    only_alive: bool = Form(False),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    session.add(
        Token(
            name=name.strip(),
            fmt=fmt,
            include_regex=include_regex.strip() or None,
            exclude_regex=exclude_regex.strip() or None,
            only_alive=only_alive,
        )
    )
    await session.commit()
    return _back()


@app.post("/tokens/{token_id}/delete")
async def delete_token(
    token_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    t = await session.get(Token, token_id)
    if t:
        await session.delete(t)
        await session.commit()
    return _back()


@app.post("/actions/refresh")
async def action_refresh(
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await sync_all(session)
    return _back()


@app.post("/actions/check")
async def action_check(
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await run_check(session)
    return _back()


@app.get("/api/stats")
async def api_stats(
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    nodes = (await session.execute(select(Node))).scalars().all()
    return {
        "total": len(nodes),
        "alive": sum(1 for n in nodes if n.alive),
        "by_tag": {
            tag: sum(1 for n in nodes if tag in (n.tags or []))
            for tag in ("RU", "XX", "MY", "WL", "TOR", "GAME")
        },
    }
