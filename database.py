from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

ENV = os.getenv("APP_ENV", "production")

if ENV == "local":
    # Local dev: plug-and-play DB file in the project folder
    DATABASE_URL = "sqlite:///./local.db"

    engine = create_engine(
        DATABASE_URL,
        # needed for SQLite + FastAPI
        connect_args={"check_same_thread": False},
    )
else:
    # Production: keep hard-coded MySQL exactly as before
    DATABASE_URL = "mysql+mysqlconnector://pcm_user:Activate!2000@localhost/pcm_db"
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
