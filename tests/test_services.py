import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from main import app
from security import get_current_user, password_hash
from tests.conftest import admin_client, auth_client, other_auth_client, db_session
from fastapi import status

# testing POST Endpoint: Creates a new service
# Happy Path: Admin User
@pytest.mark.anyio
async def test_create_service_success(admin_client: AsyncClient):
    payload = {
        "name": "Gel Polish",
        "description": "Full gel manicure application",
        "price": 35,
        "duration": 90,
    }

    response = await admin_client.post("/api/services", json=payload)

    assert response.status_code == 201
    data = response.json()

    assert "id" in data
    for key, value in payload.items():
        assert data[key] == value


# Sad Path: Non-Admin / Regular User
@pytest.mark.anyio
async def test_create_service_as_regular_user(auth_client: AsyncClient):
    """Verify that non-admin users are blocked with 403 Forbidden."""
    payload = {
        "name": "Unauthorized Service",
        "description": "Attempted creation by regular user",
        "price": 20,
        "duration": 30,
    }

    # authenticated_client provides a regular user token without the dependency override
    response = await auth_client.post("/api/services", json=payload)

    assert response.status_code == status.HTTP_403_FORBIDDEN


# Create service with invalid payload
@pytest.mark.anyio
async def test_create_service_invalid_payload(admin_client: AsyncClient):
    """Verify HTTP 422 when missing required Pydantic fields (e.g., name and price)."""
    payload = {"name": "Gel Polish"}
    response = await admin_client.post("/api/services", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# testing GET Endpoint: list available services for clients
@pytest.mark.anyio
async def test_get_services_with_pagination(admin_client: AsyncClient, auth_client: AsyncClient):

    # Seed the database with 5 services using admin
    # This allows us to test logic against a known, finite set of data
    for i in range(5):
        response = await admin_client.post(
            "/api/services",
            json={
                "name": f"Gel Polish{i}",
                "description": f"Full gel manicure application{i}",
                "price": 35,
                "duration": 90,
            }
        )
        assert response.status_code == 201

    # Fetch all services (Public/ Client access)
    # We expect to see all 5 sevices returned at once
    response = await auth_client.get("/api/services")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["services"]) == 5
    assert data["has_more"] is False

    # Fetch a limited subset (Pagination)
    response = await auth_client.get("/api/services?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["services"]) == 2
    assert data["has_more"] is True

    # Fetch with skip and limit (complex pagination)
    response = await auth_client.get("/api/services?skip=2&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["services"]) == 2
    assert data["skip"] == 2
    assert data["limit"] == 2


# Unit test
@pytest.mark.anyio
async def test_crud_get_services(db_session: AsyncSession, test_service: models.Service):
    services, total = await crud.get_services(db_session, skip=0, limit=10)
    assert total == 1
    assert len(services) == 1
    assert services[0].id == test_service.id
    assert services[0].name == test_service.name


# empty state
@pytest.mark.anyio
async def test_get_services_empty(client: AsyncClient):
    response = await client.get("/api/services")
    assert response.status_code == 200

    # Parse the JSON body returned by the server.
    data = response.json()

    # Verify the "empty state" of our application.
    # We expect a new, clean database to return an empty list of services.
    assert data["services"] == []  # Verify the list is empty
    assert data["total"] == 0  # Verify the count is zero
    assert data["has_more"] is False  # Verify pagination is inactive


# Input validation (negative skip / oversized limit)
@pytest.mark.anyio
async def test_get_services_failed(client: AsyncClient):
    negative_skip =  await client.get("/api/services?skip=-2")
    assert negative_skip.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    oversized_limit = await client.get("/api/services?limit=999")
    assert oversized_limit.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT



# testing GET Endpoint: fetches service by its id
# Retrieve an existing service by ID (Happy Path)
@pytest.mark.anyio
async def test_get_service_success(client: AsyncClient, test_service: models.Service):
    response = await client.get(f"/api/services/{test_service.id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == test_service.id
    assert data["name"] == test_service.name
    assert data["description"] == test_service.description
    assert data["price"] == test_service.price
    assert data["duration"] == test_service.duration


# Unit test
@pytest.mark.anyio
async def test_crud_get_service_by_id(db_session: AsyncSession, test_service: models.Service):
    service = await crud.get_service_by_id(db_session, test_service.id)
    assert service is not None
    assert service.id == test_service.id


# Retrieve a non-existent service ID
@pytest.mark.anyio
async def test_get_service_not_found(client: AsyncClient):
    response = await client.get("/api/services/999")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Service not found"


# Invalid path parameter type
@pytest.mark.anyio
async def test_get_service_invalid_id(client: AsyncClient):
    response = await client.get(f"/api/services/invalid-id")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT



# testing PATCH Endpoint: update service detail
@pytest.mark.anyio
async def test_update_service_success_admin(
        test_service: models.Service, admin_client: AsyncClient):
    """Verify that an admin user can successfully update a service."""

    response = await admin_client.patch(
        f"/api/services/{test_service.id}",
        json={"price": "40"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["price"] == 40
    # Ensure omitted fields were preserved
    assert data["name"] == test_service.name
    assert data["description"] == test_service.description
    assert data["duration"] == test_service.duration


# Unit test
@pytest.mark.anyio
async def test_crud_partial_service_update(auth_client: AsyncClient, test_service: models.Service, db_session: AsyncSession):
    updated_service = schemas.ServiceUpdate(price=50)

    service = await crud.partial_service_update(
        db_session,
        service=test_service,
        service_update=updated_service,
    )
    assert service.price == 50
    assert service.name == test_service.name
    assert service.description == test_service.description

# Unauthorized: guest can't update service details
@pytest.mark.anyio
async def test_update_service_client(test_service: models.Service, client: AsyncClient):
    response = await client.patch(
        f"/api/services/{test_service.id}",
        json={"price": "40"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# Forbidden: user can't update service details
@pytest.mark.anyio
async def test_update_service_user_forbidden(test_service: models.Service, other_auth_client: AsyncClient):
    response = await other_auth_client.patch(
        f"/api/services/{test_service.id}",
        json={"price": "40"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN

#updating service that doesn't exist
@pytest.mark.anyio
async def test_update_service_not_found(admin_client: AsyncClient):
    payload = {"price": 100.0}

    response = await admin_client.patch("/api/services/999999", json=payload)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Service not found"


# testing DELETE Endpoint: delete service
# success
@pytest.mark.anyio
async def test_delete_service_success(admin_client: AsyncClient, test_service: models.Service, db_session):
    response = await admin_client.delete(f"/api/services/{test_service.id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    # Verify record is deleted from PostgreSQL
    deleted_service = await db_session.get(models.Service, test_service.id)
    assert deleted_service is None

# deleting service by admin that doesn't exist
@pytest.mark.anyio
async def test_delete_service_not_found(admin_client: AsyncClient, test_service: models.Service):
    response = await admin_client.delete(f"/api/services/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Service not found"

# guest can't delete service
@pytest.mark.anyio
async def test_delete_service_guest_failed(client: AsyncClient, test_service: models.Service):
    response = await client.delete(f"/api/services/{test_service.id}")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

# standard user can't delete service
@pytest.mark.anyio
async def test_delete_service_user_failed(other_auth_client: AsyncClient, test_service: models.Service):
    response = await other_auth_client.delete(f"/api/services/{test_service.id}")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Admin privileges required"


