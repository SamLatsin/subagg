from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=False,
    future=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from . import models  # noqa: F401  регистрация таблиц

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _refresh_fingerprints()


async def _refresh_fingerprints() -> None:
    """Пересчитать отпечатки, если поменялась формула fingerprint().

    Иначе после обновления все ноды подписок считаются новыми (теряется
    история проверок), а свои ноды при повторной вставке дублируются.
    """
    from sqlalchemy import select

    from .models import Node
    from .parsers import fingerprint

    async with SessionLocal() as session:
        nodes = (await session.execute(select(Node))).scalars().all()
        changed = 0
        for n in nodes:
            fp = fingerprint(n.config or {})
            if fp != n.fingerprint:
                n.fingerprint = fp
                changed += 1
        if changed:
            await session.commit()
