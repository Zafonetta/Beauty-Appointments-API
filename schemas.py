# Pydantic validation (UserBase, ServiceBase, etc.)
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator


class Token(BaseModel):
    access_token: str #digital ID card
    token_type: str #how to use the token. set to "bearer".


# USER SCHEMAS
#The Blueprint (Shared by everyone, but never used alone)
class UserBase(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email: EmailStr = Field(max_length=120)
    phone: str | None = Field(default=None, min_length=1, max_length=20)

# Registration (Request Body)
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=50)

class UserPublic(BaseModel):
    id: int
    username: str

    model_config = ConfigDict(from_attributes=True)



class UserPrivate(UserPublic):
    email: EmailStr
    phone: str | None = None

class UserResponse(UserBase):
    id: int

    # Allows Pydantic to read data directly from SQLAlchemy database models
    model_config = ConfigDict(from_attributes=True)

class UserUpdate(BaseModel):
    username: str | None = Field(default = None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default = None, max_length=120)
    phone: str | None = Field(default=None, max_length=20)


class PaginatedUsers(BaseModel):
    users: list[UserResponse]
    total: int
    skip: int
    limit: int
    has_more: bool #tells the frontend application whether there is more data left to fetch from the database


# SERVICE SCHEMAS
class ServiceBase(BaseModel):

    name: str = Field(min_length=2, max_length=50)
    description: str | None = None
    duration: int= Field(gt=0, description="Duration in minutes")
    price: int= Field(gt=0, description="Price in currency")


class ServiceCreate(ServiceBase):
    pass  # Used when creating a new salon service


class ServiceResponse(ServiceBase):
    id: int
    admin_id: int  # Returned in response, pulled from database

    model_config = ConfigDict(from_attributes=True)

class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    duration: int | None = None
    price: int | None = None

# Used for GET responses (Output)
class ServiceClientView(ServiceBase):
    model_config = ConfigDict(from_attributes=True)

# Schema for paginated services response
class PaginatedServices(BaseModel):
    services: list[ServiceResponse]
    total: int
    skip: int
    limit: int
    has_more: bool

# --- APPOINTMENT SCHEMAS ---
class AppointmentBase(BaseModel):
    service_id: int
    start_time: datetime


class AppointmentCreate(AppointmentBase):
    service_id: int
    start_time: datetime
    # fields for unregistered guests
    client_name: str = Field(min_length=2, max_length=50)
    client_phone: str| None = Field(default=None, min_length=10, max_length=20)
    client_email: EmailStr | None = Field(max_length=120)

    @field_validator("client_phone", "client_name")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or only whitespace")
        return cleaned

# Schema for an individual appointment
class AppointmentResponse(AppointmentBase):
    id: int
    user_id: int
    status: str = "booked"
    client: UserResponse
    service: ServiceResponse
    start_time: datetime
    end_time: datetime
    guest_token: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AppointmentClientView(BaseModel):
    id: int  # Kept so the client can reference or cancel their appointment
    start_time: datetime
    service: ServiceClientView

    model_config = ConfigDict(from_attributes=True)

class AppointmentUpdate(BaseModel):
    start_time: datetime | None = None
    service_id: int | None = None


# Schema for paginated appointments response
class PaginatedAppointments(BaseModel):
    appointments: list[AppointmentClientView]
    total: int
    skip: int
    limit: int
    has_more: bool

class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(max_length=120)

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)





