from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base


class Study_planner(Base):
    __tablename__ = "study_planner_items"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String(200), nullable=False)
    cost = Column(Float, nullable=False, default=0.0)
    billing_cycle = Column(String(200), default="")
    next_payment_date = Column(String(20), default="")
    active = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
