from typing import Annotated
from fastapi import HTTPException, status, APIRouter, Query
import crud
import schemas
from config import settings
from database import DbSession
from security import CurrentUser, CurrentAdmin

router = APIRouter()

# POST Endpoint: Create a new user
@router.post("",response_model=schemas.UserPrivate,status_code=status.HTTP_201_CREATED)
async def register_user(user: schemas.UserCreate,db: DbSession):
    # Check if the username already exists
    existing_user = await crud.get_user_by_username(db, username=user.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this username already exists",
        )
    #  Check if user with this email already exists
    existing_user = await crud.get_user_by_email(db, email=user.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered"
        )
    # Check phone (only if provided)
    if user.phone and await crud.get_user_by_phone(db, phone=user.phone):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone is already registered"
        )

    #  Perform the creation via CRUD function
    return await crud.create_user(db=db, user=user)

# GET /me - Retrieve profile details for the logged-in user
@router.get("/me", response_model=schemas.UserPrivate)
async def get_current_user(current_user: CurrentUser):
    """
        Fetch current user profile.
        Requires a valid JWT Bearer token in the Authorization header.
        Returns the authenticated user's private profile details.
    """
    return current_user


# DELETE Endpoint: user deletes their own active profile
@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_own_profile(
    db: DbSession,
    current_user: CurrentUser,
):
    await crud.delete_profile(db=db, user=current_user)
    return None


# GET Endpoint: Fetches paginated users (Admin Only)
@router.get("", response_model=schemas.PaginatedUsers)
async def get_all_users_admin(
        db: DbSession,
        current_admin: CurrentAdmin,
        skip: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = settings.users_per_page,
        q: Annotated[str | None, Query(description="Search username, email, or phone")] = None,
        is_active: Annotated[bool | None, Query()] = None,
):
    users, total = await crud.get_users(
        db=db,
        skip=skip,
        limit=limit,
        q=q,
        is_active=is_active,)

    return {
        "users": users,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + len(users)) < total,
    }


# PATCH Endpoint: Partially updates specific user attributes
@router.patch("/{user_id}", response_model=schemas.UserPrivate)
async def update_user(
        user_id: int,
        db: DbSession,
        current_user: CurrentUser,
        user_update: schemas.UserUpdate,
):
       # Authorization: Allow self-updates
        if user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden.Not authorized to update this user",
        )

        # Fetch target user if Admin is updating another account
        target_user = current_user if user_id == current_user.id else await crud.get_user_by_id(db, user_id=user_id)
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Check for Username collision (if changed)
        if (
                user_update.username is not None
                and user_update.username.lower() != current_user.username.lower()
        ):
            existing_user = await crud.get_user_by_username(
                db, user_update.username
            )
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="User with this username already exists",
                )

        # Check for Email collision (if changed)
        if (
                user_update.email is not None
                and user_update.email.lower() != current_user.email.lower()
        ):
            existing_email = await crud.get_user_by_email(db, user_update.email)
            if existing_email:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="User with this email already exists",
                )
        # Check for Phone collision (if changed)
        if (
            user_update.phone is not None
            and user_update.phone != current_user.phone
        ):
            existing_phone = await crud.get_user_by_phone(db, user_update.phone)
            if existing_phone:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="User with this phone already exists",
                )

        # Perform update & return fresh record
        updated_user = await crud.partial_user_update(
            db=db, user=current_user, user_update=user_update
        )
        return updated_user



# DELETE Endpoint: admin can delete users' profile
@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_admin(
        user_id: int,
        db: DbSession,
        current_admin: CurrentAdmin,
):
        target_user = await crud.get_user_by_id(db, user_id=user_id)
        if not target_user:
                raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Delete using the already-loaded current_user object
        await crud.delete_profile(db=db, user=target_user)

        # HTTP 204 must return None (no body)
        return None


