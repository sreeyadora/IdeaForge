from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base


class Travel_itinerary_generator(Base):
    __tablename__ = "travel_itinerary_generator_items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    category = Column(String(200), default="")
    date = Column(String(20), default="")
    is_recurring = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
