from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base


class Gym_tracker(Base):
    __tablename__ = "gym_tracker_items"

    id = Column(Integer, primary_key=True, index=True)
    exercise = Column(String(200), nullable=False)
    sets = Column(Integer, nullable=False, default=0)
    reps = Column(Integer, nullable=False, default=0)
    weight = Column(Float, nullable=False, default=0.0)
    workout_date = Column(String(20), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
