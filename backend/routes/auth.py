"""
routes/auth.py — Registration, JSON login, and logout endpoints.

All authentication APIs accept JSON payloads. Password validation is enforced.
New user registration automatically seeds a standard set of default products.
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from auth.security import (
    hash_password,
    verify_password,
    validate_password_strength,
    create_access_token,
)
from auth.dependencies import CurrentUser
from db.connection import get_db
from db.models import User, Product
from seed_db import BUILTIN_PRODUCTS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


# ── Pydantic Models ───────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    business_name: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def full_name_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        if len(v) > 100:
            raise ValueError("Full name must be at most 100 characters.")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    business_name: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserOut


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    user_data: UserCreate,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Register a new user. Rate limited to 5 attempts/minute per IP.
    Automatically seeds a default catalog of products upon creation.
    """
    validate_password_strength(user_data.password)

    email_lower = user_data.email.lower()
    existing = db.query(User).filter(User.email == email_lower).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email is already registered.",
        )

    hashed = hash_password(user_data.password)
    new_user = User(
        full_name=user_data.full_name,
        email=email_lower,
        business_name=user_data.business_name,
        hashed_password=hashed,
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    log.info(f"[AUTH] New user registered: email='{new_user.email}' id={new_user.id}")

    # Seeding is disabled so that new accounts start with an empty dashboard
    log.info(f"[AUTH] Account created with empty inventory for user_id={new_user.id}")

    return {"message": "User created successfully"}


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: UserLogin,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Authenticate user via JSON email & password, and return a JWT.
    Rate limited to 10 attempts/minute per IP.
    """
    email_lower = body.email.strip().lower()
    user = db.query(User).filter(User.email == email_lower).first()

    # Use constant-time comparison helper via bcrypt verification
    if user is None or not verify_password(body.password, user.hashed_password):
        log.warning(f"[AUTH] Failed login attempt for email='{email_lower}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(user_id=user.id, email=user.email)
    log.info(f"[AUTH] Login successful: email='{user.email}' id={user.id}")
    
    user_out = UserOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        business_name=user.business_name
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_out
    }


@router.post("/logout")
async def logout(current_user: CurrentUser):
    """
    Log out the current user. Discards session token client-side.
    """
    log.info(f"[AUTH] User logged out: user_id={current_user['id']}")
    return {"message": "Logged out successfully"}
