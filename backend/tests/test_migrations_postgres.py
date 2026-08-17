"""Runs the Alembic chain against a real PostgreSQL server."""
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.postgres


# --- getting a server ---------------------------------------------------------

@pytest.fixture(scope="session")
def postgres_server():
    """A base URL to a live PostgreSQL, or a skip."""
    explicit = os.getenv("TEST_POSTGRES_URL", "").strip()

    if explicit:
        admin = sa.create_engine(explicit, isolation_level="AUTOCOMMIT")

        def make(name: str) -> str:
            with admin.connect() as conn:
                conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
                conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
            base, _, _ = explicit.rpartition("/")
            return f"{base}/{name}"

        yield make
        admin.dispose()
        return

    pgserver = pytest.importorskip(
        "pgserver",
        reason="No PostgreSQL for migration tests. Set TEST_POSTGRES_URL, or "
               "`pip install pgserver` to have these boot a throwaway server.",
    )
    data_dir = Path(tempfile.mkdtemp(prefix="meritai_pgtest_"))
    server = pgserver.get_server(data_dir)

    def make(name: str) -> str:
        server.psql(f"DROP DATABASE IF EXISTS {name};")
        server.psql(f"CREATE DATABASE {name};")
        return f"postgresql://postgres:@/{name}?host={data_dir}"

    yield make

    # pgserver shuts its postmaster down from an atexit hook, which runs after pytest has
    # already closed the streams its logger writes to.
    logging.getLogger("pgserver").disabled = True
    logging.getLogger("pgserver._commands").disabled = True


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    """Run alembic in a subprocess against `url`."""
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "SECRET_KEY": "migration-test-secret-key-long-enough",
        "CORS_ORIGINS": "http://testserver",
        "UPLOAD_DIR": tempfile.mkdtemp(prefix="meritai_migtest_uploads_"),
        "ENVIRONMENT": "development",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env=env, cwd=str(BACKEND_DIR), capture_output=True, text=True,
    )


def _enum_labels(url: str, type_name: str) -> list[str]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return [
                row[0] for row in conn.execute(
                    sa.text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = :name ORDER BY e.enumsortorder"
                    ),
                    {"name": type_name},
                )
            ]
    finally:
        engine.dispose()


# --- the tests ----------------------------------------------------------------

@pytest.fixture(scope="session")
def migrated_url(postgres_server):
    """One database taken all the way to head, shared by the read-only tests."""
    url = postgres_server("meritai_mig_head")
    result = _alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"`alembic upgrade head` failed:\n{result.stderr[-3000:]}"
    return url


def test_0022_backfills_the_password_epoch_without_signing_everyone_out(postgres_server):
    """The subtle half of 0022, and the one that would hurt on deploy day."""
    url = postgres_server("meritai_mig_0022")
    assert _alembic(url, "upgrade", "0021").returncode == 0

    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO roles (name) VALUES ('student')"))
            conn.execute(sa.text(
                "INSERT INTO users (first_name, last_name, full_name, email, hashed_password, "
                "role_id, is_active, created_at) "
                "SELECT 'Old', 'Account', 'Old Account', 'old@example.com', 'x', id, true, "
                "  now() - interval '30 days' FROM roles WHERE name = 'student'"
            ))

        assert _alembic(url, "upgrade", "0022").returncode == 0

        with engine.connect() as conn:
            created_at, changed_at = conn.execute(sa.text(
                "SELECT created_at, password_changed_at FROM users WHERE email = 'old@example.com'"
            )).one()
            verified = conn.execute(sa.text(
                "SELECT email_verified_at FROM users WHERE email = 'old@example.com'"
            )).scalar()

        assert changed_at == created_at, (
            "the epoch was not backfilled from created_at -- deploying this would invalidate "
            "every token in flight"
        )
        # And nobody is retroactively declared verified: NULL is the honest
        # answer for an account created before anyone was asked.
        assert verified is None
    finally:
        engine.dispose()


def test_the_whole_chain_applies_to_a_clean_database(migrated_url):
    """The check that would have caught 0016's DuplicateObject before deploy."""
    engine = sa.create_engine(migrated_url)
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            tables = {
                row[0] for row in conn.execute(
                    sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
    finally:
        engine.dispose()

    assert version, "no alembic_version row after upgrade"
    # Spot-check across the chain rather than pinning an exact count, which
    # would turn every legitimate new table into a failing test.
    for expected in ("users", "exams", "questions", "student_exam_attempts",
                     "proctor_events", "access_requests", "otp_codes"):
        assert expected in tables, f"{expected} missing after upgrade head"


@pytest.mark.parametrize("type_name, required", [
    ("questiontype", {"mcq", "coding", "multi_select"}),
    ("eventtype", {"screen_share_stopped", "noise_detected_loud", "spoof_detected"}),
    ("otppurpose", {"signup", "password_reset", "activation"}),
])
def test_native_enum_types_contain_every_value_the_app_uses(migrated_url, type_name, required):
    labels = set(_enum_labels(migrated_url, type_name))
    assert labels, f"enum type {type_name} does not exist after upgrade head"
    missing = required - labels
    assert not missing, (
        f"{type_name} is missing {sorted(missing)} on a migrated database. "
        f"Inserting one raises InvalidTextRepresentation at runtime."
    )


def test_multi_select_and_screen_share_stopped_are_rejected_before_0017(postgres_server):
    """Pins WHY 0017 exists."""
    url = postgres_server("meritai_mig_0016")
    result = _alembic(url, "upgrade", "0016")
    assert result.returncode == 0, f"upgrade to 0016 failed:\n{result.stderr[-2000:]}"

    engine = sa.create_engine(url)
    try:
        for literal, type_name in (("multi_select", "questiontype"),
                                   ("screen_share_stopped", "eventtype")):
            with pytest.raises(sa.exc.DataError):
                with engine.begin() as conn:
                    conn.execute(sa.text(f"SELECT '{literal}'::{type_name}"))
    finally:
        engine.dispose()


def test_activation_rows_and_the_must_change_flag_work_after_0026(migrated_url):
    """The 0017 lesson, applied to 0026."""
    engine = sa.create_engine(migrated_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO otp_codes (email, purpose, code_hash, expires_at, attempts) "
                "VALUES ('activate@example.com', 'activation', :h, NOW() + INTERVAL '72 hours', 0)"
            ), {"h": "a" * 64})
            stored = conn.execute(sa.text(
                "SELECT purpose::text FROM otp_codes WHERE email = 'activate@example.com'"
            )).scalar()
        assert stored == "activation"

        # And the column the login response reads, with the backfill 0026
        # promises: FALSE for rows that already existed, never NULL.
        with engine.begin() as conn:
            nullable, default = conn.execute(sa.text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'must_change_password'"
            )).one()
        assert nullable == "NO"
        assert default is not None and "false" in default.lower()
    finally:
        engine.dispose()


def test_every_migration_can_be_rolled_back(migrated_url):
    """A downgrade path nobody ever runs is a downgrade path that does not work."""
    down = _alembic(migrated_url, "downgrade", "base")
    assert down.returncode == 0, f"`alembic downgrade base` failed:\n{down.stderr[-3000:]}"

    back_up = _alembic(migrated_url, "upgrade", "head")
    assert back_up.returncode == 0, f"re-upgrade after downgrade failed:\n{back_up.stderr[-3000:]}"
