"""
Seeds the three fixed roles (admin, examiner, student) and a default
admin account. Run once after migrations: `python -m app.utils.seed`.
"""
from app.database.session import SessionLocal
from app.models.user import Role, User
from app.core.security import hash_password
from app.models.enums import RoleName


DEFAULT_ADMIN_EMAIL = "admin@examproctor.com"
DEFAULT_ADMIN_PASSWORD = "Admin@12345"


def run():
    db = SessionLocal()
    try:
        for role_name in [RoleName.ADMIN.value, RoleName.EXAMINER.value, RoleName.STUDENT.value]:
            if not db.query(Role).filter(Role.name == role_name).first():
                db.add(Role(name=role_name))
        db.commit()

        admin_role = db.query(Role).filter(Role.name == RoleName.ADMIN.value).first()
        if not db.query(User).filter(User.email == DEFAULT_ADMIN_EMAIL).first():
            admin = User(
                email=DEFAULT_ADMIN_EMAIL,
                hashed_password=hash_password(DEFAULT_ADMIN_PASSWORD),
                role_id=admin_role.id,
            )
            admin.set_name("System", "Admin")
            db.add(admin)
            db.commit()
            print(f"Seeded default admin: {DEFAULT_ADMIN_EMAIL} / {DEFAULT_ADMIN_PASSWORD}")
        else:
            print("Default admin already exists.")
        print("Roles seeded.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
