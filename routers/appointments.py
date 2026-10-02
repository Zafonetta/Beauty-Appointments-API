from datetime import timedelta
from typing import Annotated
from fastapi import HTTPException, status, APIRouter, Query
import crud
import schemas
from config import settings
from database import DbSession
from schemas import PaginatedAppointments
from security import CurrentUser, CurrentAdmin

# Initialize the router for appointments
router = APIRouter()

# POST Endpoint: create an appointment
@router.post("", response_model=schemas.AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    appointment: schemas.AppointmentCreate,
    db: DbSession,
):
    # Fetch service to get its duration
    service = await crud.get_service_by_id(db=db, service_id=appointment.service_id)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found"
        )

    # Calculate end time based on service duration (in minutes)
    end_time = appointment.start_time + timedelta(minutes=service.duration)

    # Check for time slot overlap
    if await crud.check_appointment_conflict(
        db=db,
        start_time=appointment.start_time,
        end_time=end_time
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Time slot already booked."
        )
    # Get or create guest user
    user = await crud.get_or_create_guest_user(
            db=db,
            client_name=appointment.client_name,
            client_phone=appointment.client_phone,
            client_email=appointment.client_email,
        )

    # Save and return the new appointment
    return await crud.create_appointment(
        db=db,
        appointment=appointment,
        user_id=user.id,
        end_time=end_time,
    )

# GET /me - Retrieve appointments' details for the client
@router.get("/me", response_model=schemas.PaginatedAppointments)
async def get_my_appointments(
        current_user: CurrentUser,
        db: DbSession,
        skip: int = 0,
        limit: int = 10,):
    result = await crud.get_user_appointments(db=db, user_id=current_user.id, skip=skip, limit=limit)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found")

    appointments, total = result
    return {
        "appointments": appointments,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + len(appointments)) < total,
    }


# GET Endpoint: get guests' appointments
@router.get("/guest/{guest_token}", response_model=schemas.AppointmentClientView)
async def get_guest_appointment(db: DbSession, guest_token: str):
    appointment = await crud.get_appointment_by_token(db=db, token=guest_token)
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    return appointment


# PATCH Endpoint: update appointment by admin
@router.patch("/admin/{appointment_id}", response_model=schemas.AppointmentUpdate)
async def update_appointment_admin(
        db: DbSession,
        appointment_id:int,
        appointment_update:schemas.AppointmentUpdate,
        current_admin: CurrentAdmin,):

    # Fetch an appointment that we want to update
    appointment = await crud.get_appointment_by_id(db=db, appointment_id=appointment_id, admin_id=current_admin.id)
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )

    # Perform update & return fresh record
    updated_appointment = await crud.update_appointment(
        db=db,
        appointment_update=appointment_update,
        appointment=appointment,
    )
    if updated_appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )
    return updated_appointment



# PATCH Endpoint: update appointment by logged-in user
@router.patch("/{appointment_id}", response_model=schemas.AppointmentUpdate)
async def update_appointment_user(
        db: DbSession,
        appointment_id:int,
        appointment_update:schemas.AppointmentUpdate,
        current_user: CurrentUser,
):
    """Allows an authenticated user and admin to update an appointment."""
    # Fetch an appointment that user/admin wants to update
    appointment = await crud.get_user_appointment_by_id(
        db=db, appointment_id=appointment_id, user_id=current_user.id)
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )

    # Perform update & return fresh record
    updated_appointment = await crud.update_appointment(
        db=db,
        appointment_update=appointment_update,
        appointment=appointment,
    )
    # Handle invalid target service
    if updated_appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service with id {appointment_update.service_id} does not exist",
        )
    return updated_appointment


# PATCH Endpoint: update appointment by guest
@router.patch("/guest/{guest_token}", response_model=schemas.AppointmentClientView)
async def update_appointment_guest(
        db: DbSession,
        appointment_update:schemas.AppointmentUpdate,
        guest_token: str,
        ):
    """Allows a guest to update their own appointment using their unique token."""
    # Fetch an appointment by token
    appointment = await crud.get_appointment_by_token(db=db, token=guest_token)
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )

    # Perform update & return fresh record
    updated_appointment = await crud.update_appointment(
        db=db,
        appointment_update=appointment_update,
        appointment=appointment,
    )
    # Handle invalid target service
    if updated_appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service with id {appointment_update.service_id} does not exist",
        )
    return updated_appointment


# GET Endpoint: Retrieves all appointments by admin in a system with pagination and optional user/guest filtering
@router.get("/admin", response_model=PaginatedAppointments)
async def get_appointments_admin(
        db: DbSession,
        current_admin: CurrentAdmin,
        user_id: Annotated[int | None, Query(description="Filter by registered user ID")] = None,
        client_email: Annotated[str | None, Query(description="Filter registered user by email")] = None,
        skip: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = settings.appointments_per_page,
        ):

    appointments, total= await crud.get_all_appointments_admin(
        db=db,
        skip=skip,
        limit=limit,
        user_id=user_id,
        client_email=client_email,
    )
    return schemas.PaginatedAppointments(
        appointments=appointments,
        total=total,
        skip=skip,
        limit=limit,
        has_more=(skip + len(appointments)) < total,
    )


# DELETE Endpoint: delete appointment by logged-in user
@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
        appointment_id: int,
        db: DbSession,
        current_user: CurrentUser,
):
    # fetch the existing appointment from the database
    appointment = await crud.get_user_appointment_by_id(db=db, appointment_id=appointment_id, user_id=current_user.id)
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )

    # Perform delete
    await crud.delete_appointment(db=db, appointment=appointment)
    # HTTP 204 must return None (no body)
    return None


# DELETE Endpoint: delete appointment by guest
@router.delete("/guest/{guest_token}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_guest_appointment(
        db: DbSession,
        guest_token: str,
):
    appointment = await crud.get_appointment_by_token(db=db, token=guest_token)
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )
    await crud.delete_appointment(db=db, appointment=appointment)
    return None


# DELETE Endpoint: delete appointment by admin
@router.delete("/admin/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment_admin(
        appointment_id: int,
        db: DbSession,
        current_admin: CurrentAdmin,
):
    appointment = await crud.get_appointment_by_id(db=db, appointment_id=appointment_id,admin_id=current_admin.id)
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )
    await crud.delete_appointment(db=db, appointment=appointment)
    return None

