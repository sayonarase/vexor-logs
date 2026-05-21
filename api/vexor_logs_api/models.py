"""SQLAlchemy models for log alerts + saved searches."""
from __future__ import annotations
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
try:
    from app.database import Base  # type: ignore
except Exception:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()


class LogAlertRule(Base):
    __tablename__ = 'log_alert_rules'
    id           = Column(Integer, primary_key=True)
    name         = Column(String(255), nullable=False, unique=True)
    query        = Column(Text, nullable=False)
    window_sec   = Column(Integer, nullable=False, default=300)
    threshold    = Column(Integer, nullable=False, default=1)
    severity     = Column(String(32), nullable=False, default='warning')
    notify_to    = Column(String(255), nullable=False, default='')
    host_binding = Column(String(255), nullable=True)
    enabled      = Column(Boolean, nullable=False, default=True)
    last_fired   = Column(DateTime(timezone=True), nullable=True)
    last_count   = Column(Integer, nullable=False, default=0)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LogSavedSearch(Base):
    __tablename__ = 'log_saved_searches'
    id          = Column(Integer, primary_key=True)
    name        = Column(String(255), nullable=False, unique=True)
    query       = Column(Text, nullable=False)
    time_range  = Column(String(64), nullable=False, default='1h')
    created_by  = Column(String(128), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
