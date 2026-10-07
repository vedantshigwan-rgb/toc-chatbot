from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MongoAuthStore:
    """Mongo persistence for basic email/password authentication."""

    def __init__(
        self,
        *,
        database,
        collection_name: str = "users",
    ) -> None:
        self.users = database[collection_name]

    async def initialize(self) -> None:
        await self.users.create_index(
            "email",
            unique=True,
        )
        await self.users.create_index(
            [
                ("is_verified", 1),
                ("is_active", 1),
            ]
        )

    async def get_user(self, *, email: str) -> Optional[Dict[str, Any]]:
        return await self.users.find_one(
            {"email": email},
        )

    async def get_verified_user(
        self,
        *,
        email: str,
    ) -> Optional[Dict[str, Any]]:
        return await self.users.find_one(
            {
                "email": email,
                "is_verified": True,
                "is_active": True,
            },
        )

    async def save_pending_registration(
        self,
        *,
        email: str,
        name: Optional[str],
        password_hash: str,
        otp_hash: str,
        otp_expires_at: datetime,
    ) -> None:
        now = utc_now()

        await self.users.update_one(
            {
                "email": email,
                "is_verified": {"$ne": True},
            },
            {
                "$set": {
                    "email": email,
                    "name": name,
                    "password_hash": password_hash,
                    "is_verified": False,
                    "is_active": True,
                    "registration_otp_hash": otp_hash,
                    "registration_otp_expires_at": otp_expires_at,
                    "registration_otp_attempts": 0,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )

    async def set_registration_otp(
        self,
        *,
        email: str,
        otp_hash: str,
        otp_expires_at: datetime,
    ) -> bool:
        result = await self.users.update_one(
            {
                "email": email,
                "is_verified": False,
            },
            {
                "$set": {
                    "registration_otp_hash": otp_hash,
                    "registration_otp_expires_at": otp_expires_at,
                    "registration_otp_attempts": 0,
                    "updated_at": utc_now(),
                }
            },
        )
        return result.modified_count == 1

    async def increment_otp_attempts(self, *, email: str) -> None:
        await self.users.update_one(
            {
                "email": email,
                "is_verified": False,
            },
            {
                "$inc": {"registration_otp_attempts": 1},
                "$set": {"updated_at": utc_now()},
            },
        )

    async def mark_verified(self, *, email: str) -> bool:
        now = utc_now()
        result = await self.users.update_one(
            {
                "email": email,
                "is_verified": False,
            },
            {
                "$set": {
                    "is_verified": True,
                    "verified_at": now,
                    "updated_at": now,
                },
                "$unset": {
                    "registration_otp_hash": "",
                    "registration_otp_expires_at": "",
                    "registration_otp_attempts": "",
                },
            },
        )
        return result.modified_count == 1
