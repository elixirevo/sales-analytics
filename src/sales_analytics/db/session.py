from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    path = Path(database_url.removeprefix("sqlite:///"))
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    _ensure_sqlite_parent(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_database(database_url: str) -> None:
    factory = create_session_factory(database_url)
    Base.metadata.create_all(factory.kw["bind"])
