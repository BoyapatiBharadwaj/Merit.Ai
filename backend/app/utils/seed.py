"""Seeds the three fixed roles (admin, examiner, student) and, optionally, a first admin
account. Run after migrations: `python -m app.utils.seed`.
"""
import logging
import os
import secrets

from app.core.config import settings
from app.core.security import hash_password
from app.database.session import SessionLocal
from app.models.enums import RoleName
from app.models.user import Role, User

logger = logging.getLogger("app")

DEFAULT_ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@merit.ai")

# The credential this file used to hard-code. Kept so an explicit use of it can
# still be called out in the logs -- see _resolve_admin_password.
PUBLICLY_KNOWN_PASSWORD = "Admin@12345"


def _resolve_admin_password() -> tuple[str | None, bool]:
    """Returns (password, should_print)."""
    configured = os.getenv("SEED_ADMIN_PASSWORD", "").strip()

    if configured:
        if configured == PUBLICLY_KNOWN_PASSWORD:
            # Warn loudly, but proceed.
            logger.warning(
                "SEED_ADMIN_PASSWORD is the publicly known default from .env.example. "
                "Anyone who has seen this repository can sign in as an administrator. "
                "Fine for local development -- change it before real candidates use this."
            )
        return configured, False

    if settings.is_production:
        logger.warning(
            "No admin seeded: SEED_ADMIN_PASSWORD is not set and ENVIRONMENT=production. "
            "Set it and run `python -m app.utils.seed` to create the first administrator."
        )
        return None, False

    # token_urlsafe, not a fixed string: different on every machine and every
    # rebuild, so nothing about one developer's box tells you anything about
    # another's -- or about a deployment.
    return secrets.token_urlsafe(16), True


def seed_roles(db) -> None:
    for role_name in (RoleName.ADMIN.value, RoleName.EXAMINER.value, RoleName.STUDENT.value):
        if not db.query(Role).filter(Role.name == role_name).first():
            db.add(Role(name=role_name))
    db.commit()


def run():
    db = SessionLocal()
    try:
        seed_roles(db)
        logger.info("Roles seeded.")

        if db.query(User).filter(User.email == DEFAULT_ADMIN_EMAIL).first():
            logger.info("Admin account %s already exists; leaving it alone.", DEFAULT_ADMIN_EMAIL)
            return

        password, should_print = _resolve_admin_password()
        if password is None:
            return

        admin_role = db.query(Role).filter(Role.name == RoleName.ADMIN.value).first()
        admin = User(
            email=DEFAULT_ADMIN_EMAIL,
            hashed_password=hash_password(password),
            role_id=admin_role.id,
        )
        admin.set_name("System", "Admin")
        db.add(admin)
        db.commit()

        if should_print:
            # Deliberately print() rather than logger.info: this is a one-time
            # interactive setup message for whoever is watching the first boot,
            # not an operational log line that should be shipped and retained.
            print("=" * 70)
            print("  Seeded the first administrator. This is shown ONCE.")
            print(f"  Email:    {DEFAULT_ADMIN_EMAIL}")
            print(f"  Password: {password}")
            print("  Change it after signing in.")
            print("=" * 70)
        else:
            logger.info("Seeded administrator %s with the configured password.", DEFAULT_ADMIN_EMAIL)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run()
