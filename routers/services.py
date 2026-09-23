from typing import Annotated
from fastapi import HTTPException, status, APIRouter, Query
import crud
import models
import schemas
from config import settings
from database import DbSession
from security import CurrentAdmin

# Initialize the router for services
router = APIRouter()

# POST Endpoint: Creates a new service
@router.post("",response_model=schemas.ServiceResponse,status_code=status.HTTP_201_CREATED)
async def create_service(
    service: schemas.ServiceCreate,
    current_admin: CurrentAdmin,
    db: DbSession,
):
    # Create the SQLAlchemy model instance
    new_service = models.Service(
        name=service.name,
        description=service.description,
        price=service.price,
        duration=service.duration,
        admin_id=current_admin.id,
    )

    db.add(new_service)
    await db.commit()
    await db.refresh(new_service, attribute_names=["admin"])

    return new_service

# GET Endpoint: list available services for clients
@router.get("",response_model=schemas.PaginatedServices)
async def get_all_services(
        db: DbSession,
        skip: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = settings.services_per_page,
):
    services, total = await crud.get_services(db=db, skip=skip, limit=limit)
    return {
        "services": services,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + len(services)) < total,
    }


# GET Endpoint: fetches service by its id
@router.get("/{service_id}",response_model=schemas.ServiceResponse)
async def get_service(
        db: DbSession,
        service_id: int,
):
    service = await crud.get_service_by_id(db=db, service_id=service_id)
    if service:
        return service
    raise HTTPException(status_code=404, detail="Service not found")


# PATCH Endpoint: update service detail
@router.patch("/{service_id}", response_model=schemas.ServiceResponse)
async def update_service(
    service_id: int,
    service_update: schemas.ServiceUpdate,
    current_admin: CurrentAdmin,
    db: DbSession,
):
    # fetch the existing service from the database
    service = await crud.get_service_by_id(db=db, service_id=service_id)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )
    # Authorization check:ensure this service belongs to the logged-in admin
    if service.admin_id != current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden.Not authorized to update this service",
        )
    # Perform update & return fresh record
    updated_service = await crud.partial_service_update(
            db=db, service_update=service_update, service=service,
    )
    return updated_service


# DELETE Endpoint: delete service
@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
        service_id: int,
        db: DbSession,
        current_admin: CurrentAdmin,
):
    # fetch the existing service from the database
    service = await crud.get_service_by_id(db=db, service_id=service_id)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )
    # Authorization check:ensure this service belongs to the logged-in admin
    if service.admin_id != current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden. Not authorized to delete this service",
        )
    # Perform delete
    await crud.delete_service(db=db, service=service)

    # HTTP 204 must return None (no body)
    return None

