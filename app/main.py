from __future__ import annotations

import json
import logging
import re
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import SessionLocal, get_session, init_db
from .fetcher import add_own_nodes, expire_date, sync_subscription
from .i18n import DEFAULT_LANG, LANGS, current_lang
from .i18n import gettext as tr
from .jobs import CHECK, FETCH, JOBS
from .models import CheckResult, CheckTarget, Node, Subscription, Token, utcnow
from .parsers import parse_subscription_body, parse_uri
from .render import explain_nodes, render_base64, render_clash, render_uri_list, select_nodes
from .xray import parse_xray_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("subagg")

TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    context_processors=[lambda request: {"lang": current_lang.get()}],
)
TEMPLATES.env.globals["_"] = tr

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_targets()
    scheduler.add_job(FETCH.run, IntervalTrigger(minutes=settings.fetch_interval_min),
                      id="fetch", max_instances=1, coalesce=True)
    scheduler.add_job(CHECK.run, IntervalTrigger(minutes=settings.check_interval_min),
                      id="check", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("сервис запущен, обновление подписок каждые %d мин, проверка каждые %d мин",
             settings.fetch_interval_min, settings.check_interval_min)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="subagg", lifespan=lifespan)
security = HTTPBasic(auto_error=True)


@app.middleware("http")
async def set_lang(request: Request, call_next):
    lang = request.cookies.get("lang")
    current_lang.set(lang if lang in LANGS else DEFAULT_LANG)
    return await call_next(request)


@app.get("/lang/{code}")
async def switch_lang(code: str, request: Request):
    """Переключатель языка в шапке: запомнить в cookie и вернуться назад."""
    back = request.headers.get("referer") or "/"
    resp = RedirectResponse(back, status_code=303)
    if code in LANGS:
        resp.set_cookie("lang", code, max_age=365 * 24 * 3600, samesite="lax")
    return resp


def require_admin(creds: HTTPBasicCredentials = Depends(security)) -> str:
    ok_user = secrets.compare_digest(creds.username, settings.admin_user)
    ok_pass = secrets.compare_digest(creds.password, settings.admin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr("неверный логин или пароль"),
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
            "base_url": _base_url(request),
            "jobs": [j.status() for j in JOBS],
        },
    )


def _base_url(request: Request) -> str:
    """Адрес для ссылок на подписки: из SUBAGG_PUBLIC_URL, иначе текущий."""
    return (settings.public_url or str(request.base_url)).rstrip("/")


def _back(to: str = "/") -> RedirectResponse:
    return RedirectResponse(to, status_code=303)


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


async def _token_value(session: AsyncSession, value: str, token_id: int | None = None) -> str | None:
    """Проверить токен, заданный руками. Пусто - None, сгенерируется сам.

    Можно вставить ссылку целиком: берем то, что после /sub/. Так после
    потери базы старые ссылки восстанавливаются копипастой с телефона.
    """
    value = value.strip()
    if "/sub/" in value:
        value = value.split("/sub/", 1)[1]
    value = value.split("?", 1)[0].strip("/")
    if not value:
        return None
    if not TOKEN_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=tr("токен: от 8 до 64 символов, только латиница, цифры, - и _"),
        )
    other = (await session.execute(select(Token).where(Token.token == value))).scalar_one_or_none()
    if other is not None and other.id != token_id:
        raise HTTPException(status_code=400, detail=tr("токен уже занят токеном «{name}»").format(name=other.name))
    return value


