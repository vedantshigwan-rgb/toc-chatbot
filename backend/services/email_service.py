from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from config.settings import (
    MAIL_DEFAULT_SENDER,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_SERVER,
    MAIL_USERNAME,
    MAIL_USE_TLS,
    OTP_EXPIRY_SECONDS,
)


class EmailDeliveryError(RuntimeError):
    pass


def _send_registration_otp_sync(*, email: str, otp: str) -> None:
    required = {
        "MAIL_SERVER": MAIL_SERVER,
        "MAIL_USERNAME": MAIL_USERNAME,
        "MAIL_PASSWORD": MAIL_PASSWORD,
        "MAIL_DEFAULT_SENDER": MAIL_DEFAULT_SENDER,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise EmailDeliveryError(
            "Email configuration is incomplete: " + ", ".join(missing)
        )

    expiry_minutes = max(1, OTP_EXPIRY_SECONDS // 60)

    message = EmailMessage()
    message["From"] = MAIL_DEFAULT_SENDER
    message["To"] = email
    message["Subject"] = "TOC registration verification code"
    message.set_content(
        "Your TOC registration verification code is "
        f"{otp}.\n\n"
        f"This code expires in {expiry_minutes} minutes.\n"
        "If you did not request this registration, you can ignore this email."
    )

    try:
        with smtplib.SMTP(
            MAIL_SERVER,
            MAIL_PORT,
            timeout=20,
        ) as smtp:
            smtp.ehlo()
            if MAIL_USE_TLS:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
            smtp.send_message(message)
    except Exception as error:
        raise EmailDeliveryError(
            "The verification email could not be sent."
        ) from error


async def send_registration_otp(*, email: str, otp: str) -> None:
    await asyncio.to_thread(
        _send_registration_otp_sync,
        email=email,
        otp=otp,
    )
