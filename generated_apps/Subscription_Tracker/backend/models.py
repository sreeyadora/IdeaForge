from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base


class Subscription_tracker(Base):
    __tablename__ = "subscription_tracker_items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    category = Column(String(200), default="")
    date = Column(String(20), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
