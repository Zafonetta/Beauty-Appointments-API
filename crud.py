import uuid
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import delete as sql_delete #alias
import models
import schemas
from security import hash_password


# Helper: Fetch user by case-insensitive username
async def get_user_by_username(db: AsyncSession, username: str) -> models.User | None:
    """Fetch user by username asynchronously (case-insensitive)."""
    result = await db.execute(
        select(models.User)
        .where(func.lower(models.User.username) == username.lower())
    )
    return result.scalars().first()


async def get_admin_by_email(db: AsyncSession, email: str) -> models.Admin | None:
    """Fetch admin by email asynchronously (case-insensitive)."""
    result = await db.execute(
        select(models.Admin)
        .where(func.lower(models.Admin.email) == email.lower())
    )
    return result.scalars().first()

# Helper: Fetch user by case-insensitive email
async def get_user_by_email(db: AsyncSession, email: str) -> models.User | None:
    """Fetch user by email, useful for login and registration checks)."""
    result = await db.execute(
        select(models.User)
        .where(func.lower(models.User.email) == email.lower())
    )
    return result.scalars().first()

#Helper: Fetch user by case-insensitive phone number
async def get_user_by_phone(db: AsyncSession, phone: str) -> models.User | None:
    """Fetch user by phone, useful for login and registration checks."""
    result = await db.execute(
        select(models.User)
        .where(models.User.phone == phone)
    )
    return result.scalars().first()


#Helper: Fetch user by id
async def get_user_by_id(db: AsyncSession, user_id: int) -> models.User | None:
    """Fetch a single user by primary key ID."""
    result = await db.execute(
        select(models.User)
        .where(models.User.id == user_id)
    )
    return result.scalars().first()


async def create_user(db: AsyncSession, user: schemas.UserCreate) -> models.User:
    """Hash the password and save a new user to the database."""
    # 1. Hash the incoming raw password
    hashed_pwd = hash_password(user.password)

    # 2. Extract input data(username, email, phone) excluding the plain-text password
    user_data = user.model_dump(exclude={"password"})
    user_data["email"] = user.email.lower()  # Normalize email in the dictionary
    # 3. Instantiate the SQLAlchemy model
    new_user = models.User(
        **user_data,
        password_hash=hashed_pwd,
    )
    # 4. Save to DB
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


