from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base


class Project_task_manager(Base):
    __tablename__ = "project_task_manager_items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    priority = Column(String(200), default="")
    status = Column(String(200), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
