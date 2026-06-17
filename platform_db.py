"""
IdeaForge — Platform Database
"""
import os
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import relationship, sessionmaker

try:
    from sqlalchemy.orm import DeclarativeBase
    class Base(DeclarativeBase):
        pass
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base
    Base = declarative_base()

_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "ideaforge.db"))
_ENGINE = create_engine(
    f"sqlite:///{_DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


def get_platform_db():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_platform_db_sync():
    return _SessionLocal()


class User(Base):
    __tablename__ = "platform_users"
    id         = Column(Integer, primary_key=True, index=True)
    username   = Column(String(80), unique=True, nullable=False, index=True)
    password   = Column(String(128), nullable=False)
    is_admin   = Column(Boolean, default=False, nullable=False)
    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    apps = relationship("AppRecord", back_populates="owner", cascade="all, delete-orphan")


class AppRecord(Base):
    __tablename__ = "platform_apps"
    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(120), nullable=False)
    path       = Column(String(500), nullable=False)
    user_id    = Column(Integer, ForeignKey("platform_users.id"), nullable=False, index=True)
    app_type   = Column(String(80), default="Custom CRUD App")
    run_count  = Column(Integer, default=0, nullable=False)
    last_run   = Column(DateTime, nullable=True)
    port       = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    owner = relationship("User", back_populates="apps")


Base.metadata.create_all(bind=_ENGINE)