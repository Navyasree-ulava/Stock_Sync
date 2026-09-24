"""
auth/dependencies.py — FastAPI dependency for authenticated user injection.

Replaces the old get_current_user which had a DEMO_TOKEN bypass.
Every protected route uses: current_user: dict = Depends(get_current_user)
"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from auth.security import decode_access_token
from db.connection import get_db
from db.models import User

log = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Validate Bearer token and return the authenticated user dict.

    No backdoors. No DEMO_TOKEN. No exceptions.
    If the token is invalid or expired, 401 is raised.
    If the user no longer exists, 401 is raised.
    """
    payload = decode_access_token(token)
    user_id = int(payload["sub"])

    user = db.query(User).filter(User.id == user_id).first()

    if user is None:
        log.warning(f"[AUTH] Token valid but user_id={user_id} not found in DB")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled.",
        )

    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "business_name": user.business_name,
        "is_active": user.is_active
    }


# Convenience alias for use in route signatures
CurrentUser = Annotated[dict, Depends(get_current_user)]