def _check_regex(value: str, field: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        re.compile(value)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=tr("{field}: кривой regex: {error}").format(field=field, error=exc)) from exc
    return value


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
            raise ValueError(tr("ожидается JSON-объект"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=tr("заголовки: {error}").format(error=exc)) from exc

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
        raise HTTPException(status_code=404, detail=tr("нет такой подписки"))
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
        raise HTTPException(status_code=404, detail=tr("нет такой подписки"))
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
            raise HTTPException(status_code=400, detail=tr("не разобран JSON: {error}").format(error=exc)) from exc
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
        raise HTTPException(status_code=400, detail=tr("ничего не разобрано из вставленного текста"))

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
        raise HTTPException(status_code=404, detail=tr("нет такой ноды"))
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
        raise HTTPException(status_code=404, detail=tr("нет такой ноды"))
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
    await _commit_target(session)
    return _back()


async def _commit_target(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=tr("цель с таким именем уже есть")) from exc


@app.post("/targets/{target_id}")
async def update_target(
    target_id: int,
    name: str = Form(...),
    url: str = Form(...),
    expected_status: int = Form(204),
    timeout_ms: int = Form(6000),
    required: bool = Form(False),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    t = await session.get(CheckTarget, target_id)
    if not t:
        raise HTTPException(status_code=404, detail=tr("нет такой цели"))
    t.name = name.strip()
    t.url = url.strip()
    t.expected_status = expected_status
    t.timeout_ms = max(500, timeout_ms)
    t.required = required
    await _commit_target(session)
    return _back()


@app.post("/targets/{target_id}/toggle")
async def toggle_target(
    target_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    t = await session.get(CheckTarget, target_id)
    if not t:
        raise HTTPException(status_code=404, detail=tr("нет такой цели"))
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
    token_value: str = Form(""),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    value = await _token_value(session, token_value)
    token = Token(
        name=name.strip(),
        fmt=fmt,
        include_regex=_check_regex(include_regex, "include"),
        exclude_regex=_check_regex(exclude_regex, "exclude"),
        only_alive=only_alive,
        excluded_nodes=[],
    )
    if value:
        token.token = value
    session.add(token)
    await session.commit()
    return _back(f"/tokens/{token.id}")


async def _get_token(session: AsyncSession, token_id: int) -> Token:
    token = await session.get(Token, token_id)
    if not token:
        raise HTTPException(status_code=404, detail=tr("нет такого токена"))
    return token


@app.get("/tokens/{token_id}", response_class=HTMLResponse)
async def token_page(
    token_id: int,
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Что получит устройство по этому токену и почему не получит остальное."""
    token = await _get_token(session, token_id)
    rows = await explain_nodes(session, token)
    return TEMPLATES.TemplateResponse(
        request,
        "token.html",
        {
            "t": token,
            "rows": rows,
            "given": sum(1 for _, reason in rows if reason is None),
            "excluded": set(token.excluded_nodes or []),
            "base_url": _base_url(request),
        },
    )


@app.post("/tokens/{token_id}")
async def update_token(
    token_id: int,
    name: str = Form(...),
    fmt: str = Form("clash"),
    include_regex: str = Form(""),
    exclude_regex: str = Form(""),
    only_alive: bool = Form(False),
    token_value: str = Form(""),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    token = await _get_token(session, token_id)
    value = await _token_value(session, token_value, token_id)
    if value:
        token.token = value
    token.name = name.strip()
    token.fmt = fmt
    token.include_regex = _check_regex(include_regex, "include")
    token.exclude_regex = _check_regex(exclude_regex, "exclude")
    token.only_alive = only_alive
    await session.commit()
    return _back(f"/tokens/{token_id}")


@app.post("/tokens/{token_id}/nodes")
async def update_token_nodes(
    token_id: int,
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Галочки на странице токена: отмеченные ноды отдаем, остальные нет.

    Трогаем только ноды, которые были на странице: если пока ее смотрели,
    из подписки пришли новые, они остаются включенными.
    """
    token = await _get_token(session, token_id)
    form = await request.form()
    shown = {int(v) for v in form.getlist("shown")}
    checked = {int(v) for v in form.getlist("node")}
    excluded = set(token.excluded_nodes or []) - shown
    excluded |= shown - checked
    token.excluded_nodes = sorted(excluded)
    await session.commit()
    return _back(f"/tokens/{token_id}")


@app.post("/tokens/{token_id}/toggle")
async def toggle_token(
    token_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    token = await _get_token(session, token_id)
    token.enabled = not token.enabled
    await session.commit()
    return _back(f"/tokens/{token_id}")


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
async def action_refresh(_: str = Depends(require_admin)):
    FETCH.start()
    return _back()


@app.post("/actions/check")
async def action_check(_: str = Depends(require_admin)):
    CHECK.start()
    return _back()


@app.get("/api/jobs")
async def api_jobs(_: str = Depends(require_admin)):
    return [j.status() for j in JOBS]


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
