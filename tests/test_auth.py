from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import crud
import models
from crud import get_user_by_email
from security import hash_reset_token, verify_password


# testing Admin Login Endpoint
@pytest.mark.anyio
async def test_admin_login_success(admin_client: AsyncClient, client: AsyncClient, db_session: AsyncSession):

    admin = await crud.get_admin_by_email(db_session, "admin@example.com")
    assert admin is not None

    response = await client.post(
        "/api/auth/admin/token",
    data = {
        "username":"admin@example.com",
        "password":"admin123",
        },
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


# Verify login fails when providing an incorrect username/password
@pytest.mark.parametrize(
    "payload, expected_status, expected_detail",
    [
        (
            {"username": "wrong_email", "password": "admin123"},
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
        ),
        (
            {"username":"admin@example.com", "password": "wrong_password"},
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
        ),
    ],
)
async def test_admin_login_invalid_credentials(
    admin_client: AsyncClient,
    client: AsyncClient,
    payload: dict,
    expected_status: int,
    expected_detail: str,
):
    response = await client.post(
        "/api/auth/admin/token",
        data=payload,
    )
    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail


# regular user cannot login as an admin
@pytest.mark.anyio
async def test_user_login_failure(client: AsyncClient, auth_client: AsyncClient):
    # auth_client fixture seeds a regular user in the DB (e.g. user@example.com / password123)
    response = await client.post(
        "/api/auth/admin/token",
        data={"username": "user@example.com",
            "password": "userpassword123",
              }
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Incorrect email or password"



# testing User Login Endpoint
# Verify registered users can get a valid JWT access token
@pytest.mark.anyio
async def test_user_login_success(client: AsyncClient, auth_client: AsyncClient, db_session: AsyncSession):

    user = await crud.get_user_by_email(db_session, "test@example.com")
    assert user is not None

    # Login request (OAuth2 standard requires form-encoded data)
    response = await client.post(
        "/api/auth/token", #token endpoint
        data={
            "username": "test@example.com",
            "password": "testpassword123",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data.get("token_type") == "bearer"


# Verify login fails when providing an incorrect username/password
@pytest.mark.parametrize(
    "payload, expected_status, expected_detail",
    [
        (
            {"username": "wrong_email", "password": "testpassword123"},
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
        ),
        (
            {"username":"test@example.com", "password": "wrong_password"},
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
        ),
    ],
)
async def test_user_login_invalid_credentials(
    admin_client: AsyncClient,
    client: AsyncClient,
    payload: dict,
    expected_status: int,
    expected_detail: str,
):
    response = await client.post(
        "/api/auth/token",
        data=payload,
    )
    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail


# test Send a link with a new token if a user forgot his password
@pytest.mark.anyio
async def test_forgot_password_sends_email(client: AsyncClient, auth_client: AsyncClient,db_session: AsyncSession):

    # Mock the background email function so no actual email is sent during tests
    with patch(
        "routers.auth.send_password_reset_email",
        new_callable=AsyncMock,
    ) as mock_send:
        response = await client.post(
            "/api/auth/forgot-password",
            json={"email": "test@example.com"},
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["to_email"] == "test@example.com"
        assert call_kwargs["username"] == "testuser"
        assert "token" in call_kwargs

        # Verify that a reset token was created in the database for the user
        result = await db_session.execute(
            select(models.User).where(models.User.email == "test@example.com")
        )
        user = result.scalars().first()
        assert user is not None

        token_result = await db_session.execute(
            select(models.PasswordResetToken).where(
                models.PasswordResetToken.user_id == user.id
            )
        )
        reset_token = token_result.scalars().first()
        assert reset_token is not None
        assert reset_token.expires_at is not None


# Request for a non-existent user (Security / Non-enumeration test)
@pytest.mark.anyio
async def test_forgot_password_non_existent_email(client: AsyncClient, db_session: AsyncSession):
    """
        Verify that non-existent emails return the exact same 202 response
        to prevent user enumeration, but generate no database records.
    """
    response = await client.post(
        "/api/auth/forgot-password",
        json={"email": "nonexistent@example.com"},
    )
    # Must return 202 with an identical message
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == {
        "message": "If the email exists, a reset link has been sent."
    }
    # Ensure no token was created in the DB
    token_result = await db_session.execute(select(models.PasswordResetToken))
    tokens = token_result.scalars().all()
    assert len(tokens) == 0


# Request for wrong email
@pytest.mark.anyio
async def test_forgot_password_wrong_email(client: AsyncClient):
    response = await client.post(
        "/api/auth/forgot-password",
        json={"email": "wrong-email"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# testing reset_password endpoint
# Success: Verify that a valid token resets the user's password in the database
@pytest.mark.anyio
async def test_reset_password_success(auth_client: AsyncClient, db_session: AsyncSession):

    # Use the user ID already attached to auth_client by conftest
    user_id = auth_client.user_id

    # Seed the reset token in DB for this existing user
    raw_token = "valid_reset_token_123"
    token_record = models.PasswordResetToken(
        user_id=user_id,
        token_hash=hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(token_record)
    await db_session.commit()

    # Execute password reset
    response = await auth_client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": "NewPassword123!"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert "reset successfully" in response.json()["message"].lower()

    #  Verify user password was updated in DB
    user = await crud.get_user_by_id(db_session, user_id)
    assert user is not None
    assert verify_password("NewPassword123!", user.password_hash)

    #  Verify reset token was deleted from DB (single-use enforcement)
    token_query = await db_session.execute(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == user_id
        )
    )
    assert token_query.scalars().first() is None


# Attempt reset with non-existent / invalid token
@pytest.mark.anyio
async def test_reset_password_invalid_token(client: AsyncClient):

    response = await client.post(
        "/api/auth/reset-password",
        json={"token": "unrecognized_token_xyz", "new_password": "NewPassword123!"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid or expired reset token"


# Verify that an expired token returns 400 Bad Request and gets purged from DB
@pytest.mark.anyio
async def test_reset_password_expired_token(
    auth_client: AsyncClient, db_session: AsyncSession, client: AsyncClient):

    # Use the user ID already attached to auth_client by conftest
    user_id = auth_client.user_id

    raw_token = "expired_token_456"

    # Create token with expires_at in the past
    token_record = models.PasswordResetToken(
        user_id=user_id,
        token_hash=hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(token_record)
    await db_session.commit()

    # Execute reset with expired token
    response = await client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "new_password": "NewPassword123!"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid or expired reset token"

    # Verify stale token was cleaned up from the DB
    token_query = await db_session.execute(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == user_id
        )
    )
    assert token_query.scalars().first() is None



# testing PATCH "/me/password" endpoint
# Success
@pytest.mark.anyio
async def test_change_password_me_success(auth_client: AsyncClient, db_session: AsyncSession):
    response = await auth_client.patch(
        "/api/auth/me/password",
        json={"current_password": "testpassword123",
              "new_password": "newpassword123"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "Password changed successfully"}

    # Confirm updated hash in database
    result = await db_session.execute(
        select(models.User).where(models.User.id == auth_client.user_id)
    )
    user = result.scalars().first()
    assert verify_password("newpassword123", user.password_hash)

# Attempt change with incorrect current password (400 Bad Request)
@pytest.mark.anyio
async def test_change_password_me_fail(auth_client: AsyncClient):
    response = await auth_client.patch(
        "/api/auth/me/password",
        json={"current_password": "wrongpassword",
              "new_password": "newpassword123"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Current password is incorrect"

# Attempt change with identical new password
@pytest.mark.anyio
async def test_change_password_me_identical_password(auth_client: AsyncClient):
    response = await auth_client.patch(
        "/api/auth/me/password",
        json={"current_password": "testpassword123",
              "new_password": "testpassword123"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "New password cannot be the same as current password"

# Unauthenticated user request
@pytest.mark.anyio
async def test_change_password_me_unauthenticated(client: AsyncClient):
    response = await client.patch(
        "/api/auth/me/password",
        json={"current_password": "testpassword123",
              "new_password": "newpassword123"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Not authenticated"
