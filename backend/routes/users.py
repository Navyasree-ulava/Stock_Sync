"""
routes/users.py — Authenticated user profile endpoints.
"""

from fastapi import APIRouter
from auth.dependencies import CurrentUser

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def get_me(current_user: CurrentUser):
    """Return basic profile info for the authenticated user."""
    return {
        "id": current_user["id"],
        "full_name": current_user["full_name"],
        "email": current_user["email"],
        "business_name": current_user["business_name"],
    }
