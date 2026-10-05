# Password hashing & JWT tokens
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, status, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pwdlib import PasswordHash
from sqlalchemy import select


from config import settings
import models
from database import DbSession


#Set up a secure hashing instance that automatically selects the best available cryptographic algorithm
password_hash = PasswordHash.recommended()

# TOKEN SCHEMES (For Swagger UI & Header Extraction)
oauth = OAuth2PasswordBearer(
    tokenUrl="/api/auth/token",
    scheme_name="UserAuth")
#for admin
oauth2 = OAuth2PasswordBearer(
    tokenUrl="/api/auth/admin/token",
    scheme_name="AdminAuth")

# FORM DATA ALIAS (Shared by both)
OAuth2Form = Annotated[OAuth2PasswordRequestForm, Depends()]

# Helper function to securely hash a plain-text password before saving it to the database
def hash_password(password: str) -> str:
    return password_hash.hash(password)

#This function takes the plain-text password the user typed into the login form and compares it against the secure, unreadable hash stored in our database
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)

def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)

def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

#Create a JWT access token
def create_access_token(data:dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy() #Create a shallow copy of the data dictionary to avoid modifying the original payload
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            minutes=settings.access_token_expire_minutes,
        )
    to_encode.update({"exp": expire})
#It takes the user's information (to_encode), encrypts it using our SECRET_KEY, and specifies the HS256 algorithm to sign the final string.
    encoded_jwt = jwt.encode(
            to_encode,
            settings.secret_key.get_secret_value(),
            algorithm=settings.algorithm,
        )

    return encoded_jwt

#Verify a JWT access token and return the subject (user id) if valid
def verify_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token, #By providing SECRET_KEY, the library checks if the token was truly signed by server
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm], #It enforces the use of the algorithm (e.g., HS256) defined in config.py
            options={"require": ["exp", "sub"]},
        )
#It forces the function to ensure the token has an expiration date and a "subject" (which, in our case, is the unique ID of the user).
    except jwt.InvalidTokenError:
        return None
    else:
        return payload.get("sub")

# Dependency to authenticate the incoming JWT token and retrieve the current logged-in user
async def get_current_user(
    token: Annotated[str, Depends(oauth)],
    db: DbSession,
) -> models.User:
    user_id = verify_access_token(token)
    if user_id is None: #means token is not valid or expire
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id_int = int(user_id) #JWT tokens store data as strings, the code tries to safely convert that string into an integer.
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(
        select(models.User).where(models.User.id == user_id_int),
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

# alias for current user
CurrentUser = Annotated[models.User, Depends(get_current_user)]

# Dependency to authenticate admin JWT tokens
async def get_current_admin(
    token: Annotated[str, Depends(oauth2)],
    db: DbSession,
) -> models.Admin:
    admin_id = verify_access_token(token)
    if admin_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        admin_id_int = int(admin_id)  # Fixed: using admin_id instead of user_id
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(
        select(models.Admin).where(models.Admin.id == admin_id_int)
    )
    admin = result.scalars().first()  # Fixed: assign directly to admin

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return admin

# Alias for current admin dependency injection
CurrentAdmin = Annotated[models.Admin, Depends(get_current_admin)]



