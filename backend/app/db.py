"""Database engine / session management (SQLite via SQLAlchemy).

The SQLite file must live on a normal local disk. Cloud sync clients
(OneDrive, Dropbox, Google Drive) can mark or lock a database that sits
inside a synced folder, which makes SQLite fail with
"attempt to write a readonly database". SolarSync therefore detects a synced
working directory and moves the database to the user's local application data
folder automatically.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import BACKEND_DIR, get_settings
from .models import Base, CostBenchmarkRow

log = logging.getLogger("solarsync.db")

_settings = get_settings()

_SYNC_FOLDER_MARKERS = ("onedrive", "dropbox", "google drive", "icloud", "box")


def _is_synced_path(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    return any(any(marker in part for marker in _SYNC_FOLDER_MARKERS) for part in parts)


def _local_fallback_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path(tempfile.gettempdir())
    folder = root / "SolarSync"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "solarsync.db"


def _resolve_database_url() -> str:
    """Pick the SQLite URL, relocating it out of a cloud-synced folder."""
    url = _settings.database_url
    if not url.startswith("sqlite"):
        return url

    raw = url.replace("sqlite:///", "").strip()
    if raw in ("", ":memory:"):
        return url

    target = Path(raw)
    if not target.is_absolute():
        target = (BACKEND_DIR / target).resolve()
    if _is_synced_path(target.parent):
        fallback = _local_fallback_path()
        log.warning(
            "The project folder is inside a cloud-synced directory; SQLite files there can be "
            "locked by the sync client. Using %s instead. Override with DATABASE_URL.",
            fallback,
        )
        return f"sqlite:///{fallback.as_posix()}"
    return url


DATABASE_URL = _resolve_database_url()


def _build_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # SQLite serialises writers anyway, and background runs hold sessions for
        # minutes; a fixed QueuePool runs out on Cloud Run (single instance, many
        # concurrent buildings + pollers), so hand out a fresh connection per
        # session and let the driver's busy timeout handle lock contention.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        kwargs["poolclass"] = NullPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _build_engine(DATABASE_URL)


def _probe_write(target: Engine) -> bool:
    """Check that the database file is really writable."""
    try:
        with target.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS _solarsync_probe (x INTEGER)")
            connection.exec_driver_sql("DELETE FROM _solarsync_probe")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Database write probe failed: %s", exc)
        return False


if not _probe_write(engine):
    fallback = _local_fallback_path()
    log.warning(
        "Falling back to a local database file at %s (the configured location is not writable).",
        fallback,
    )
    engine = _build_engine(f"sqlite:///{fallback.as_posix()}")
    DATABASE_URL = f"sqlite:///{fallback.as_posix()}"


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables, apply additive migrations and seed the cost benchmark."""
    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()
    seed_cost_benchmarks()


_SQLITE_TYPE_MAP = {
    "INTEGER": "INTEGER",
    "SMALLINT": "INTEGER",
    "BIGINT": "INTEGER",
    "FLOAT": "REAL",
    "NUMERIC": "REAL",
    "REAL": "REAL",
    "BOOLEAN": "INTEGER",
    "TEXT": "TEXT",
    "VARCHAR": "TEXT",
    "DATETIME": "TEXT",
    "JSON": "TEXT",
}


def _apply_additive_migrations() -> None:
    """Add columns that a newer model introduced to an existing database.

    `create_all()` never alters an existing table, so a database created by an
    older version would break on the first INSERT. New columns are added in
    place, using the live dialect so this also works on PostgreSQL.
    """
    inspector = inspect(engine)
    logger = logging.getLogger("solarsync.db")
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                try:
                    sql_type = column.type.compile(dialect=engine.dialect)
                except Exception:  # noqa: BLE001 - fall back to TEXT
                    sql_type = "TEXT"
                default = ""
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    value = column.default.arg
                    if isinstance(value, (int, float)):
                        default = f" DEFAULT {value}"
                    elif isinstance(value, str):
                        default = f" DEFAULT '{value}'"
                statement = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {sql_type}{default}"
                try:
                    connection.exec_driver_sql(statement)
                    logger.info("Migration: added %s.%s", table.name, column.name)
                except Exception as exc:  # noqa: BLE001
                    connection.rollback()
                    logger.warning("Could not add %s.%s: %s", table.name, column.name, exc)


def seed_cost_benchmarks() -> None:
    benchmark = _settings.cost_benchmark
    with SessionLocal() as session:
        existing = session.query(CostBenchmarkRow).filter(CostBenchmarkRow.benchmark_id == "cb_my_default_1").first()
        if existing:
            return
        tiers = [
            (0.0, 4.0, benchmark.tier_lt_4_kwp_rm_per_kwp),
            (4.0, 10.0, benchmark.tier_4_to_10_kwp_rm_per_kwp),
            (10.0, None, benchmark.tier_gt_10_kwp_rm_per_kwp),
        ]
        for index, (min_kwp, max_kwp, rate) in enumerate(tiers, start=1):
            session.add(
                CostBenchmarkRow(
                    benchmark_id=f"cb_my_default_{index}",
                    region="MY",
                    building_type="residential",
                    capacity_min_kwp=min_kwp,
                    capacity_max_kwp=max_kwp,
                    fixed_base_rm=benchmark.fixed_base_rm,
                    low_rm_per_kwp=round(rate * benchmark.low_multiplier, 2),
                    base_rm_per_kwp=rate,
                    high_rm_per_kwp=round(rate * benchmark.high_multiplier, 2),
                    extra_cost_model="none",
                    included_scope=benchmark.included_scope,
                    source_url=benchmark.source_url,
                    effective_date=benchmark.effective_date,
                    confidence_level="low",
                    sample_count=0,
                )
            )
        session.commit()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def get_session() -> Iterator[Session]:
    """FastAPI dependency.

    Declared as an async generator so the connection is always created in the
    same thread that uses it (the event loop), which keeps SQLite happy for
    both sync and async endpoints.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
