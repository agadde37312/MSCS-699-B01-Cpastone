"""
Database engine / session setup.

Reads the database URL from the DATABASE_URL environment variable so the
same code works against:
  - a real Postgres instance in production/dev
    (e.g. postgresql+psycopg2://user:pass@localhost/healthtrack_phase3)
  - a throwaway SQLite database for local testing (the default here),
    so `pytest` works out of the box with zero external setup.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./healthtrack.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a Session, always closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
