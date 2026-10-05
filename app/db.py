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
        await conn.run_sync(_add_missing_columns)
    await _refresh_fingerprints()


# create_all не трогает существующие таблицы, новые колонки докидываем сами
_NEW_COLUMNS = {
    "tokens": {"excluded_nodes": "JSON NOT NULL DEFAULT '[]'"},
}


def _add_missing_columns(conn) -> None:
    from sqlalchemy import text

    for table, cols in _NEW_COLUMNS.items():
        have = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        for name, ddl in cols.items():
            if name not in have:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


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
