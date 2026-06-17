from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base


class Study_planner_ai(Base):
    __tablename__ = "study_planner_ai_items"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String(200), nullable=False)
    topic = Column(String(200), default="")
    duration_minutes = Column(Integer, nullable=False, default=0)
    study_date = Column(String(20), default="")
    completed = Column(Boolean, default=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
