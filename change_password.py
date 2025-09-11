# change_password.py
from getpass import getpass
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User
from passlib.context import CryptContext

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main():
    db: Session = SessionLocal()
    try:
        email = input("Email to reset: ").strip().lower()
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print("No user with that email.")
            return
        p1 = getpass("New password: ")
        p2 = getpass("Confirm password: ")
        if p1 != p2:
            print("Passwords do not match.")
            return
        if len(p1) < 8:
            print("Please use at least 8 characters.")
            return
        user.password_hash = pwd.hash(p1)
        db.commit()
        print("Password updated.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

# python change_password.py
