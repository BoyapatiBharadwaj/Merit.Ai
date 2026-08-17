"""
SQLAlchemy engine and session management.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# SQLite (used for the automated test suite, see backend/tests/)
# needs a couple of extra options that Postgres does not.
_engine_kwargs: dict = {"pool_pre_ping": True}

if settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in settings.DATABASE_URL:
        from sqlalchemy.pool import StaticPool
        _engine_kwargs["poolclass"] = StaticPool
else:
    # Pool sizing, which starts mattering the moment core-api runs more than one worker.
    _engine_kwargs.update({
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        # Wait briefly for a free connection rather than forever.
        "pool_timeout": settings.DB_POOL_TIMEOUT_SECONDS,
        # Recycle before anything upstream silently drops the connection.
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
