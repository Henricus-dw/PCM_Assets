from models import User
from database import SessionLocal
import os
import sys
from passlib.context import CryptContext

# Ensure project root is on sys.path so imports work when running from scripts/
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

# Force local SQLite
os.environ["APP_ENV"] = "local"


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(plain: str) -> str:
    return pwd_context.hash(plain)


def main():
    email = input("Admin email: ").strip().lower()
    password = input("Admin password: ").strip()
    name = input("Name (optional): ").strip() or None
    surname = input("Surname (optional): ").strip() or None

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print("User already exists; updating password and admin flag.")
            existing.password_hash = get_password_hash(password)
            existing.name = name
            existing.surname = surname
            existing.is_admin = True
        else:
            user = User(
                email=email,
                password_hash=get_password_hash(password),
                name=name,
                surname=surname,
                is_admin=True,
            )
            db.add(user)
        db.commit()
        print("Local admin user saved.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
