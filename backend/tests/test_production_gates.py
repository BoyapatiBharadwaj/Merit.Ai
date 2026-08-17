"""Regression cover for the four pre-production hardening fixes."""
import importlib
import re
from pathlib import Path

import pytest

from app.core import rate_limit
from app.core.config import settings
from app.models.enums import EventType, QuestionType
from app.models.user import User

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


# --- 1. every Python enum member has a PostgreSQL migration -------------------

def _migrated_enum_values() -> dict[str, set[str]]:
    """Replay every migration's DDL to find which enum values Postgres knows."""
    found: dict[str, set[str]] = {}

    def add(type_name: str, *values: str) -> None:
        found.setdefault(type_name, set()).update(v for v in values if v)

    for path in sorted(VERSIONS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")

        # (a) Fully literal:  ALTER TYPE eventtype ADD VALUE ... 'spoof_detected'
        for type_name, value in re.findall(
            r"ALTER TYPE (\w+) ADD VALUE IF NOT EXISTS '([a-z_]+)'", source
        ):
            add(type_name, value)

        # (b) Literal type, templated value, driven by a module-level list of
        #     plain strings -- the shape used by 0003, 0005 and 0007.
        for type_name in re.findall(r"ALTER TYPE (\w+) ADD VALUE IF NOT EXISTS '\{value\}'", source):
            for list_body in re.findall(r"^[A-Z_]+ = \[(.*?)\]", source, re.S | re.M):
                add(type_name, *re.findall(r'"([a-z_]+)"', list_body))

        # (c) Both templated, driven by a list of (type, value) pairs -- 0017.
        if re.search(r"ALTER TYPE \{\w+\} ADD VALUE IF NOT EXISTS '\{value\}'", source):
            for type_name, value in re.findall(r'\(\s*"([a-z_]+)"\s*,\s*"([a-z_]+)"\s*\)', source):
                add(type_name, value)

        # (d) Type created with its members inline: sa.Enum("mcq", "coding", name="questiontype")
        for members, type_name in re.findall(
            r'sa\.Enum\(((?:\s*"[a-z_]+"\s*,\s*)+)name="(\w+)"', source
        ):
            add(type_name, *re.findall(r'"([a-z_]+)"', members))

        # (e) Type created from a module constant: sa.Enum(*OTP_PURPOSE_VALUES, name="otppurpose")
        for const_name, type_name in re.findall(r'sa\.Enum\(\*(\w+),\s*name="(\w+)"', source):
            body = re.search(rf"^{const_name} = [\(\[](.*?)[\)\]]", source, re.S | re.M)
            if body:
                add(type_name, *re.findall(r'"([a-z_]+)"', body.group(1)))

    return found


@pytest.mark.parametrize("enum_class, type_name", [
    (QuestionType, "questiontype"),
    (EventType, "eventtype"),
])
def test_every_enum_member_exists_in_the_migration_history(enum_class, type_name):
    """The bug this suite could never see before."""
    migrated = _migrated_enum_values().get(type_name, set())
    declared = {member.value for member in enum_class}
    missing = declared - migrated
    assert not missing, (
        f"{enum_class.__name__} members {sorted(missing)} have no ALTER TYPE in any migration. "
        f"They will fail with InvalidTextRepresentation on a real PostgreSQL database. "
        f"Add a migration with: ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '<value>'"
    )


def test_the_migration_chain_is_linear_with_one_head():
    """A duplicate revision id or a fork makes `alembic upgrade head` refuse to
    run -- easy to introduce when two changes are written in parallel, and
    invisible until deploy time."""
    revisions, downs = {}, {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision = "([^"]+)"', source, re.M).group(1)
        down = re.search(r'^down_revision = (?:"([^"]+)"|None)', source, re.M).group(1)
        assert rev not in revisions, f"duplicate revision {rev}: {path.name} and {revisions[rev]}"
        revisions[rev] = path.name
        downs[rev] = down

    heads = [rev for rev in revisions if rev not in downs.values()]
    assert len(heads) == 1, f"expected exactly one head, found {heads}"
    assert list(downs.values()).count(None) == 1, "expected exactly one root migration"


# --- 2. the admin seed ships no known credential ------------------------------

def test_seed_module_contains_no_hardcoded_live_password():
    """The old file hard-coded Admin@12345 as the password it would actually
    use. It may now only appear as a value to REJECT."""
    from app.utils import seed

    source = Path(seed.__file__).read_text(encoding="utf-8")
    assert "PUBLICLY_KNOWN_PASSWORD" in source
    assert 'hash_password("Admin@12345")' not in source
    assert "DEFAULT_ADMIN_PASSWORD" not in source


def test_production_without_a_configured_password_seeds_no_admin(monkeypatch):
    """Better no administrator than a guessable one."""
    from app.utils import seed

    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    password, should_print = seed._resolve_admin_password()
    assert password is None


