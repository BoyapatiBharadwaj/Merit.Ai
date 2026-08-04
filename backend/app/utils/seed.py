"""
Seeds the three fixed roles (admin, examiner, student) and, optionally, a
first admin account. Run after migrations: `python -m app.utils.seed`.

Roles are always safe to seed: they are three fixed rows the application cannot
function without, and creating them grants nobody access to anything.

The admin account is a different matter, and this module used to treat the two
the same. It hard-coded `admin@examproctor.com` / `Admin@12345`, created that
account on every boot (entrypoint.sh runs this with AUTO_SEED_ADMIN defaulting
to true), and printed the password to stdout. Both halves of that credential
appear in this repository, in .env.example, and in the README -- so every
deployment that never got around to changing it shares one publicly known
administrator login, and the password lands in the container logs of whatever
platform is collecting them.

What happens now instead:

  * SEED_ADMIN_PASSWORD unset, non-production -> a random password is generated
    and printed once. Local development still works with zero setup, and the
    credential is different on every machine.
  * SEED_ADMIN_PASSWORD unset, production -> the account is NOT created, and
    the reason is logged. A deployment that forgot to configure this ends up
    with no admin rather than a publicly known one; `python -m app.utils.seed`
    can be run by hand once a real password is set.
  * SEED_ADMIN_PASSWORD set to the old public default -> refused in production.
  * The password is never printed when it was supplied by the operator, who
    already knows it and does not need it in the logs.
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

# The credential this file used to hard-code. Kept only so it can be recognised
# and rejected -- a deployment that pastes it into SEED_ADMIN_PASSWORD to "keep
# things working" is exactly the case this whole change exists to stop.
PUBLICLY_KNOWN_PASSWORD = "Admin@12345"


def _resolve_admin_password() -> tuple[str | None, bool]:
    """Returns (password, should_print).

    `should_print` is False whenever the operator supplied the password: they
    already have it, and echoing it would put a live admin credential into
    stdout and therefore into whatever aggregates the container's logs.
    """
    configured = os.getenv("SEED_ADMIN_PASSWORD", "").strip()

    if configured:
        if configured == PUBLICLY_KNOWN_PASSWORD and settings.is_production:
            logger.error(
                "Refusing to seed an admin with the publicly known default password. "
                "Set SEED_ADMIN_PASSWORD to something else."
            )
            return None, False
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
