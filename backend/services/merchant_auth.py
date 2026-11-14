"""Merchant account and session authentication.

This module owns credential handling. Passwords use stdlib scrypt with a
per-account random salt. Bearer tokens are returned once and only their
SHA-256 digest is persisted, so a database read cannot reveal live sessions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

from backend.config import get_settings
from backend.db import connect
from backend.models import (
    MerchantAccount,
    MerchantAuthResponse,
    MerchantLoginRequest,
    MerchantRegisterRequest,
)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_DUMMY_SALT = b"checkoutpilot-auth-dummy-salt"
_DUMMY_HASH = hashlib.scrypt(
    b"not-a-real-password",
    salt=_DUMMY_SALT,
    n=_SCRYPT_N,
    r=_SCRYPT_R,
    p=_SCRYPT_P,
    dklen=_SCRYPT_DKLEN,
)


class MerchantEmailExistsError(ValueError):
    pass


class MerchantAuthenticationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded.encode("ascii"))


def _password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )


def _hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    return _encode(_password_digest(password, salt)), _encode(salt)


def _verify_password(password: str, encoded_hash: str, encoded_salt: str) -> bool:
    try:
        expected = _decode(encoded_hash)
        salt = _decode(encoded_salt)
        actual = _password_digest(password, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _consume_dummy_hash(password: str) -> None:
    actual = _password_digest(password, _DUMMY_SALT)
    hmac.compare_digest(actual, _DUMMY_HASH)


def _slug_base(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")
    return slug[:48] or "merchant"


def _unique_slug(conn: sqlite3.Connection, name: str) -> str:
    base = _slug_base(name)
    candidate = base
    while conn.execute("SELECT 1 FROM merchants WHERE slug = ?", (candidate,)).fetchone():
        candidate = f"{base[:39]}-{secrets.token_hex(4)}"
    return candidate


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _account_from_row(row: sqlite3.Row) -> MerchantAccount:
    return MerchantAccount(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        email=row["email"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _create_session(conn: sqlite3.Connection, merchant: MerchantAccount) -> MerchantAuthResponse:
    now = _now()
    expires_at = now + timedelta(hours=get_settings().merchant_session_ttl_hours)
    token = f"mss_{secrets.token_urlsafe(32)}"
    conn.execute(
        """
        INSERT INTO merchant_sessions (
            token_hash, merchant_id, created_at, expires_at, last_used_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, NULL)
        """,
        (
            _token_hash(token),
            merchant.id,
            now.isoformat(),
            expires_at.isoformat(),
            now.isoformat(),
        ),
    )
    return MerchantAuthResponse(
        merchant=merchant,
        access_token=token,
        expires_at=expires_at,
    )


def register(body: MerchantRegisterRequest) -> MerchantAuthResponse:
    password_hash, password_salt = _hash_password(body.password)
    now = _now()
    merchant_id = f"mer_{uuid.uuid4().hex[:16]}"

    try:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            slug = _unique_slug(conn, body.name)
            conn.execute(
                """
                INSERT INTO merchants (
                    id, name, slug, email, password_hash, password_salt,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    merchant_id,
                    body.name,
                    slug,
                    body.email,
                    password_hash,
                    password_salt,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
            assert row is not None
            result = _create_session(conn, _account_from_row(row))
            conn.commit()
            return result
    except sqlite3.IntegrityError as exc:
        if "merchants.email" in str(exc) or "UNIQUE constraint failed: merchants.email" in str(exc):
            raise MerchantEmailExistsError("An account with this email already exists") from exc
        raise


def login(body: MerchantLoginRequest) -> MerchantAuthResponse:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM merchants WHERE email = ?",
            (body.email,),
        ).fetchone()
        if row is None:
            _consume_dummy_hash(body.password)
            raise MerchantAuthenticationError("Invalid email or password")
        if row["status"] != "active":
            _consume_dummy_hash(body.password)
            raise MerchantAuthenticationError("Invalid email or password")
        if not _verify_password(body.password, row["password_hash"], row["password_salt"]):
            raise MerchantAuthenticationError("Invalid email or password")

        result = _create_session(conn, _account_from_row(row))
        conn.commit()
        return result


def authenticate_token(token: str) -> MerchantAccount | None:
    if not token or not token.startswith("mss_"):
        return None
    now = _now()
    digest = _token_hash(token)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT m.*, s.expires_at, s.revoked_at
            FROM merchant_sessions AS s
            JOIN merchants AS m ON m.id = s.merchant_id
            WHERE s.token_hash = ?
            """,
            (digest,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None or row["status"] != "active":
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= now:
            conn.execute(
                "UPDATE merchant_sessions SET revoked_at = ? WHERE token_hash = ?",
                (now.isoformat(), digest),
            )
            conn.commit()
            return None
        conn.execute(
            "UPDATE merchant_sessions SET last_used_at = ? WHERE token_hash = ?",
            (now.isoformat(), digest),
        )
        conn.commit()
        return _account_from_row(row)


def revoke_token(token: str) -> bool:
    if not token:
        return False
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE merchant_sessions
            SET revoked_at = ?
            WHERE token_hash = ? AND revoked_at IS NULL
            """,
            (_now().isoformat(), _token_hash(token)),
        )
        conn.commit()
        return cursor.rowcount == 1


def revoke_all_sessions(merchant_id: str) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE merchant_sessions
            SET revoked_at = ?
            WHERE merchant_id = ? AND revoked_at IS NULL
            """,
            (_now().isoformat(), merchant_id),
        )
        conn.commit()
        return cursor.rowcount
