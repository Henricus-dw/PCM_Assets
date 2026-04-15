# create_user.py
from getpass import getpass
from database import SessionLocal, engine, ensure_local_sqlite_schema
from models import Base, User
from passlib.context import CryptContext

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
db = SessionLocal()

# ensure tables exist
Base.metadata.create_all(bind=engine)
ensure_local_sqlite_schema(Base)

email = input("Email: ").strip().lower()
password = getpass("Password: ")

user = db.query(User).filter(User.email == email).first()
if user:
    print("User already exists.")
else:
    u = User(email=email, password_hash=pwd.hash(password))
    db.add(u)
    db.commit()
    print("User created.")
db.close()

# python create_user.py
