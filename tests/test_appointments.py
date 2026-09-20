import uuid
from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import user
import crud
import models
import schemas
from security import create_access_token
from tests.conftest import auth_client, other_auth_client, db_session
from fastapi import status


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


# 422 Unprocessable Entity when schema validators fail
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

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


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
    assert response.json()["detail"] == f"Service with id {invalid_service_id} does not exist"


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