import os
import sys
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB_URL = "sqlite:///./test_healthtrack.db"


@pytest.fixture(scope="function")
def db_session():
    from app.models import Base

    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_healthtrack.db"):
            os.remove("./test_healthtrack.db")


@pytest.fixture(scope="function")
def client(monkeypatch):
    """FastAPI TestClient wired to an isolated SQLite test database."""
    if os.path.exists("./test_client.db"):
        os.remove("./test_client.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_client.db")

    # Reload app modules fresh so they pick up the test DATABASE_URL
    import importlib
    import app.database as database_module
    importlib.reload(database_module)
    import app.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c

    if os.path.exists("./test_client.db"):
        os.remove("./test_client.db")
