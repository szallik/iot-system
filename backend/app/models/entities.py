from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.core.security import utc_now_iso


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    login = Column(String(64), unique=True, nullable=False, index=True)
    hash_pin = Column(String(128), nullable=False)
    password_hash = Column(String(128), nullable=True)
    rola = Column(String(32), nullable=False, default="user")
    aktywny = Column(Boolean, nullable=False, default=True)
    created_at = Column(String(32), nullable=False, default=utc_now_iso)

    access_attempts = relationship(
        "AccessAttempt", back_populates="user", cascade="all, delete-orphan"
    )


class Device(Base):
    __tablename__ = "devices"

    id = Column(String(64), primary_key=True, index=True)
    nazwa = Column(String(128), nullable=False)
    typ = Column(String(64), nullable=False)
    online = Column(Boolean, nullable=False, default=False)
    mqtt_topic = Column(String(128), nullable=True)
    ieee_address = Column(String(32), nullable=True)
    last_state = Column(Text, nullable=True, default="{}")
    updated_at = Column(String(32), nullable=True, default=utc_now_iso)

    measurements = relationship(
        "Measurement", back_populates="device", cascade="all, delete-orphan"
    )
    events = relationship(
        "Event", back_populates="device", cascade="all, delete-orphan"
    )
    access_attempts = relationship(
        "AccessAttempt", back_populates="device", cascade="all, delete-orphan"
    )


class Measurement(Base):
    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(
        String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    metric = Column(String(64), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(16), nullable=False)
    ts = Column(String(32), nullable=False, default=utc_now_iso)

    device = relationship("Device", back_populates="measurements")

    __table_args__ = (
        Index("ix_measurements_dev_metric_ts", "device_id", "metric", "ts"),
        Index("ix_measurements_ts", "ts"),
    )


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(
        String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    typ = Column(String(64), nullable=False)
    payload = Column(Text, nullable=False)
    ts = Column(String(32), nullable=False, default=utc_now_iso)

    device = relationship("Device", back_populates="events")

    __table_args__ = (
        Index("ix_events_type_ts", "typ", "ts"),
        Index("ix_events_dev_ts", "device_id", "ts"),
    )


class AccessAttempt(Base):
    __tablename__ = "access_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    device_id = Column(
        String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    wynik = Column(String(32), nullable=False)
    kod_decyzji = Column(Integer, nullable=False)
    ts = Column(String(32), nullable=False, default=utc_now_iso)

    user = relationship("User", back_populates="access_attempts")
    device = relationship("Device", back_populates="access_attempts")

    __table_args__ = (
        Index("ix_access_user_ts", "user_id", "ts"),
        Index("ix_access_ts", "ts"),
    )


class Automation(Base):
    __tablename__ = "automations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nazwa = Column(String(128), nullable=False, default="Reguła automatyzacji")
    trigger = Column(Text, nullable=False)
    warunek = Column(Text, nullable=True)
    akcja = Column(Text, nullable=False)
    aktywna = Column(Boolean, nullable=False, default=True)