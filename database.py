# Database connection & engine setup
from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from config import settings


# Create the asynchronous engine using our URL from .env
engine = create_async_engine(settings.database_url)

#  async_sessionmaker for session creation
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    pass
# Create a Dependency injector to manage database session lifetimes
# This yields a connection session to a route, and automatically closes it when done
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# Define reusable type a FastAPI dependency alias
DbSession = Annotated[AsyncSession, Depends(get_db)]