# Fetch a paginated list of all users
async def get_users(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 10,
        q: str | None = None,
        is_active: bool | None = None,
) -> tuple[list[models.User], int]:
    # Base Select & Count Statements
    query = select(models.User) #query to fetch the actual user records
    count_query = select(func.count(models.User.id)).select_from(models.User) #to compute the total number of matching rows in database

    # Build Dynamic Filters
    filters = []

    if q:
        search_pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                models.User.username.ilike(search_pattern),
                models.User.email.ilike(search_pattern),
                models.User.phone.ilike(search_pattern),
            )
        )

    if is_active is not None:
        filters.append(models.User.is_active == is_active) #passing False filters for deactivated accounts

    # Apply Filters to Both Queries
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    #  Fetch Filtered Total Count
    total = await db.scalar(count_query) or 0

    # Paginated users query (using the filtered 'query' variable)
    query = query.order_by(models.User.id.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    users = list(result.scalars().all())

    return users, total


# Fetch paginated appointments for a specific user
async def get_user_appointments(
        db: AsyncSession,
        user_id: int,
        skip: int = 0,
        limit: int = 10) -> tuple[list[models.Appointment], int]| None:

    # Verify user existence
    user_exists = await db.scalar(
        select(models.User.id).where(models.User.id == user_id)
    )
    if not user_exists:
        return None  # Return None so the router can raise 404

    # Get total appointment count for this user
    total = (
            await db.scalar(
        select(func.count())
        .select_from(models.Appointment)
        .where(models.Appointment.user_id == user_id)) or 0
    )

  # Fetch paginated appointments and sort them here
    result = await db.execute(
        select(models.Appointment)
        .where(models.Appointment.user_id == user_id) # Find all appointments of this user
        .options(selectinload(models.Appointment.service)
        ) # Eagerly load service data
        .order_by(models.Appointment.start_time.desc())
        .offset(skip)#Hop over the appointment the frontend has already seen
        .limit(limit)
    )
    appointments = list(result.scalars().all())

    return appointments, total

# Fetches paginated appointments across all clients and guests
async def get_all_appointments_admin(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 10,
    user_id: int | None = None,
    client_email: str | None = None, # Single parameter for guests AND users
) -> tuple[list[models.Appointment], int]:

    # Build filter conditions
    conditions = []
    needs_user_join = False

    if user_id is not None:
        conditions.append(models.Appointment.user_id == user_id)
    if client_email is not None:
        needs_user_join = True
        conditions.append(
            or_(
                models.User.email.ilike(f"%{client_email}%"),
                models.Appointment.guest_email.ilike(f"%{client_email}%"),
            )
        )
    # Build count query
    count_stmt = select(func.count()).select_from(models.Appointment)
    if needs_user_join:
        count_stmt = count_stmt.outerjoin(models.Appointment.client) # Use outerjoin so guests aren't excluded
    if conditions:
        count_stmt = count_stmt.where(*conditions)

    total = await db.scalar(count_stmt) or 0

    # Fetch paginated appointments sorted by newest start time
    stmt = (
        select(models.Appointment)
        .options(
            selectinload(models.Appointment.service),
            selectinload(models.Appointment.client),
        )
        .order_by(models.Appointment.start_time.desc())
        .offset(skip)
        .limit(limit)
    )

    if needs_user_join:
        stmt = stmt.outerjoin(models.Appointment.client)
    if conditions:
        stmt = stmt.where(*conditions)

    result = await db.execute(stmt)
    appointments = list(result.scalars().unique().all())

    return appointments, total


async def get_user_services(
        db: AsyncSession,
        user_id: int,
        skip: int = 0,
        limit: int = 10) -> tuple[list[models.Service], int] | None:
    """Fetch paginated services for a specific user."""

    # Verify user existence
    user_exists = await db.scalar(
        select(models.User.id).where(models.User.id == user_id)
    )
    if not user_exists:
        return None  # Return None so the router can raise 404

    # Get total service count for this user
    total = await db.scalar(
        select(func.count())
        .select_from(models.Service)
        .where(models.Service.user_id == user_id)
    ) or 0

# Fetch paginated services and sort them here
    result = await db.execute(
        select(models.Service)
        .where(models.Service.user_id == user_id) # Find all services of this user
        .order_by(models.Service.price.desc())
        .offset(skip)#Hop over the service the frontend has already seen
        .limit(limit),
    )
    services = list(result.scalars().all())

    return services, total



#Partially update a user record in the database
async def partial_user_update(
        db: AsyncSession,
        user: models.User,
        user_update: schemas.UserUpdate,
        ) -> models.User:
    """Applies passed updates, commits transaction, and returns updated instance."""
    # Fetch the existing user (reusing our existing get_user_by_id helper)
    update_data = user_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user

async def delete_profile(db: AsyncSession,  user: models.User) -> None:
    """Delete an existing user profile."""
    await db.delete(user)
    await db.commit()


async def delete_existing_token(db: AsyncSession, user_id: int) -> None:
    """Delete an existing user token."""
    await db.execute(
        sql_delete(models.PasswordResetToken)
        .where(models.PasswordResetToken.user_id == user_id)
    )


async def create_reset_token(
    db: AsyncSession, user_id: int, token_hash: str, expires_at: datetime
) -> models.PasswordResetToken:
    """Create a new password reset token record."""
    reset_token = models.PasswordResetToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(reset_token)
    await db.commit()
    await db.refresh(reset_token)
    return reset_token

async def get_reset_token(db: AsyncSession, token_hash: str) -> models.PasswordResetToken:
    """Fetch token record by hash without side effects."""
    result = await db.execute(
        select(models.PasswordResetToken)
        .where(models.PasswordResetToken.token_hash == token_hash)
    )
    return result.scalars().first()


async def delete_token(db: AsyncSession, reset_token: models.PasswordResetToken) -> None:
    """Delete a specific token instance."""
    await db.delete(reset_token)


async def get_services(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 10
) -> tuple[list[models.Service], int]:
    """Fetch paginated list of all services."""
    # Total services count
    total = (
            await db.scalar(select(func.count()).select_from(models.Service)) or 0
    )

    # Paginated services query
    result = await db.execute(
        select(models.Service)
        .order_by(models.Service.price.asc())
        .offset(skip)
        .limit(limit)
    )
    services = list(result.scalars().all())

    return services, total

# helper function
async def get_service_by_id(db: AsyncSession, service_id: int) -> models.Service | None:
    result = await db.execute(
        select(models.Service)
        .where(models.Service.id == service_id)
        .options(selectinload(models.Service.client))
    )
    return result.scalars().first()


# Partially update a user record in the database
async def partial_service_update(
        db: AsyncSession,
        service: models.Service,
        service_update: schemas.ServiceUpdate,
        ) -> models.Service:
    """Applies passed updates, commits transaction, and returns updated instance."""
    # Fetch the existing services (reusing our existing get_user_by_id helper)
    update_data = service_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(service, field, value)

    await db.commit()
    await db.refresh(service, attribute_names=["admin"])
    return service

# Delete service
async def delete_service(db: AsyncSession, service: models.Service) -> None:
    """Delete an existing service instance."""
    await db.delete(service)
    await db.commit()


async def check_appointment_conflict(
        db: AsyncSession, start_time: datetime, end_time: datetime
) -> bool:
    result = await db.execute(
        select(models.Appointment)
        .where(and_(
                models.Appointment.start_time < end_time,
                models.Appointment.end_time > start_time,)
        )
    )
    existing_appointment = result.scalar_one_or_none()

    return existing_appointment is not None


# Helper function
async def get_available_slots_for_date(
    db: AsyncSession,
    booking_date: datetime.date,
    duration_minutes: int,
    slot_interval_minutes: int = 120,  # Offers slots every 2 hours
) -> list[datetime]:
    # Define working hours for the day (10:00 AM to 7:00 PM)
    work_start = datetime.combine(booking_date, time(10, 0),tzinfo=ZoneInfo("Europe/Rome"))
    work_end = datetime.combine(booking_date, time(19, 0),tzinfo=ZoneInfo("Europe/Rome"))

    # Fetch all existing appointments for that specific date
    query = select(models.Appointment).where(
        and_(
            models.Appointment.start_time >= work_start,
            models.Appointment.start_time < work_end,
        )
    )
    result = await db.execute(query)
    existing_appointments = result.scalars().all()

    # Generate candidate slots and check for conflicts
    available_slots = []
    # pointer that continuously shifts forward after each check (e.g., 10:00 AM →12:00 PM →2:00 PM)
    current_slot_start = work_start

    while current_slot_start + timedelta(minutes=duration_minutes) <= work_end:
        current_slot_end = current_slot_start + timedelta(minutes=duration_minutes)

        # Overlap check: slot overlaps if existing_start < new_end AND existing_end > new_start
        has_conflict = any(
            appt.start_time < current_slot_end and appt.end_time > current_slot_start
            for appt in existing_appointments
        )

        if not has_conflict:
            available_slots.append(current_slot_start)

        # Move forward by the interval step (e.g., check 10:00, 12:00, 14:00...)
        current_slot_start += timedelta(minutes=slot_interval_minutes)

    return available_slots


async def get_or_create_guest_user(
    db: AsyncSession,
    client_name: str,
    client_phone: str,
    client_email: str | None = None
) -> models.User:
    """Check if a client with given number/email exists and return it. If not, create it."""
    clean_phone = client_phone.strip()
    clean_email = client_email.strip() if client_email else None

    # Search by phone OR email to prevent duplicate key violations
    conditions = [models.User.phone == clean_phone]
    if clean_email:
        conditions.append(models.User.email == clean_email)

    result = await db.execute(
        select(models.User).where(or_(*conditions))
    )
    user = result.scalars().first()

    # Create user if neither phone nor email was found
    if not user:
        effective_email = clean_email

        user = models.User(
            username=client_name.strip(),
            phone=clean_phone,
            email=effective_email,
            password_hash="GUEST_NO_PASSWORD",  # Satisfies NOT NULL constraint
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user

async def create_appointment(
        db:AsyncSession,
        appointment: schemas.AppointmentCreate,
        end_time:datetime,
        user_id: int,) -> models.Appointment:
    # Pass only the database columns for the appointment
    new_appointment = models.Appointment(
        service_id=appointment.service_id,
        start_time=appointment.start_time,
        end_time=end_time,
        user_id=user_id,
        guest_token=str(uuid.uuid4()),  # Automatically generates a unique token
    )

    db.add(new_appointment)
    await db.commit()
    await db.refresh(new_appointment)
    return new_appointment

async def get_appointment_by_token(
        db: AsyncSession,
        token: str,
)-> models.Appointment | None:
    result = await db.execute(
        select(models.Appointment)
        .where(models.Appointment.guest_token == token)
        .options(
            selectinload(models.Appointment.service),
            selectinload(models.Appointment.client),
        )
    )
    return result.scalar_one_or_none()

async def get_user_appointment_by_id(db: AsyncSession, appointment_id: int, user_id:int,) -> models.Appointment | None:
    result = await db.execute(
        select(models.Appointment)
        .where(
            models.Appointment.id == appointment_id,
            models.Appointment.user_id == user_id,)
    )
    return result.scalars().first()

# Delete an existing appointment instance
async def delete_appointment(db: AsyncSession, appointment:models.Appointment) -> None:
    await db.delete(appointment)
    await db.commit()

# Fetch an appointment only if it belongs to a service owned by admin_id
async def get_appointment_by_id(db: AsyncSession, appointment_id: int, admin_id:int) -> models.Appointment | None:
    result = await db.execute(
        select(models.Appointment)
        .join(models.Appointment.service)  #  Explicit relationship join
        .where(
            models.Appointment.id == appointment_id,
            models.Service.admin_id == admin_id,  # Scopes query to current admin's salon
        )
    )
    return result.scalars().first()

# appointment update by admin
async def appointment_update_by_admin(
        db: AsyncSession,
        appointment: models.Appointment,
        appointment_update: schemas.AppointmentUpdate,
        ) -> models.Appointment | None:

    update_data = appointment_update.model_dump(exclude_unset=True)

    #  Validate service existence if service_id is being updated
    if "service_id" in update_data and update_data["service_id"] is not None:
        target_service = await db.get(models.Service, update_data["service_id"])
        if not target_service:
            return None
    # Update basic fields
    for field, value in update_data.items():
        setattr(appointment, field, value)

        # Recalculate end_time if start_time or service changed
        if "start_time" in update_data or "service_id" in update_data:
            # Fetch service to get duration
            service = await db.get(models.Service, appointment.service_id)
            if service:
                appointment.end_time = appointment.start_time + timedelta(minutes=service.duration)

    await db.commit()
    await db.refresh(appointment, attribute_names=["service"])
    return appointment

# appointment update by user
async def appointment_update_by_user(
        db: AsyncSession,
        appointment: models.Appointment,
        appointment_update: schemas.AppointmentUpdate,
        ) -> models.Appointment | None:

    update_data = appointment_update.model_dump(exclude_unset=True)

    #  Validate service existence if service_id is being updated
    if "service_id" in update_data and update_data["service_id"] is not None:
        target_service = await db.get(models.Service, update_data["service_id"])
        if not target_service:
            return None
    # Update basic fields
    for field, value in update_data.items():
        setattr(appointment, field, value)

        # Recalculate end_time if start_time or service changed
        if "start_time" in update_data or "service_id" in update_data:
            # Fetch service to get duration
            service = await db.get(models.Service, appointment.service_id)
            if service:
                appointment.end_time = appointment.start_time + timedelta(minutes=service.duration)

    await db.commit()
    await db.refresh(appointment, attribute_names=["service"])
    return appointment

