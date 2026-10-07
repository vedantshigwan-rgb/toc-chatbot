from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth.dependencies import get_current_user
from config.settings import (
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES,
    OTP_EXPIRY_SECONDS,
    OTP_MAX_ATTEMPTS,
)
from services.auth_security import (
    create_access_token,
    generate_otp,
    hash_otp,
    hash_password,
    normalize_email,
    verify_otp,
    verify_password,
)
from services.email_service import (
    EmailDeliveryError,
    send_registration_otp,
)


router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: Optional[str] = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        return cleaned or None


class VerifyRegistrationRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., pattern=r"^\d{6}$")


class ResendOtpRequest(BaseModel):
    email: EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


def _auth_store(request: Request):
    store = getattr(request.app.state, "auth_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Authentication service is unavailable.",
        )
    return store


async def _issue_registration_otp(
    *,
    request: Request,
    email: str,
) -> None:
    store = _auth_store(request)
    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=OTP_EXPIRY_SECONDS
    )

    updated = await store.set_registration_otp(
        email=email,
        otp_hash=hash_otp(email=email, otp=otp),
        otp_expires_at=expires_at,
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Pending registration was not found.",
        )

    try:
        await send_registration_otp(email=email, otp=otp)
    except EmailDeliveryError as error:
        raise HTTPException(
            status_code=502,
            detail="Verification email could not be sent.",
        ) from error


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(
    payload: RegisterRequest,
    request: Request,
):
    store = _auth_store(request)
    email = normalize_email(str(payload.email))

    existing = await store.get_user(email=email)
    if existing is not None and existing.get("is_verified") is True:
        raise HTTPException(
            status_code=409,
            detail="An account already exists for this email.",
        )

    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=OTP_EXPIRY_SECONDS
    )

    await store.save_pending_registration(
        email=email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        otp_hash=hash_otp(email=email, otp=otp),
        otp_expires_at=expires_at,
    )

    try:
        await send_registration_otp(email=email, otp=otp)
    except EmailDeliveryError as error:
        raise HTTPException(
            status_code=502,
            detail="Registration was created, but the verification email could not be sent. Use /auth/register/resend-otp after checking SMTP configuration.",
        ) from error

    return {
        "status": "otp_sent",
        "message": "Registration OTP sent to the supplied email address.",
        "otp_expires_in": OTP_EXPIRY_SECONDS,
    }


@router.post("/register/verify")
async def verify_registration(
    payload: VerifyRegistrationRequest,
    request: Request,
):
    store = _auth_store(request)
    email = normalize_email(str(payload.email))
    user = await store.get_user(email=email)

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Pending registration was not found.",
        )

    if user.get("is_verified") is True:
        return {
            "status": "success",
            "message": "Email is already verified.",
        }

    attempts = int(user.get("registration_otp_attempts") or 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many invalid OTP attempts. Request a new OTP.",
        )

    expires_at = user.get("registration_otp_expires_at")
    now = datetime.now(timezone.utc)
    if expires_at is not None and expires_at.tzinfo is None:
        # PyMongo returns naive UTC datetimes unless tz_aware=True.
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at is None or expires_at <= now:
        raise HTTPException(
            status_code=400,
            detail="OTP has expired. Request a new OTP.",
        )

    valid = verify_otp(
        email=email,
        otp=payload.otp,
        encoded_hash=str(user.get("registration_otp_hash") or ""),
    )
    if not valid:
        await store.increment_otp_attempts(email=email)
        raise HTTPException(
            status_code=400,
            detail="Invalid OTP.",
        )

    updated = await store.mark_verified(email=email)
    if not updated:
        raise HTTPException(
            status_code=409,
            detail="Registration could not be verified.",
        )

    return {
        "status": "success",
        "message": "Registration verified. You can now log in.",
    }


@router.post("/register/resend-otp", status_code=status.HTTP_202_ACCEPTED)
async def resend_registration_otp(
    payload: ResendOtpRequest,
    request: Request,
):
    store = _auth_store(request)
    email = normalize_email(str(payload.email))
    user = await store.get_user(email=email)

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Pending registration was not found.",
        )

    if user.get("is_verified") is True:
        raise HTTPException(
            status_code=409,
            detail="This email is already verified.",
        )

    await _issue_registration_otp(
        request=request,
        email=email,
    )

    return {
        "status": "otp_sent",
        "message": "A new registration OTP was sent.",
        "otp_expires_in": OTP_EXPIRY_SECONDS,
    }


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
):
    store = _auth_store(request)
    email = normalize_email(str(payload.email))
    user = await store.get_user(email=email)

    if user is None or not verify_password(
        payload.password,
        str(user.get("password_hash") or ""),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.get("is_verified") is not True:
        raise HTTPException(
            status_code=403,
            detail="Email verification is required before login.",
        )

    if user.get("is_active") is not True:
        raise HTTPException(
            status_code=403,
            detail="This account is inactive.",
        )

    access_token = create_access_token(email=email)

    return {
        "status": "success",
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": AUTH_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "email": email,
            "name": user.get("name"),
        },
    }


@router.get("/me")
async def me(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    return {
        "status": "success",
        "user": current_user,
    }
