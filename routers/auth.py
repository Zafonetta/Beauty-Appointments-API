from datetime import timedelta, datetime, UTC, timezone
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
import crud
import schemas
from config import settings
from database import DbSession
from email_utils import send_password_reset_email
from security import create_access_token, verify_password, OAuth2Form, generate_reset_token, hash_reset_token, \
    hash_password, CurrentUser

router = APIRouter()

# Admin Login Endpoint
@router.post("/admin/token", response_model=schemas.Token)
async def admin_login(db:DbSession, form_data: OAuth2Form):
    admin = await crud.get_admin_by_email(db=db, email=form_data.username)

    if not admin or not verify_password(form_data.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(admin.id),
                "role": "admin", # Explicitly marks this token as an admin token
              },
        expires_delta=access_token_expires,
    )

    return {"access_token": access_token, "token_type": "bearer"}



# User Login Endpoint
@router.post("/token", response_model=schemas.Token)
async def login_for_access_token(db: DbSession,form_data: OAuth2Form,):
    # form_data.username holds whatever string was entered in Swagger's username box (email)
    user = await crud.get_user_by_email(db=db, email=form_data.username)

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id),
              "role": "user", },
        expires_delta=access_token_expires,
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
        request_data: schemas.ForgotPasswordRequest,
        background_tasks: BackgroundTasks,
        db: DbSession,
):
    user = await crud.get_user_by_email(db=db, email=request_data.email)

    if user:
        # Delete old tokens
        await crud.delete_existing_token(db=db, user_id=user.id)
        # Generate new token details
        token = generate_reset_token()
        token_hash = hash_reset_token(token)
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.reset_token_expire_minutes
        )
        # Save new token to DB
        await crud.create_reset_token(
            db=db,
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        # Schedule background email (use unhashed 'token' here!)
        # Hand off the heavy SMTP email transmission to FastAPI's background workers
        background_tasks.add_task(
            send_password_reset_email,
            to_email=user.email,
            username=user.username,
            token=token,
        )

    # Always return 202 Accepted without leaking if the user existed
    return {"message": "If the email exists, a reset link has been sent."}



@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request_data: schemas.ResetPasswordRequest,
    db: DbSession,
):
    # Hash the incoming raw token to match against the stored database hash
    token_hash = hash_reset_token(request_data.token)

    # Fetch token
    reset_token = await crud.get_reset_token(db=db, token_hash=token_hash)
    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check expiration
    if reset_token.expires_at < datetime.now(timezone.utc):
        await crud.delete_token(db=db, reset_token=reset_token)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Fetch user
    user = await crud.get_user_by_id(db=db, user_id=reset_token.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Update password & consume token
    user.password_hash = hash_password(request_data.new_password)
    await crud.delete_token(db=db, reset_token=reset_token)

    # Commit both updates atomically
    await db.commit()

    return {"message": "Password has been reset successfully. You can now log in with your new password."}


@router.patch("/me/password", status_code=status.HTTP_200_OK)
async def change_password(
        password_data: schemas.ChangePasswordRequest,
        current_user: CurrentUser,
        db: DbSession,
):
    # Verify that the user actually knows their current password
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    # Prevent setting new password to the same current password
    if verify_password(password_data.new_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password cannot be the same as current password",
        )

    # Re-hash the newly provided plain-text password securely
    current_user.password_hash = hash_password(password_data.new_password)

    # Clean house: invalidate any outstanding password reset tokens for security
    await crud.delete_existing_token(db=db, user_id=current_user.id)

    # Save updates atomically to the database
    await db.commit()

    return {"message": "Password changed successfully"}