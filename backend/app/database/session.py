"""
SQLAlchemy engine and session management.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# SQLite (used for the automated test suite, see backend/tests/) needs a couple of
# extra options that Postgres does not: allow the connection to be shared across
# the threadpool FastAPI runs sync routes in, and pin it to a single connection
# when it's an in-memory database so every session sees the same schema/data.
_engine_kwargs: dict = {"pool_pre_ping": True}

if settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in settings.DATABASE_URL:
        from sqlalchemy.pool import StaticPool
        _engine_kwargs["poolclass"] = StaticPool
else:
    # Pool sizing, which starts mattering the moment core-api runs more than one
    # worker. The pool is PER PROCESS, so the real connection count against
    # Postgres is (pool_size + max_overflow) x workers x replicas -- and
    # Postgres's own default max_connections is 100. Four workers on SQLAlchemy's
    # defaults (5 + 10) is already 60 connections from one container, and adding
    # the worker and scheduler services on top is how a stack that ran fine
    # yesterday starts refusing connections today.
    #
    # These defaults are deliberately modest for that reason: a request holds a
    # connection only for the duration of its query, so a small pool with a
    # short queue serves far more traffic than its size suggests.
    _engine_kwargs.update({
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        # Wait briefly for a free connection rather than forever. Without this a
        # pool exhausted by one slow query turns every other request into a hang
        # with no error, which is much harder to diagnose than a clean 503.
        "pool_timeout": settings.DB_POOL_TIMEOUT_SECONDS,
        # Recycle before anything upstream silently drops the connection --
        # Postgres, pgbouncer and cloud load balancers all reap idle sessions,
        # and a recycled connection is cheaper than discovering a dead one
        # mid-transaction. pool_pre_ping above catches the rest.
        "pool_recycle": settings.DB_POOL_RECYCLE_SECONDS,
    })

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a DB session, rolling back on error and always closing it."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
