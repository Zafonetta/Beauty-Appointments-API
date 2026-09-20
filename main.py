# FastAPI entry point & routes
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import models  # noqa: F401
from database import engine, get_db
from routers import users, auth, services, appointments


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
# Cleanly close all connections in the connection pool and free up resources
    await engine.dispose()

app = FastAPI(
    title="Beauty Appointments REST API",
    description=(
        "A robust, asynchronous REST API for an appointments platform featuring "
        "JWT authentication, role/user management, CRUD operations, "
        "and secure password reset flows via email."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

# Enable CORS for cross-origin frontend clients and mobile apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(users.router, prefix= "/api/users", tags=["Users"])
app.include_router(auth.router, prefix= "/api/auth", tags=["Token"])
app.include_router(services.router, prefix="/api/services", tags=["Services"])
app.include_router(appointments.router, prefix="/api/appointments", tags=["Appointments"])

#Health check probe for monitoring tools and deployment platforms.
#Verifies that the database connection is alive and responding
@app.get("/health", tags=["Health check"])
async def health_check(db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", reload=True)