def test_the_publicly_known_password_is_allowed_but_warned_about(monkeypatch, caplog):
    """Explicitly configuring it is honoured; accidentally inheriting it is not."""
    from app.utils import seed

    monkeypatch.setenv("SEED_ADMIN_PASSWORD", seed.PUBLICLY_KNOWN_PASSWORD)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    with caplog.at_level("WARNING"):
        password, should_print = seed._resolve_admin_password()

    assert password == seed.PUBLICLY_KNOWN_PASSWORD
    assert should_print is False
    # The warning is the whole point of still recognising the value.
    assert any("publicly known" in record.message for record in caplog.records)


def test_development_generates_a_random_password_each_time(monkeypatch):
    from app.utils import seed

    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    first, should_print = seed._resolve_admin_password()
    second, _ = seed._resolve_admin_password()
    assert first and second and first != second
    assert should_print is True


def test_a_configured_password_is_never_printed(monkeypatch):
    """The operator already has it; echoing it puts a live admin credential in
    whatever aggregates container logs."""
    from app.utils import seed

    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "a-real-configured-secret")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    password, should_print = seed._resolve_admin_password()
    assert password == "a-real-configured-secret"
    assert should_print is False


# --- 3. interactive docs are development-only ---------------------------------

def test_docs_are_served_in_development(client):
    assert client.get("/openapi.json").status_code == 200


def test_docs_are_disabled_in_production(monkeypatch):
    """Rebuilds the app with ENVIRONMENT=production, because docs_url is fixed
    at FastAPI() construction time -- asserting against the running test app
    would only ever prove the development branch."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    import app.main

    reloaded = importlib.reload(app.main)
    try:
        paths = {route.path for route in reloaded.app.routes}
        assert "/openapi.json" not in paths
        assert "/docs" not in paths
        assert "/redoc" not in paths
    finally:
        # Restore the module for every other test in the session.
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")
        importlib.reload(app.main)


def test_compose_does_not_publish_the_core_api_port():
    """The proxy is meant to be the only door in -- and the only thing applying
    nginx's client_max_body_size to the multi-megabyte proctoring payloads."""
    compose = (Path(__file__).resolve().parent.parent.parent / "docker-compose.yml")
    if not compose.exists():
        pytest.skip("docker-compose.yml not present in this checkout")
    text = compose.read_text(encoding="utf-8")
    active = [line for line in text.splitlines()
              if '"8000:8000"' in line and not line.strip().startswith("#")]
    assert not active, f"core-api still publishes its port directly: {active}"


# --- 4. per-user throttling on the AI endpoints -------------------------------

@pytest.fixture(autouse=True)
def _clear_counters():
    rate_limit._reset_all()
    yield
    rate_limit._reset_all()


def test_consume_allows_up_to_the_limit_then_refuses():
    for _ in range(5):
        rate_limit.consume("test_bucket", "user-1", limit=5, window=60)
    with pytest.raises(Exception) as caught:
        rate_limit.consume("test_bucket", "user-1", limit=5, window=60)
    assert caught.value.status_code == 429


def test_one_users_budget_does_not_affect_another():
    """The reason this is keyed on user id and not IP: an exam hall behind one
    NAT shares a source address, and IP-keying would throttle the whole room."""
    for _ in range(5):
        rate_limit.consume("test_bucket", "user-1", limit=5, window=60)
    rate_limit.consume("test_bucket", "user-2", limit=5, window=60)  # must not raise


def test_buckets_are_independent_per_endpoint():
    """Sharing one bucket across all four proctoring endpoints would let their
    individually-safe polling rates add up to a limit none would hit alone."""
    for _ in range(5):
        rate_limit.consume("ai_pose", "user-1", limit=5, window=60)
    rate_limit.consume("ai_objects", "user-1", limit=5, window=60)  # must not raise


def test_every_expensive_ai_endpoint_is_throttled():
    """Guards against a new inference endpoint being added without a limit --
    the failure mode is silent, and only shows up as an exam hall grinding to a
    halt under load."""
    source = (Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "proctoring.py") \
        .read_text(encoding="utf-8")
    for endpoint in ("/face/register", "/face/verify", "/id-card/verify",
                     "/objects/detect", "/pose/check"):
        block = source.split(f'"{endpoint}"')[1].split("def ")[0]
        assert "rate_limit_user" in block, f"{endpoint} has no per-user rate limit"


def test_the_configured_limit_leaves_room_for_normal_exam_polling():
    """proctoring.js polls pose every 5s (12/min), objects every 10s (6/min) and
    face identity every 12s (5/min). A limit at or below the busiest of those
    would throttle a candidate who is doing nothing wrong."""
    busiest_per_minute = 12
    per_minute = settings.AI_RATE_LIMIT_MAX_REQUESTS * (60 / settings.AI_RATE_LIMIT_WINDOW_SECONDS)
    assert per_minute >= busiest_per_minute * 2, (
        f"AI rate limit allows {per_minute}/min but the client already polls "
        f"{busiest_per_minute}/min; leave at least 2x headroom for retries and slow ticks."
    )
