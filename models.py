# SQLAlchemy tables
import datetime
from sqlalchemy import ForeignKey, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=True)
    # Status column
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    # One-to-Many: One client has MANY appointments and services
    appointments: Mapped[list["Appointment"]] = relationship(
        "Appointment",
        back_populates="client",
        cascade="all, delete, delete-orphan") #Automatically deletes appointments when user is deleted
    services: Mapped[list["Service"]] = relationship(
        "Service",
        back_populates="client",
        cascade="all, delete, delete-orphan")

    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        "PasswordResetToken",
        back_populates="client",  # Links directly to the field name in the token model
        cascade="all, delete-orphan",
    )


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    start_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    end_time: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True),)
    guest_token: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)

    # User Foreign Key and many-to-one relationship
    client: Mapped["User"] = relationship("User", back_populates="appointments", lazy="selectin")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Service Foreign Key and many-to-one relationship
    service: Mapped["Service"] = relationship("Service", back_populates="appointments", lazy="selectin")
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))

    # Guest contact details (nullable because registered users don't need them)
    guest_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    guest_email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    guest_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(50))
    duration: Mapped[int] = mapped_column()
    price: Mapped[int] = mapped_column()
    description: Mapped[str | None] = mapped_column(String(150))

    # One-to-Many relationship: One service can be booked in MANY appointments
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="service")

    # User Foreign key and Relationships many-to-one: many services have one user
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    client: Mapped["User"] = relationship("User", back_populates="services")

    # Admin Foreign Key column referencing admin.id and relationship
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin.id"), nullable=False)
    admin: Mapped["Admin"] = relationship("Admin", back_populates="services")


class Admin(Base):
    __tablename__ = "admin"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)  # Needed for login
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)

    # Many-to-one relationship: Link back to Service (matches back_populates="admin")
    services: Mapped[list["Service"]] = relationship(
        "Service",
        back_populates="admin",
        cascade="all, delete-orphan"
    )




class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    # Automatic timestamp indicating exactly when the token record was created
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    # User Foreign key and Many-to-one relationship: many tokens have one client
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    client: Mapped["User"] = relationship("User", back_populates="reset_tokens")