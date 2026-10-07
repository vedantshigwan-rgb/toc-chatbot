from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from services.auth_security import (
    AuthTokenError,
    decode_access_token,
)


bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Authentication required.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        bearer_scheme
    ),
) -> Dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    try:
        claims = decode_access_token(credentials.credentials)
    except AuthTokenError as error:
        raise _unauthorized(str(error)) from error

    auth_store = getattr(
        request.app.state,
        "auth_store",
        None,
    )
    if auth_store is None:
        raise HTTPException(
            status_code=503,
            detail="Authentication service is unavailable.",
        )

    user = await auth_store.get_verified_user(
        email=claims["sub"],
    )
    if user is None:
        raise _unauthorized("Authenticated user is unavailable.")

    safe_user = {
        "email": user["email"],
        "name": user.get("name"),
        "is_verified": bool(user.get("is_verified")),
        "is_active": bool(user.get("is_active")),
    }

    request.state.current_user = safe_user
    return safe_user


def current_user_email(request: Request) -> str:
    user = getattr(request.state, "current_user", None)
    if not user or not user.get("email"):
        raise _unauthorized()
    return str(user["email"])
