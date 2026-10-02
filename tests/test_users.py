import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import crud
import models
import schemas
from crud import get_user_by_email, get_user_by_username, partial_user_update

from fastapi import status

from tests.conftest import create_test_user


# testing POST Endpoint: Create a new user
@pytest.mark.anyio
async def test_create_user_success(client: AsyncClient):
    response = await client.post(
        "/api/users",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "securepassword123",
            "phone": "0123456789"
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "newuser"
    assert data["email"] == "newuser@example.com"
    assert "id" in data
    assert "password" not in data
    assert "password_hash" not in data
    assert "phone" in data


# Failure: Creating user with missing email and password
@pytest.mark.anyio
async def test_create_user_validation_error(client: AsyncClient):

    response = await client.post(
        "/api/users",
        json={"username": "testuser",},
    )

    assert response.status_code == 422
    assert "email" in response.text
    assert "password" in response.text


# Failure: Creating user with email that already exists
@pytest.mark.anyio
async def test_create_user_duplicate_email(client: AsyncClient,):

    await create_test_user(client)

    response = await client.post(
        "/api/users",
        json={
            "username": "different_user",
            "email": "test@example.com",
            "password": "password123",
            "phone": "0123456789",
        },
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Email is already registered"


# Failure: Creating user with username that already exists
@pytest.mark.anyio
async def test_create_user_duplicate_username(client: AsyncClient,):
    await create_test_user(client)

    response = await client.post(
        "/api/users",
        json={
            "username": "testuser",
            "email": "different@email.com",
            "password": "different123",
            "phone": "3285596647",
        }
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "User with this username already exists"


# Failure: Creating user with a phone that already exists
@pytest.mark.anyio
async def test_create_user_duplicate_phone(client: AsyncClient,):
    await create_test_user(client)

    response = await client.post(
        "/api/users",
        json={
            "username": "Marina",
            "email": "marina@email.com",
            "password": "different123",
            "phone": "0123456789",
        }
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Phone is already registered"


# testing GET /me endpoint
# SUCCESS CASE: Authenticated user fetches their own profile
@pytest.mark.anyio
async def test_get_current_user_success(auth_client: AsyncClient):
    # ACT: Send GET request using the pre-authenticated client
    response = await auth_client.get("/api/users/me")
    # ASSERT
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert data["email"] == "test@example.com"


# FAILURE CASE: Requesting profile without Authorization header
@pytest.mark.anyio
async def test_get_current_user_failed(client: AsyncClient):
    # ACT: Send GET request using the non-authenticated client
    response = await client.get("/api/users/me")
    assert response.status_code == 401
    # ASSERT: FastAPI auto-rejects missing credentials with 401
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# FAILURE CASE: Requesting profile with a fake or expired token
@pytest.mark.anyio
async def test_get_current_user_invalid(client: AsyncClient):
    # Construct headers with a malformed/expired JWT string
    invalid_headers = {"Authorization": f"Bearer invalid_or_expired_token_123"}
    response = await client.get("/api/users/me", headers=invalid_headers)
    assert response.status_code == 401
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    # Verify error message payload from JWT validation failure
    assert "detail" in response.json()


# test GET Endpoint: Fetches paginated users (Admin Only)
# Success: fetch users with pagination
@pytest.mark.anyio
async def test_get_all_users_admin_success(admin_client: AsyncClient, db_session: AsyncSession):
    # Seed 5 additional users directly via CRUD helper
    for i in range(5):
        user_in = schemas.UserCreate(
            username=f"seededuser{i}",
            email=f"seeded{i}@example.com",
            password="password123",
            phone=f"123456789{i}",
        )
        await crud.create_user(db = db_session, user=user_in)

    # The total will include seeded users + the admin user created by the fixture
    response = await admin_client.get("/api/users")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 5
    assert len(data["users"]) >= 5
    assert data["has_more"] is False

    # Fetch a limited subset
    response = await admin_client.get("/api/users?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 5
    assert len(data["users"]) == 2
    assert data["has_more"] is True

    # Fetch with skip and limit (complex pagination)
    response = await admin_client.get("/api/users?skip=2&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 5
    assert len(data["users"]) == 2
    assert data["skip"] == 2
    assert data["limit"] == 2


# Success: fetch users with search(q) parameters
@pytest.mark.anyio
async def test_get_all_users_admin_search_q(admin_client: AsyncClient, db_session: AsyncSession):
    user = schemas.UserCreate(
            username="Svetlana",
            email="svetlana@example.com",
            password="password123",
            phone="123456789",
        )
    await crud.create_user(db = db_session, user=user)
    response = await admin_client.get("/api/users?q=Svetlana")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["users"][0]["username"] == "Svetlana"


# Success: fetch all users which account is active
@pytest.mark.anyio
async def test_get_all_users_admin_filter_active(admin_client: AsyncClient, db_session: AsyncSession):
    response = await admin_client.get("/api/users?is_active=true")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "users" in data


# Failure: Regular non-admin user is forbidden (403)
@pytest.mark.anyio
async def test_get_all_users_user_forbidden(auth_client: AsyncClient):
    response = await auth_client.get("/api/users")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "detail" in response.json()


# Failure: Guest user - unauthenticated request
@pytest.mark.anyio
async def test_get_all_users_guest_forbidden(client: AsyncClient):
    response = await client.get("/api/users")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "detail" in response.json()

# Unit test
@pytest.mark.anyio
async def test_crud_get_users_direct(db_session: AsyncSession):
    # Direct CRUD call bypasses HTTP router and auth middleware
    users, total = await crud.get_users(db_session, skip=0, limit=10)

    assert isinstance(users, list)
    assert isinstance(total, int)

# Unit test
@pytest.mark.anyio
async def test_crud_get_user_username(db_session: AsyncSession, auth_client: AsyncClient):
    # Get existing user created by auth_client fixture
    user = await crud.get_user_by_username(db_session, username="testuser")
    assert user is not None

    # Test case-insensitive match (Pass uppercase username)
    found_user = await crud.get_user_by_username(db_session, username=user.username)
    assert found_user is not None
    assert found_user.id == user.id

    # Test non-existent username branch (Returns None)
    non_existent_user = await crud.get_user_by_username(db_session, username="non_existent_user")
    assert non_existent_user is None


# Unit test get user by phone if provided
@pytest.mark.anyio
async def test_crud_get_user_by_phone(db_session: AsyncSession, auth_client: AsyncClient):
    # Get existing user created by auth_client fixture
    user = await crud.get_user_by_phone(db_session, phone="0123456789")
    assert user is not None

    found_user = await crud.get_user_by_phone(db_session, phone=user.phone)
    assert found_user is not None
    assert found_user.id == user.id

    non_existent_user = await crud.get_user_by_phone(db_session, phone="non_existent_phone")
    assert non_existent_user is None


# test PATCH Endpoint: logged-in user update his profile
# User successfully update his profile
@pytest.mark.anyio
async def test_user_update_success(auth_client: AsyncClient):
    # Access user_id directly from the client attribute
    user_id = auth_client.user_id

    response = await auth_client.patch(
        f"/api/users/{user_id}",
        json={"username": "Updated name"},

    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "Updated name"
    assert data["email"] == "test@example.com"
    assert data["phone"] == "0123456789"


# Unit test partial update success
@pytest.mark.anyio
async def test_crud_partial_update_success(db_session: AsyncSession):
    """ Successful partial update of user profile """
    user = models.User(
        username="Eva",
        email="eva@email.com",
        phone="0123456789",
        password_hash="password123",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Pass user into the CRUD function
    update_data = schemas.UserUpdate(phone="32887565258")
    updated_user = await crud.partial_user_update(
        db=db_session,
        user=user,
        user_update=update_data,
    )
    assert updated_user.phone == "32887565258"


# Non-authorized user wants to update not his profile: forbidden(403)
@pytest.mark.anyio
async def test_non_authorized_user_update_failed(auth_client: AsyncClient):
    # Use an ID that does not belong to the current authenticated user
    other_user = auth_client.user_id + 99

    response = await auth_client.patch(
        f"/api/users/{other_user}",
        json={"username": "Updated name"},

    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Not authorized" in response.json()["detail"]


# Non-authenticated client wants to update users' profile (401)
@pytest.mark.anyio
async def test_non_authenticated_user_update_failed(client: AsyncClient):
    response = await client.patch(
        "/api/users/1",
        json={"username": "Updated name"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Not authenticated" in response.json()["detail"]

# conflict (409) if user wants to update his profile with username that already exist
@pytest.mark.anyio
async def test_user_update_conflict(auth_client: AsyncClient, client: AsyncClient,):
    # Create user
    existing_user = await create_test_user(
        client,
        username="existinguser",
        email="existing@example.com",
        phone="0987654321",
    )
    user_id = auth_client.user_id
    response = await auth_client.patch(
        f"/api/users/{user_id}",
        json={"username": "existinguser"},
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "User with this username already exists"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload, expected_status, expected_detail",
    [
        (
            {"username": "existinguser"},
            status.HTTP_409_CONFLICT,
            "User with this username already exists",
        ),
        (
            {"email": "existing@example.com"},
            status.HTTP_409_CONFLICT,
            "User with this email already exists",
        ),
        (
            {"phone": "0987654321"},
            status.HTTP_409_CONFLICT,
            "User with this phone already exists",
        ),
    ],
)
async def test_user_update_collisions(
    auth_client: AsyncClient,
    client: AsyncClient,
    payload: dict,
    expected_status: int,
    expected_detail: str,
):
    # Create the secondary target user once per test run
    await create_test_user(
        client,
        username="existinguser",
        email="existing@example.com",
        phone="0987654321",
    )

    user_id = auth_client.user_id

    response = await auth_client.patch(
        f"/api/users/{user_id}",
        json=payload,
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail


# test DELETE /me
@pytest.mark.anyio
async def test_user_delete_success(auth_client: AsyncClient):
    response =  await auth_client.delete("/api/users/me")
    assert response. status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""

# self-deletion 401 unauthenticated
@pytest.mark.anyio
async def test_user_delete_failure(client: AsyncClient):
    response = await client.delete("/api/users/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "detail" in response.json()

# Unit test
@pytest.mark.anyio
async def test_crud_user_delete(db_session: AsyncSession):
    user = models.User(
        username="Eva",
        email="eva@.com",
        phone="0123456789",
        password_hash="password123",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    deleting_user = await crud.delete_profile(db=db_session, user=user)

    # Assert the user is no longer in the database
    user_id = user.id
    deleted_user = await db_session.get(models.User, user_id)
    assert deleted_user is None


# test DELETE Endpoint: admin can delete users' profile
# success
@pytest.mark.anyio
async def test_admin_delete_success(admin_client: AsyncClient, auth_client: AsyncClient):
    user_id = auth_client.user_id
    response = await admin_client.delete(f"/api/users/{user_id}")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""

# delete not existing user
@pytest.mark.anyio
async def test_admin_delete_failure(admin_client: AsyncClient, auth_client: AsyncClient):
    non_existent_user = auth_client.user_id + 999
    response = await admin_client.delete(f"/api/users/{non_existent_user}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "User not found"

# regular user can't use admin delete
@pytest.mark.anyio
async def test_user_cannot_use_admin_delete(auth_client: AsyncClient):
    user_id = auth_client.user_id
    response = await auth_client.delete(f"/api/users/{user_id}")
    assert response.status_code == status.HTTP_403_FORBIDDEN

# non-authenticated admin can't delete users' profile
@pytest.mark.anyio
async def test_non_authenticated_admin_failure(client: AsyncClient):
    response = await client.delete(f"/api/users/1")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Not authenticated" in response.json()["detail"]

