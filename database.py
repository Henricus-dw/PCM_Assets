import logging
import os

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker, declarative_base


logger = logging.getLogger(__name__)

ENV = os.getenv("APP_ENV", "production").strip().lower()

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


def _column_default_sql(column):
    if column.server_default is not None:
        return str(column.server_default.arg)

    if column.default is not None and getattr(column.default, "is_scalar", False):
        value = column.default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return f"'{value}'"

    return None


def ensure_local_sqlite_schema(base):
    if ENV != "local" or not DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue

            existing_columns = {
                column["name"] for column in inspector.get_columns(table.name)
            }

            for column in table.columns:
                if column.name in existing_columns or column.primary_key:
                    continue

                default_sql = _column_default_sql(column)
                if not column.nullable and default_sql is None:
                    logger.warning(
                        "Skipping local SQLite migration for %s.%s because it is NOT NULL without a default",
                        table.name,
                        column.name,
                    )
                    continue

                column_type = column.type.compile(dialect=engine.dialect)
                parts = [
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'
                ]
                if default_sql is not None:
                    parts.append(f"DEFAULT {default_sql}")
                if not column.nullable:
                    parts.append("NOT NULL")

                sql = " ".join(parts)
                conn.exec_driver_sql(sql)
                logger.info("Applied local SQLite schema update: %s", sql)
