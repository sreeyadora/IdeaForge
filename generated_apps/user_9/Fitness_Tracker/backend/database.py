from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

# Support both SQLAlchemy 1.4 and 2.x
try:
    from sqlalchemy.orm import DeclarativeBase
    class Base(DeclarativeBase):
        pass
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base
    Base = declarative_base()

DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "database", "app.db")
)
DATABASE_URL = "sqlite:///" + DB_PATH

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
