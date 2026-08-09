import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DB_PATH = os.path.join(os.path.dirname(__file__), "healthtrack_perf.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
