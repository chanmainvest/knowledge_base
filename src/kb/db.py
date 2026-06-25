"""Sync + async DB engines and helpers."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings


_engine: Engine | None = None
_async_engine = None
_SessionLocal: sessionmaker[Session] | None = None
_AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(settings().db_url, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def async_engine():
    global _async_engine, _AsyncSessionLocal
    if _async_engine is None:
        _async_engine = create_async_engine(settings().db_url_async, pool_pre_ping=True)
        _AsyncSessionLocal = async_sessionmaker(_async_engine, expire_on_commit=False)
    return _async_engine


@contextmanager
def session():
    engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@asynccontextmanager
async def asession():
    async_engine()
    assert _AsyncSessionLocal is not None
    async with _AsyncSessionLocal() as s:
        yield s


def exec_sql(sql: str, **params: Any) -> None:
    with engine().begin() as c:
        c.execute(text(sql), params)
