import os
import uuid
from collections.abc import AsyncGenerator
import sys
from datetime import datetime, timezone, timedelta

import pytest
import asyncio

import models
from security import password_hash, create_access_token, get_current_admin

#  Windows Selector Policy (Must run BEFORE event loop creation)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

#Environment Isolation for Testing
os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://postgres:6925koza@localhost:5432/beauty_db_test"
)
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from database import Base, get_db
from main import app


# automatically load the anyio plugin before it starts collecting or running our tests.
pytest_plugins = ["anyio"]

#Handling Asynchronous Tests
@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture(scope="session")
def test_engine():
    # Creates an async database engine using the URL defined in our environment variables.
    # Use NullPool to ensure each test gets a fresh, isolated connection.
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        poolclass=NullPool,
    )
    return engine


@pytest.fixture(scope="session")
async def setup_database(test_engine):
    # SETUP: Create all tables before the tests run
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # This 'yield' pauses the fixture, allowing tests to run while tables exist.
    yield
    # TEARDOWN: Drop all tables after the tests finish
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # Close the engine connection completely
    await test_engine.dispose()

@pytest.fixture
async def db_session(
    test_engine,
    setup_database,
) -> AsyncGenerator[AsyncSession]:
    # 1. Connect to the database and start a transaction
    conn = await test_engine.connect()
    trans = await conn.begin()

    # 2. Configure a temporary session that uses the transaction we just started
    test_async_session = async_sessionmaker(
        bind=conn,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint", # Allows nested transactions
    )

    async with test_async_session() as session:
        try:
            # 3. Provide the session to our test (the 'yield')
            yield session
        finally:
            # 4. TEARDOWN: Always close the session and rollback the transaction
            # This ensures no data is actually saved to our test database
            await session.close()
            await trans.rollback()
            await conn.close()



# Standalone fixture that creates and returns the Admin record
@pytest.fixture
async def admin_user(db_session: AsyncSession) -> models.Admin:
    test_admin = models.Admin(
        email="admin@example.com",
        password_hash=password_hash.hash("admin123")
    )
    db_session.add(test_admin)
    await db_session.commit()
    await db_session.refresh(test_admin)
    return test_admin


@pytest.fixture
async def admin_client(client: AsyncClient, admin_user: models.Admin):
    from main import app
    from security import get_current_admin, password_hash
    from models import Admin

     # Override dependency to return this Admin instance
    app.dependency_overrides[get_current_admin] = lambda: admin_user
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def test_service(
    db_session: AsyncSession,
    admin_user: models.Admin  # Inject our existing admin fixture here
) -> models.Service:
    service = models.Service(
        name="Manicure",
        duration=90,
        price=35,
        admin_id=admin_user.id,  # Link directly to the shared admin fixture
    )
    db_session.add(service)
    await db_session.commit()
    await db_session.refresh(service)
    return service


@pytest.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """Fixture providing an HTTP client pre-authenticated with a registered test user."""
    user = await create_test_user(client)
    token = await login_user(client)
    client.headers.update(auth_header(token))

    # Store user payload on the client instance
    client.user_id = user["id"]
    return client


@pytest.fixture
async def other_auth_client(client: AsyncClient, db_session: AsyncSession) -> AsyncClient:
    from security import create_access_token, password_hash

    # Seed dummy user (receives User ID = 1)
    dummy = models.User(
        username="dummy",
        email="dummy@example.com",
        password_hash=password_hash.hash("pass"),
        phone="dummy",
    )
    db_session.add(dummy)
    await db_session.commit()

    # Create a second regular user in PostgreSQL (receives User ID = 2)
    user2 = models.User(
        username="user2",
        email="client2@example.com",
        password_hash=password_hash.hash("user123"),
        phone="9876543210"
    )
    db_session.add(user2)
    await db_session.commit()
    await db_session.refresh(user2)

    # Generate token for User #2
    token = create_access_token(data={"sub": str(user2.id)})
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
async def guest_client(
    client: AsyncClient,
    db_session: AsyncSession,
    test_service: models.Service
) -> AsyncClient:
    # Create a mandatory user record first
    user = models.User(
        username="guest_owner",
        email="guest_owner@example.com",
        phone="3288088807",
        password_hash="hashed_pass",
    )
    db_session.add(user)
    await db_session.commit()

    # Assign user.id to the appointment
    token = uuid.uuid4().hex
    now = datetime.now(timezone.utc)

    # Seed the guest appointment in PostgreSQL
    appointment = models.Appointment(
        guest_token=token,
        user_id=user.id,  # <-- Satisfies PostgreSQL NOT NULL constraint
        service_id=test_service.id,
        guest_name="Jane Guest",
        guest_email="jane@example.com",
        guest_phone="3288088807",
        start_time=now,
        end_time=now + timedelta(hours=1),
    )
    db_session.add(appointment)
    await db_session.commit()

    # Store metadata directly on the AsyncClient instance
    client.guest_token = token
    client.appointment = appointment
    return client


# A "clean slate" HTTP client connected to FastAPI app
@pytest.fixture
async def client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient]:
    # 1. Dependency Override: Replace the real database dependency with our test session
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # 2. Create the AsyncClient to simulate HTTP requests to your FastAPI app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    # 3. Cleanup: Remove the overrides so other tests are not affected
    app.dependency_overrides.clear()



# Helpers & Auth Fixtures
async def create_test_user(
    client: AsyncClient,
    username: str = "testuser",
    email: str = "test@example.com",
    password: str = "testpassword123",
    phone: str = "0123456789",
) -> dict:
    # Sends a POST request to register the user
    response = await client.post(
        "/api/users",
        json={
            "username": username,
            "email": email,
            "password": password,
            "phone": phone,
        },
    )
    # Asserts that the API successfully created the resource (201 Created)
    assert response.status_code == 201, f"Failed to create user: {response.text}"
    return response.json()


async def login_user(
    client: AsyncClient,
    email: str = "test@example.com",
    password: str = "testpassword123",
) -> str:
    # Sends a POST request to the token endpoint to get an access token
    response = await client.post(
        "/api/auth/token",
        data={
            "username": email,
            "password": password,
        },
    )
    # Asserts that the login was successful (200 OK)
    assert response.status_code == 200, f"Failed to login: {response.text}"
    # Returns only the token string to the caller
    return response.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    # Formats the token into the standard "Bearer" format used in HTTP headers
    return {"Authorization": f"Bearer {token}"}

