import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import user, current_user
import crud
import models
import schemas
from routers import appointments
from security import create_access_token

from fastapi import status, HTTPException

from tests.conftest import guest_client


# testing POST Endpoint: create an appointment
@pytest.mark.asyncio
async def test_create_appointment_success(client: AsyncClient, test_service: models.Service):
    """Test successful booking creation for a valid service and time slot."""
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {
        "service_id": test_service.id,
        "start_time": booking_time.isoformat(),
        "client_name": "Elena Rossi",
        "client_phone": "+393123456789",
        "client_email": "elena@example.com",
    }

    response = await client.post("/api/appointments", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["service_id"] == test_service.id
    assert "id" in data
    assert "end_time" in data

# Unit test: create an appointment with success
@pytest.mark.asyncio
async def test_create_appointment(db_session, test_service, test_user):
    # Create the input schema payload expected by the endpoint
    new_appointment = schemas.AppointmentCreate(
        service_id=test_service.id,
        start_time=datetime.now(timezone.utc),
        client_name="Elena Rossi",
        client_phone="+393123456709",
        client_email="elena@example.com"
    )

    # Call the router function
    created_appointment = await appointments.create_appointment(
        db=db_session,
        appointment=new_appointment,
    )
    assert created_appointment is not None
    assert created_appointment.user_id is not None


# Unit test: create an appointment with unexisted service
@pytest.mark.asyncio
async def test_create_appointment_unexisted_service(db_session, test_service, test_user):
    # Create the input schema payload expected by the endpoint
    new_appointment = schemas.AppointmentCreate(
        service_id=99999, # Non-existent ID
        start_time=datetime.now(timezone.utc),
        client_name="Elena Rossi",
        client_phone="+393123456709",
        client_email="elena@example.com"
    )

    # Expect an HTTPException to be raised
    with pytest.raises(HTTPException) as exc_info:
        await appointments.create_appointment(
            db=db_session,
            appointment=new_appointment,
        )

    # Verify the exception details
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Service not found"


# Unit test: create an appointment with conflict
@pytest.mark.asyncio
async def test_create_appointment_conflict(db_session, test_service, guest_client):
    # Grab the existing appointment from the guest_client fixture
    existed_appointment = guest_client.appointment

    # Build a new payload reusing the exact same start_time
    new_appointment = schemas.AppointmentCreate(
        service_id=test_service.id,
        start_time=existed_appointment.start_time,
        client_name="Elena Rossi",
        client_phone="+393123456709",
        client_email="elena@example.com"
    )

    # Expect an HTTPException to be raised
    with pytest.raises(HTTPException) as exc_info:
        await appointments.create_appointment(
            db=db_session,
            appointment=new_appointment,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Time slot already booked."



# Unit test
@pytest.mark.asyncio
async def test_crud_check_appointment_conflict(db_session: AsyncSession, test_service: models.Service, test_user):
    # Establish a single base time to prevent microsecond drift
    now = datetime.now(timezone.utc)

    # Define time slot 1
    start_time_first = now + timedelta(hours=1)
    end_time_first = start_time_first + timedelta(minutes=test_service.duration)

    # Test FREE slot (Database has no appointments yet)
    has_conflict = await crud.check_appointment_conflict(
        db=db_session,
        start_time=start_time_first,
        end_time=end_time_first,
    )
    assert has_conflict is False

    # Save an actual appointment in the database to test conflicts against
    appointment = models.Appointment(
        user_id=test_user.id,
        service_id=test_service.id,
        start_time=start_time_first,
        end_time=end_time_first,
    )
    db_session.add(appointment)
    await db_session.commit()

    # Define an overlapping slot and test for CONFLICT (should return True)
    start_time_overlapping = start_time_first + timedelta(minutes=15)
    end_time_overlapping = start_time_overlapping + timedelta(minutes=test_service.duration)

    has_conflict_true = await crud.check_appointment_conflict(
        db=db_session,
        start_time=start_time_overlapping,
        end_time=end_time_overlapping,
    )
    assert has_conflict_true is True


# Unit test: check if a client with given number/email exists and return it. If not, create it
@pytest.mark.asyncio
async def test_crud_get_or_create_guest_user(test_user: models.User, db_session: AsyncSession):
    # RETRIEVE EXISTING USER (matches by existing phone/email in test_user)
    existed_user = await crud.get_or_create_guest_user(
        db=db_session,
        client_name=test_user.username,
        client_phone=test_user.phone,
        client_email=test_user.email,
    )
    assert existed_user.id == test_user.id
    assert existed_user.email == test_user.email

    # CREATE NEW GUEST (must use a UNIQUE phone/email not in DB)
    guest = await crud.get_or_create_guest_user(
        db=db_session,
        client_name="Fenix Guest",
        client_phone="300000000",
        client_email="fenix@example.com"
    )
    assert guest.id is not None
    assert guest.id != test_user.id
    assert guest.email == "fenix@example.com"
    assert guest.phone == "300000000"


# 404 error when attempting to book a non-existent service
@pytest.mark.asyncio
async def test_create_appointment_service_not_found(client: AsyncClient):
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {
        "service_id": 99999,  # Non-existent ID
        "start_time": booking_time.isoformat(),
        "client_name": "Elena Rossi",
        "client_phone": "+393123456789",
        "client_email": "elena@example.com",
    }

    response = await client.post("/api/appointments", json=payload)

    assert response.status_code == 404
    assert response.json()["detail"] == "Service not found"


# 409 Conflict when attempting to book an overlapping time slot
@pytest.mark.asyncio
async def test_create_appointment_time_slot_conflict(
    client: AsyncClient, test_service: models.Service
):
    base_time = datetime.now(timezone.utc) + timedelta(days=2)
    start_time = base_time.replace(hour=14, minute=0, second=0, microsecond=0)

    first_booking = {
        "service_id": test_service.id,
        "start_time": start_time.isoformat(),
        "client_name": "First Client",
        "client_phone": "+393123456789",
        "client_email": "first@example.com",
    }

    # First booking succeeds
    res1 = await client.post("/api/appointments", json=first_booking)
    assert res1.status_code == 201

    # Second booking overlaps (starts 30 mins into the 60-min service)
    overlapping_booking = {
        "service_id": test_service.id,
        "start_time": (start_time + timedelta(minutes=30)).isoformat(),
        "client_name": "Second Client",
        "client_phone": "+393987654321",
        "client_email": "second@example.com",
    }

    res2 = await client.post("/api/appointments", json=overlapping_booking)

    assert res2.status_code == 409
    assert res2.json()["detail"] == "Time slot already booked."


# 422 Unprocessable Content when schema validators fail
@pytest.mark.asyncio
async def test_create_appointment_validation_error(
    client: AsyncClient, test_service: models.Service
):
    payload = {
        "service_id": test_service.id,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "client_name": "   ",  # Fails field_validator (only whitespace)
        "client_phone": "123",  # Fails min_length constraint (< 10 chars)
        "client_email": "second@example.", # Incorrect email
    }

    response = await client.post("/api/appointments", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT



# testing GET /me Endpoint - Retrieve appointments' details for the client
@pytest.mark.anyio
async def test_get_my_appointments_with_pagination(
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_service: models.Service):

    # Fetch the user created inside auth_client fixture
    test_user = await crud.get_user_by_email(db_session, email="test@example.com")
    assert test_user is not None

    # Seed 5 appointments directly linked to test_user.id
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    for i in range(5):
        slot_time = booking_time + timedelta(hours=i * 2)  # Spaced out by 2 hours
        await crud.create_appointment(
            db=db_session,
            appointment=schemas.AppointmentCreate(
                service_id=test_service.id,
                start_time=slot_time,
                client_name="Test",
                client_phone="+393123456789",
                client_email="test@example.com",),
            end_time=slot_time + timedelta(minutes=test_service.duration),
            user_id=test_user.id,  # Directly links to auth_client's account
        )
        await db_session.commit()

    # Fetch all appointments (Default skip=0, limit=10)
    # We expect to see all 5 appointments returned at once
    response = await auth_client.get("/api/appointments/me")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 5
    assert data["has_more"] is False

    # Fetch first page (Pagination)
    response = await auth_client.get("/api/appointments/me?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 2
    assert data["has_more"] is True

    # Fetch with skip and limit, middle page (complex pagination)
    response = await auth_client.get("/api/appointments/me?skip=2&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 2
    assert data["skip"] == 2
    assert data["limit"] == 2


# Unit test: get appointment of unexisted user
@pytest.mark.anyio
async def test_get_my_appointments_unexisted(db_session: AsyncSession,):
    #  Create a dummy user object with an invalid ID in memory (not saved to DB)
    dummy_user = SimpleNamespace(id=99999)
    with pytest.raises(HTTPException) as exc_info:
        await appointments.get_my_appointments(
        current_user=dummy_user,
        db=db_session,
    )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "User not found"


# Unit test: get appointments with success
@pytest.mark.anyio
async def test_get_my_appointments_success(db_session: AsyncSession, test_user):

    result = await appointments.get_my_appointments(
        db=db_session,
        current_user=test_user,
    )
    assert "appointments" in result
    assert "total" in result
    assert result["skip"] == 0
    assert result["limit"] == 10
    assert result["has_more"] is False


# Direct DB/CRUD unit test: retrieve appointments' details for non-existed user
@pytest.mark.anyio
async def test_get_my_appointments_user_not_found(db_session: AsyncSession):
    result = await crud.get_user_appointments(db_session, user_id=99999)
    assert result is None

# Direct DB/CRUD unit test
@pytest.mark.anyio
async def test_get_user_appointments_crud_success(db_session: AsyncSession, auth_client: AsyncClient):
    # fetch authenticated client
    user = await crud.get_user_by_email(db_session, "test@example.com")
    assert user is not None

    # Pass an existing user_id directly to crud.get_user_appointments
    result = await crud.get_user_appointments(
        db=db_session,
        user_id=user.id,
        skip=0,
        limit=10
    )

    assert result is not None
    appointments, total = result
    assert isinstance(appointments, list)
    assert isinstance(total, int)

# empty state
@pytest.mark.anyio
async def test_get_my_appointments_empty(auth_client: AsyncClient):
    response = await auth_client.get("/api/appointments/me")
    assert response.status_code == 200
    data = response.json()

    # Verify the "empty state" of our application.
    assert data["appointments"] == []  # Verify the list is empty
    assert data["total"] == 0  # Verify the count is zero
    assert data["has_more"] is False  # Verify pagination is inactive


# Input validation (negative skip / oversized limit)
@pytest.mark.anyio
async def test_get_my_appointments_failed(auth_client: AsyncClient):
    negative_skip =  await auth_client.get("/api/services?skip=-2")
    assert negative_skip.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    oversized_limit = await auth_client.get("/api/services?limit=999")
    assert oversized_limit.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# testing GET Endpoint: get guests' appointments
@pytest.mark.anyio
async def test_get_guest_appointments(
        client: AsyncClient,
        test_service: models.Service
):
    start_time = datetime.now(timezone.utc) + timedelta(days=1)
    end_time = start_time + timedelta (minutes = test_service.duration)

    # Create an appointment by guest
    payload = {
        "service_id": test_service.id,
        "start_time": start_time.isoformat(),
        "client_name": "Guest",
        "client_phone": "+393123456789",
        "client_email": "guest@example.com",
    }

    response = await client.post("/api/appointments", json=payload)

    assert response.status_code == 201

    # Extract values from the POST response JSON payload
    created_data = response.json()
    guest_token = created_data["guest_token"]

    # Call the public endpoint (using unauthenticated client)
    response = await client.get(f"/api/appointments/guest/{guest_token}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created_data["id"]
    assert data["service"]["name"] == test_service.name
    assert data["service"]["price"] == test_service.price

    # Verify privacy: sensitive database keys should not be exposed
    assert "user_id" not in data
    assert "admin_id" not in data["service"]


# Unit test:get unexisted appointment
@pytest.mark.anyio
async def test_get_guest_appointments_unexisted(db_session: AsyncSession,):
    # Pass unexisted token
    with pytest.raises(HTTPException) as exc_info:
        await appointments.get_guest_appointment(
            db=db_session,
            guest_token="wrong_token",
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Appointment not found"


# Unit test: success
@pytest.mark.anyio
async def test_get_guest_appointments_success(db_session: AsyncSession, guest_client: AsyncClient):
    # Extract token from our guest_client
    token = guest_client.appointment.guest_token
    result = await appointments.get_guest_appointment(
        db=db_session,
        guest_token=token,
    )
    assert result.id == guest_client.appointment.id
    assert result.guest_token == token

# appointment not found
@pytest.mark.anyio
async def test_get_guest_appointment_not_found(client: AsyncClient):
    """Verify requesting a non-existent guest token returns 404."""
    response = await client.get("/api/appointments/guest/non_existent_token_123")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"


# Input validation (negative skip / oversized limit)
@pytest.mark.anyio
async def test_get_my_appointments_guest_failed(guest_client: AsyncClient):
    negative_skip =  await guest_client.get("/api/services?skip=-2")
    assert negative_skip.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    oversized_limit = await guest_client.get("/api/services?limit=999")
    assert oversized_limit.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# get appointment by token
@pytest.mark.anyio
async def test_get_appointment_by_token(guest_client: AsyncClient):
    # Retrieve the token attached by the fixture
    token = guest_client.guest_token

    # Make the HTTP request to your API router endpoint
    response = await guest_client.get(f"/api/appointments/guest/{token}")

    assert response.status_code == 200
    data = response.json()

    # Assert fields defined in AppointmentClientView
    assert data["id"] == guest_client.appointment.id
    assert "start_time" in data
    assert "service" in data


# Unit test: crud get appointment by token
@pytest.mark.anyio
async def test_crud_get_appointment_by_token(guest_client: AsyncClient, db_session: AsyncSession):
    token = guest_client.guest_token

    appointment = await crud.get_appointment_by_token(db=db_session, token=token)
    assert appointment is not None
    assert appointment.guest_token == token
    assert appointment.guest_name == guest_client.appointment.guest_name
    assert appointment.service.id == guest_client.appointment.service_id

# Unit test: invalid token return None
@pytest.mark.anyio
async def test_crud_get_appointment_invalid_token(guest_client: AsyncClient, db_session: AsyncSession):
    appointment = await crud.get_appointment_by_token(db=db_session, token="invalid")
    assert appointment is None

# Unit test: crud get user appointment by id
@pytest.mark.anyio
async def test_crud_get_user_appointment_by_id(guest_client: AsyncClient, db_session):
    appointment_id = guest_client.appointment.id
    user_id = guest_client.appointment.user_id

    appointment = await crud.get_user_appointment_by_id(
        db=db_session,
        appointment_id=appointment_id,
        user_id=user_id)

    assert appointment is not None
    assert appointment.id == appointment_id
    assert appointment.user_id == user_id

# Unit test: crud get appointment by id
@pytest.mark.anyio
async def test_crud_get_appointment_by_id(guest_client: AsyncClient, db_session, test_service):
    appointment_id = guest_client.appointment.id
    admin_id = test_service.admin_id

    appointment = await crud.get_appointment_by_id(db_session, appointment_id, admin_id)

    assert appointment is not None
    assert appointment.id == appointment_id
    assert appointment.service_id == test_service.id


# testing PATCH Endpoint: update appointment by admin
@pytest.mark.anyio
async def test_update_appointment_success_admin(
        admin_client: AsyncClient,
        client: AsyncClient,
        test_service: models.Service):
    """Verify that an admin can successfully update appointment's date and start time."""
    # Create initial booking
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/appointments",
        json={
        "service_id": test_service.id,
        "start_time": booking_time.isoformat(),
        "client_name": "Elena Rossi",
        "client_phone": "+393123456789",
        "client_email": "elena@example.com",
        }
    )
    assert response.status_code == 201
    appointment_id = response.json()["id"]

    # Define a valid new datetime (e.g., +2 hours)
    new_start_time = booking_time + timedelta(hours=2)

    response = await admin_client.patch(
        f"/api/appointments/admin/{appointment_id}",
        json={"start_time": new_start_time.isoformat()},

    )
    assert response.status_code == 200
    data = response.json()
    assert data["start_time"] == new_start_time.isoformat().replace("+00:00", "Z")


# Unit test: update appointment success
@pytest.mark.anyio
async def test_update_appointment_admin_success(
        db_session: AsyncSession,
        guest_client: AsyncClient,
        admin_user: models.Admin,):
    # Fetch existed appointment from our guest_client fixture
    appointment = guest_client.appointment

    # Update a start time of appointment
    updated_appointment = schemas.AppointmentUpdate(
        start_time= datetime.now(timezone.utc),
        service_id=appointment.service_id,
    )
    result = await appointments.update_appointment_admin(
        db=db_session,
        appointment_id= appointment.id,
        appointment_update=updated_appointment,
        current_admin=admin_user,
    )

    assert result is not None
    assert result.id == appointment.id


# Unit test: appointment not found
@pytest.mark.anyio
async def test_update_appointment_not_found(db_session, admin_user):

    updated_appointment = schemas.AppointmentUpdate()

    with pytest.raises(HTTPException) as exc_info:
        await appointments.update_appointment_admin(
        db=db_session,
        appointment_id=9999,
        appointment_update=updated_appointment,
        current_admin=admin_user,
    )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Appointment not found"

# Unit test: appointment with service id that doesn't exist
@pytest.mark.anyio
async def test_update_appointment_service_not_found(
        db_session: AsyncSession,
        guest_client: AsyncClient,
        admin_user: models.Admin,):

    # Fetch existed appointment from our guest_client fixture
    appointment = guest_client.appointment

    # Update an appointment with unexisted service id
    updated_appointment = schemas.AppointmentUpdate(
        start_time= datetime.now(timezone.utc),
        service_id=9999,
    )
    with pytest.raises(HTTPException) as exc_info:
        await appointments.update_appointment_admin(
        db=db_session,
        appointment_id= appointment.id,
        appointment_update=updated_appointment,
        current_admin=admin_user,
    )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Service not found"


# Updating an appointment with invalid service
@pytest.mark.anyio
async def test_update_appointment_admin_failed(admin_client: AsyncClient, client: AsyncClient, test_service: models.Service):
    # Create initial booking with valid service id
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "Elena Rossi",
            "client_phone": "+393123456789",
            "client_email": "elena@example.com",
        }
    )
    appointment_id = response.json()["id"]

    # Admin try to update invalid service ID
    invalid_service_id = 9999
    response = await admin_client.patch(
        f"/api/appointments/admin/{appointment_id}",
        json={"service_id": invalid_service_id},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Service not found"


# Unauthorized / non-admin access
@pytest.mark.anyio
async def test_update_appointment_unauthorized(client: AsyncClient, test_service: models.Service, other_auth_client: AsyncClient):
    # Create initial booking
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "Elena Rossi",
            "client_phone": "+393123456789",
            "client_email": "elena@example.com",
        }
    )
    appointment_id = response.json()["id"]

    response = await other_auth_client.patch(
        f"/api/appointments/admin/{appointment_id}",
        json={"start_time": datetime.now(timezone.utc).isoformat()},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN

# invalid payload
@pytest.mark.anyio
async def test_update_appointment_admin_invalid(
        admin_client: AsyncClient,
        client: AsyncClient,
        test_service: models.Service,
):
    """Verify sending invalid data types triggers Pydantic 422 validation error."""
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "Elena Rossi",
            "client_phone": "+393123456789",
            "client_email": "elena@example.com",
        }
    )
    appointment_id = response.json()["id"]

    response = await admin_client.patch(
        f"/api/appointments/admin/{appointment_id}",
        json={"start_time": "invalid - datetime - format"},

    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# Unit test: crud appointment_update_by_admin
@pytest.mark.anyio
async def test_crud_appointment_update(db_session: AsyncSession, guest_client: AsyncClient,):
    appointment = guest_client.appointment
    new_start_time = datetime.now(timezone.utc)

    appointment_update=schemas.AppointmentUpdate(
        start_time=new_start_time,
        service_id=appointment.service_id,
    )
    updated_appointment = await crud.update_appointment(
        db=db_session,
        appointment=appointment,
        appointment_update=appointment_update,)

    assert appointment is not None
    assert updated_appointment.id == appointment.id
    assert updated_appointment.start_time == new_start_time
    assert updated_appointment.service_id == appointment.service_id

    # unexisted service_id
    appointment_update_invalid = schemas.AppointmentUpdate(
        start_time=new_start_time,
        service_id=99999,
    )
    unexisted_service = await crud.update_appointment(
        db=db_session,
        appointment=appointment,
        appointment_update=appointment_update_invalid,
    )
    assert unexisted_service is None



# testing PATCH Endpoint: update appointment by logged-in user
@pytest.mark.anyio
async def test_update_appointment_success_user(
        auth_client: AsyncClient,
        test_service: models.Service):
    """Verify that a logged-in user can successfully update their appointment's start time."""
    # Create initial booking (strip microseconds for clean ISO string matching)
    booking_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    response = await auth_client.post(
        "/api/appointments",
        json={
        "service_id": test_service.id,
        "start_time": booking_time.isoformat(),
        "client_name": "testuser",
        "client_phone": "0123456789",
        "client_email": "test@example.com",
        }
    )
    assert response.status_code == 201
    appointment_id = response.json()["id"]

    # Define a valid new datetime (e.g., +2 hours)
    new_start_time = booking_time + timedelta(hours=2)

    response = await auth_client.patch(
        f"/api/appointments/{appointment_id}",
        json={"start_time": new_start_time.isoformat()},

    )
    assert response.status_code == 200
    data = response.json()

    assert data["start_time"] == new_start_time.isoformat().replace("+00:00", "Z")


# Unit test: update appointment by user - success
@pytest.mark.anyio
async def test_update_appointment_user_success(
        db_session: AsyncSession,
        test_user: models.User,
        test_service: models.Service
):
    # test_user create an appointment
    appointment = models.Appointment(
        service_id=test_service.id,
        user_id=test_user.id,
        start_time=datetime.now(timezone.utc),
        guest_name=test_user.username,
        guest_phone=test_user.phone,
        guest_email=test_user.email,
        end_time=datetime.now(timezone.utc),
    )
    db_session.add(appointment)
    await db_session.commit()
    await db_session.refresh(appointment)

    # update an appointment
    updated_appointment = schemas.AppointmentUpdate(
        start_time=datetime.now(timezone.utc)+ timedelta(days=1),
        service_id=test_service.id,
    )

    result = await appointments.update_appointment_user(
        db=db_session,
        appointment_id=appointment.id,
        appointment_update=updated_appointment,
        current_user=test_user,
    )

    assert result is not None
    assert result.id == appointment.id


# Unit test: update unexisted appointment
@pytest.mark.anyio
async def test_update_appointment_user_failure(
        db_session: AsyncSession,
        test_user: models.User,
):
    # update an appointment
    updated_appointment = schemas.AppointmentUpdate()

    with pytest.raises(HTTPException) as exc_info:
     await appointments.update_appointment_user(
        db=db_session,
        appointment_id=99999,
        appointment_update=updated_appointment,
        current_user=test_user,
    )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Appointment not found"


# Unit test:update appointment with non-existent service ID
@pytest.mark.anyio
async def test_update_appointment_user_invalid_service(
        db_session: AsyncSession,
        guest_client):

    #  Match current_user.id to the appointment's actual owner
    owner = SimpleNamespace(id=guest_client.appointment.user_id)

    updated_appointment = schemas.AppointmentUpdate(service_id=99999)

    with pytest.raises(HTTPException) as exc_info:
        await appointments.update_appointment_user(
            db=db_session,
            appointment_id=guest_client.appointment.id,
            appointment_update=updated_appointment,
            current_user=owner,
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == f"Service with id {updated_appointment.service_id} does not exist"


# Forbidden: User A can't update Users' B appointment
@pytest.mark.anyio
async def test_update_appointment_user_forbidden(
        auth_client: AsyncClient,
        other_auth_client: AsyncClient,
        test_service: models.Service,
):
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await auth_client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "testuser",
            "client_phone": "0123456789",
            "client_email": "test@example.com",
        }
    )
    appointment_id = response.json()["id"]

    response = await other_auth_client.patch(
        f"/api/appointments/{appointment_id}",
        json={"start_time": booking_time.isoformat()},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND

# Unauthorized: Guest can't update users' appointment
@pytest.mark.anyio
async def test_update_appointment_user_unauthorized(
        guest_client: AsyncClient,
        client: AsyncClient,
        test_service: models.Service,
):
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "testuser",
            "client_phone": "0123456789",
            "client_email": "test@example.com",
        }
    )
    appointment_id = response.json()["id"]

    response = await guest_client.patch(
        f"/api/appointments/{appointment_id}",
        json={"start_time": booking_time.isoformat()},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# Updating an appointment with invalid service
@pytest.mark.anyio
async def test_update_appointment_failed(auth_client: AsyncClient, client: AsyncClient, test_service: models.Service):

    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "Elena Rossi",
            "client_phone": "+393123456789",
            "client_email": "elena@example.com",
        }
    )
    appointment_id = response.json()["id"]

    invalid_service_id = 9999
    response = await auth_client.patch(
        f"/api/appointments/{appointment_id}",
        json={"service_id": invalid_service_id},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"


# Validation Error (422 Unprocessable Entity): Invalid payload data
@pytest.mark.anyio
async def test_update_appointment_invalid(
        auth_client: AsyncClient,
        client: AsyncClient,
        test_service: models.Service,
):

    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "Elena Rossi",
            "client_phone": "+393123456789",
            "client_email": "elena@example.com",
        }
    )
    appointment_id = response.json()["id"]

    response = await auth_client.patch(
        f"/api/appointments/{appointment_id}",
        json={"start_time": "invalid - datetime - format"},

    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT



# testing PATCH Endpoint: update appointment by guest
@pytest.mark.anyio
async def test_update_appointment_success_guest(
        guest_client: AsyncClient,
        test_service: models.Service,
    ):
    booking_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    response = await guest_client.post(
        "/api/appointments",
        json={
            "service_id": test_service.id,
            "start_time": booking_time.isoformat(),
            "client_name": "test Guest",
            "client_phone": "0123456789",
            "client_email": "test@example.com",
            }
        )

    guest_token = response.json()["guest_token"]

    # Define a valid new datetime (e.g., +2 hours)
    new_start_time = booking_time + timedelta(hours=2)

    response = await guest_client.patch(
        f"/api/appointments/guest/{guest_token}",
        json={"start_time": new_start_time.isoformat()},

    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["start_time"] == new_start_time.isoformat().replace("+00:00", "Z")


# updating guests' appointment with invalid token
@pytest.mark.anyio
async def test_update_appointment_guest_invalid_token(
        client: AsyncClient,
        test_service: models.Service,
):
    booking_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    fake_token = "invalid_guest-token"
    response = await client.patch(
        f"/api/appointments/guest/{fake_token}",
        json={"start_time": booking_time.isoformat()},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


# updating payload with invalid payload
@pytest.mark.anyio
async def test_update_appointment_guest_invalid(guest_client: AsyncClient,):
    token = guest_client.guest_token

    response = await guest_client.patch(
            f"/api/appointments/guest/{token}",
            json={"start_time": "invalid-payload"}
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT



# testing GET Endpoint: Verify that admin can fetch all appointments (both guest and user bookings)
@pytest.mark.anyio
async def test_get_appointments_admin(
        admin_client: AsyncClient,
        test_service: models.Service,
        client: AsyncClient,
        auth_client: AsyncClient,
):

    # Create a guest booking
    booking_time_guest = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    response = await client.post(
            "/api/appointments",
            json={
            "service_id": test_service.id,
            "start_time": booking_time_guest.isoformat(),
            "client_name": "test Guest",
            "client_phone": "0123456789",
            "client_email": "test@example.com",
            }
        )
    assert response.status_code == 201

    # Create a user booking (authenticated client)
    booking_time_user = (datetime.now(timezone.utc) + timedelta(days=1, hours=2)).replace(microsecond=0)
    res_user = await auth_client.post(
            "/api/appointments",
            json={
                "service_id": test_service.id,
                "start_time": booking_time_user.isoformat(),
                "client_name": "Registered User",
                "client_phone": "0123456789",
                "client_email": "user@example.com",
            },
        )
    assert res_user.status_code == 201

    # Assert admin sees both (total == 2)
    response = await admin_client.get("/api/appointments/admin")
    assert response.status_code == 200
    data = response.json()

    # Verify both appointments are returned
    assert data["total"] == 2
    assert len(data["appointments"]) == 2


# verify that admin can get users' appointments with skip/limit/offset
@pytest.mark.anyio
async def test_get_appointments_admin_skip(
        admin_client: AsyncClient,
        test_service: models.Service,
        auth_client: AsyncClient,
        db_session: AsyncSession,
):
    # Fetch the user created inside auth_client fixture
    result = await db_session.execute(
        select(models.User).where(models.User.email == "test@example.com")
    )
    test_user = result.scalars().first()
    assert test_user is not None

    # Seed 5 appointments directly linked to test_user.id
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    for i in range(5):
        slot_time = booking_time + timedelta(hours=i * 2)  # Spaced out by 2 hours
        await crud.create_appointment(
            db=db_session,
            appointment=schemas.AppointmentCreate(
                service_id=test_service.id,
                start_time=slot_time,
                client_name="Test",
                client_phone="+393123456789",
                client_email="test@example.com",
            ),
            end_time=slot_time + timedelta(minutes=test_service.duration),
            user_id=test_user.id,  # Directly links to auth_client's account
        )

    # Fetch all services (Default skip=0, limit=10)
    # We expect to see all 5 appointments returned at once
    response = await admin_client.get("/api/appointments/admin")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 5
    assert data["has_more"] is False

    # Fetch first page (Pagination)
    response = await admin_client.get("/api/appointments/admin?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 2
    assert data["has_more"] is True

    # Fetch with skip and limit, middle page (complex pagination)
    response = await admin_client.get("/api/appointments/admin?skip=2&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 2
    assert data["skip"] == 2
    assert data["limit"] == 2


# Verify admin can paginate through guests' appointments
@pytest.mark.anyio
async def test_get_appointments_admin_skip_guest(
        admin_client: AsyncClient,
        test_service: models.Service,
        auth_client: AsyncClient,
        db_session: AsyncSession,
):
    # Fetch the user created inside auth_client fixture
    result = await db_session.execute(
        select(models.User).where(models.User.email == "test@example.com")
    )
    test_user = result.scalars().first()
    assert test_user is not None

    # # Seed 5 guest appointments linked to test_user.id for FK constraint
    booking_time = datetime.now(timezone.utc) + timedelta(days=1)
    for i in range(5):
        slot_time = booking_time + timedelta(hours=i * 2)  # Spaced out by 2 hours
        await crud.create_appointment(
            db=db_session,
            appointment=schemas.AppointmentCreate(
                service_id=test_service.id,
                start_time=slot_time,
                client_name=f"Jane Guest{i}",
                client_phone="+393123456789",
                client_email=f"jane{i}@example.com",
            ),
            end_time=slot_time + timedelta(minutes=test_service.duration),
            user_id=test_user.id,  # Satisfies NOT NULL foreign key constraint
        )

    # Fetch all services (Default skip=0, limit=10)
    # We expect to see all 5 appointments returned at once
    response = await admin_client.get("/api/appointments/admin")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 5
    assert data["has_more"] is False

    # Fetch first page (Pagination)
    response = await admin_client.get("/api/appointments/admin?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 2
    assert data["has_more"] is True

    # Fetch with skip and limit, middle page (complex pagination)
    response = await admin_client.get("/api/appointments/admin?skip=2&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["appointments"]) == 2
    assert data["skip"] == 2
    assert data["limit"] == 2


# empty state
@pytest.mark.anyio
async def test_get_appointments_admin_empty(admin_client: AsyncClient):
    response = await admin_client.get("/api/appointments/admin")
    assert response.status_code == 200

    assert response.json()["appointments"] == []
    assert response.json()["total"] == 0
    assert response.json()["skip"] == 0
    assert response.json()["has_more"] is False


# Unauthenticated Access (401): a plain client can't get all appointments
@pytest.mark.anyio
async def test_get_appointments_admin_unauthorized(client: AsyncClient):
    response = await client.get("/api/appointments/admin")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Not authenticated"


# Non-Admin User Access (403): Logged-in regular client
@pytest.mark.anyio
async def test_get_appointments_admin_forbidden(auth_client: AsyncClient):
    response = await auth_client.get("/api/appointments/admin")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Admin privileges required"


# Admin A cannot view appointments belonging to Admin B
@pytest.mark.anyio
async def test_tenant_isolation(
        admin_client: AsyncClient,  # Pre-configured with Admin A's token in conftest
        db_session: AsyncSession,
        client: AsyncClient,  # Unauthenticated raw client
):
    # Seed Admin B in the database
    admin_b = models.Admin(email="admin_b@example.com", password_hash="hashed_pass")
    db_session.add(admin_b)
    await db_session.flush()

    #  Generate a valid JWT token specifically for Admin B
    token_b = create_access_token(data={"sub": str(admin_b.id)})
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Admin A's request uses admin_client (Admin A's headers)
    res_a = await admin_client.get("/api/appointments/admin")

    # Admin B's request uses raw client + Admin B's headers
    res_b = await client.get("/api/appointments/admin", headers=headers_b)

    assert res_a.status_code == 200
    assert res_b.status_code == 200


# CRUD unit test: covers main statement execution
@pytest.mark.anyio
async def test_crud_get_all_appointments_admin(db_session: AsyncSession):
    appointments, total = await crud.get_all_appointments_admin(db=db_session)
    assert isinstance(appointments, list)
    assert isinstance(total, int)


# CRUD Unit test: filter by user_id
@pytest.mark.anyio
async def test_crud_get_all_appointments_admin_id_filter(db_session: AsyncSession, auth_client: AsyncClient):
    user = await crud.get_user_by_email(db=db_session, email="test@example.com")
    assert user is not None

    appointments, total = await crud.get_all_appointments_admin(db=db_session, user_id=user.id)
    assert isinstance(appointments, list)

# CRUD Unit test: Filter by client_email
@pytest.mark.anyio
async def test_crud_get_all_appointments_admin_email_filter(db_session: AsyncSession):
    appointments, total = await crud.get_all_appointments_admin(db=db_session, client_email="test@example.com")
    assert isinstance(appointments, list)



# testing DELETE Endpoint: delete appointment by logged-in user
@pytest.mark.anyio
async def test_delete_appointment_user_success(
        auth_client: AsyncClient,
        test_service: models.Service,
        db_session: AsyncSession,
):

    # Fetch the user created by auth_client
    result = await db_session.execute(
        select(models.User).where(models.User.email == "test@example.com")
    )
    user = result.scalars().first()

    # Seed an appointment
    booking_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    end_time = booking_time + timedelta(minutes=test_service.duration)

    appointment = models.Appointment(
        user_id=user.id,
        service_id=test_service.id,
        start_time=booking_time,
        end_time=end_time,
    )
    db_session.add(appointment)
    await db_session.commit()

    # Deleting appointment
    response = await auth_client.delete(f"/api/appointments/{appointment.id}")
    assert response.status_code == 204

# Cancel non-existed appointment
@pytest.mark.anyio
async def test_delete_appointment_not_found(auth_client: AsyncClient):
    response = await auth_client.delete("/api/appointments/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"

# Unauthorized: guest can't delete users' appointment
@pytest.mark.anyio
async def test_delete_appointment_unauthorized(client: AsyncClient):
    response = await client.delete("/api/appointments/1")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Not authenticated"

# Verify User A cannot delete an appointment belonging to User B (returns 404)
@pytest.mark.anyio
async def test_delete_appointment_isolation(
        auth_client: AsyncClient,
        other_auth_client: AsyncClient,
        db_session: AsyncSession,
        test_service: models.Service,
):
    # Fetch the user created by auth_client
    result = await db_session.execute(
        select(models.User).where(models.User.email == "test@example.com")
    )
    user = result.scalars().first()

    # Seed an appointment
    booking_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    end_time = booking_time + timedelta(minutes=test_service.duration)

    appointment = models.Appointment(
        user_id=user.id,
        service_id=test_service.id,
        start_time=booking_time,
        end_time=end_time,
    )
    db_session.add(appointment)
    await db_session.commit()

    # User B attend to delete Users' A appointment
    response = await other_auth_client.delete(f"/api/appointments/{appointment.id}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"


# testing DELETE Endpoint: delete appointment by guest
@pytest.mark.anyio
async def test_delete_appointment_guest_success(guest_client: AsyncClient):
    token=guest_client.guest_token
    response = await guest_client.delete(f"/api/appointments/guest/{token}")
    assert response.status_code == status.HTTP_204_NO_CONTENT


# Verify passing a non-existent guest token returns 404 Not Found
@pytest.mark.anyio
async def test_delete_appointment_guest_not_found(client: AsyncClient):
    fake_token = "fake-token"
    response = await client.delete(f"/api/appointments/guest/{fake_token}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"


# Verify guest endpoint cannot delete a registered user's appointment (guest_token is None)
@pytest.mark.anyio
async def test_delete_appointment_guest_forbidden(
        guest_client: AsyncClient,
        auth_client: AsyncClient,
        db_session: AsyncSession,
        test_service: models.Service
    ):
    # Create an appointment belonging a registered user
    result = await db_session.execute(select(models.User).where(models.User.email == "test@example.com"))
    user = result.scalars().first()

    appointment = models.Appointment(
        user_id=user.id,
        service_id=test_service.id,
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(minutes=test_service.duration),
        guest_token=None,
    )
    db_session.add(appointment)
    await db_session.commit()

    # Attempt to call guest delete route with random or invalid token
    response = await auth_client.delete(f"/api/appointments/guest/invalid-token")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"



# testing DELETE Endpoint: delete appointment by admin
# Verify an admin can successfully delete any appointment
@pytest.mark.anyio
async def test_delete_appointment_admin_success(
        admin_client: AsyncClient,
        test_service: models.Service,
        db_session: AsyncSession,
):
    # Create a temporary user to satisfy user_id constraint
    temp_user = models.User(
        username="temp_client",
        email="tem@example.com",
        phone="1234567890",
        password_hash="hashed-password",
    )
    db_session.add(temp_user)
    await db_session.commit()

    # Seed the appointment directly in db
    booking_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    appointment = models.Appointment(
        user_id=temp_user.id,
        service_id=test_service.id,
        start_time=booking_time,
        end_time=booking_time + timedelta(minutes=test_service.duration),
    )
    db_session.add(appointment)
    await db_session.commit()

    # Perform deleting by admin
    response = await admin_client.delete(f"/api/appointments/admin/{appointment.id}")
    assert response.status_code == status.HTTP_204_NO_CONTENT


# Verify Admin B cannot delete an appointment belonging to Admin A's salon
@pytest.mark.anyio
async def test_cross_tenant_isolation(
    test_service: models.Service,  # Belongs to Admin A (Salon A)
    client: AsyncClient,           # Raw client
    db_session: AsyncSession,
):
    """Verify Admin B cannot delete an appointment belonging to Admin A's service."""
    # Seed Admin B (Salon B)
    admin_b = models.Admin(email="admin_b@example.com", password_hash="pass_b")
    db_session.add(admin_b)
    await db_session.commit()

    # Seed dummy user & appointment under Admin A's test_service
    temp_user = models.User(
        username="client_a",
        email="client_a@example.com",
        phone="1234567890",
        password_hash="pass",
    )
    db_session.add(temp_user)
    await db_session.commit()

    booking_time = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    appointment = models.Appointment(
        user_id=temp_user.id,
        service_id=test_service.id,  # Admin A's service
        start_time=booking_time,
        end_time=booking_time + timedelta(minutes=test_service.duration),
    )
    db_session.add(appointment)
    await db_session.commit()

    # Create Admin B's headers
    token_b = create_access_token(data={"sub": str(admin_b.id)})
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Admin B attempts to delete Admin A's appointment
    response = await client.delete(
        f"/api/appointments/admin/{appointment.id}",
        headers=headers_b
    )

    # Assert 404 Not Found
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"

# Deleting appointment with invalid ID
@pytest.mark.anyio
async def test_delete_appointment_invalid_id(admin_client: AsyncClient):

    response = await admin_client.delete(f"/api/appointments/admin/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Appointment not found"

# Unauthorized: a client can't delete appointment using admin route
@pytest.mark.anyio
async def test_delete_appointment_admin_unauthorized(client: AsyncClient):
    response = await client.delete(f"/api/appointments/admin/1")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Not authenticated"

# Forbidden: a logged-in user can't delete appointment using admin route
@pytest.mark.anyio
async def test_delete_appointment_admin_forbidden(auth_client: AsyncClient):
    response = await auth_client.delete(f"/api/appointments/admin/1")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Admin privileges required"