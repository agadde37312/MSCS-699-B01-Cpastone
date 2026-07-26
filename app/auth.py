"""
Authentication and authorization.

Design choices (see docs/Security_Performance_Report.docx for the full
write-up with citations):
- Passwords are hashed with bcrypt via passlib, never stored or logged in
  plain text (OWASP Foundation, 2023).
- Access is granted via short-lived JWT bearer tokens (Jones et al., 2015),
  carrying the user id and role as claims.
- Role checks are enforced with FastAPI dependencies (require_role), so
  every protected endpoint declares its allowed roles explicitly rather
  than relying on ad hoc checks scattered through the code.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.logging_config import access_logger, error_logger

SECRET_KEY = os.environ.get("HEALTHTRACK_SECRET_KEY", "dev-only-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Note: we call the `bcrypt` library directly rather than going through
# passlib's CryptContext. passlib is effectively unmaintained and its
# bcrypt backend-detection self-test breaks on bcrypt>=4.1 (raises
# ValueError during import-time capability checks), so calling bcrypt
# directly is both simpler and more reliable here.
_BCRYPT_MAX_BYTES = 72  # bcrypt silently ignores bytes beyond this; we hash the SHA-256 digest instead


def hash_password(plain_password: str) -> str:
    password_bytes = _prepare_password_bytes(plain_password)
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = _prepare_password_bytes(plain_password)
    try:
        return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
    except ValueError:
        return False


def _prepare_password_bytes(plain_password: str) -> bytes:
    """bcrypt only uses the first 72 bytes of its input. Rather than
    silently truncating (and creating a subtle security gap where two
    different long passwords sharing a 72-byte prefix would be treated as
    identical), we pre-hash long passwords with SHA-256 first so the full
    password always contributes to the final bcrypt hash."""
    raw = plain_password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        import hashlib
        raw = hashlib.sha256(raw).hexdigest().encode("utf-8")
    return raw


def create_access_token(user_id: str, role: str) -> tuple[str, int]:
    expire_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.now(timezone.utc) + expire_delta
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token, int(expire_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        error_logger.warning(f"Token decode failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_exception

    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    user = db.get(models.User, user_id)
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_role(*allowed_roles: models.UserRole):
    """FastAPI dependency factory: use as
    Depends(require_role(UserRole.clinician, UserRole.admin))
    to restrict an endpoint to specific roles."""

    def _check(current_user: models.User = Depends(get_current_user)) -> models.User:
        if current_user.role not in allowed_roles:
            access_logger.warning(
                f"Forbidden: user {current_user.email} (role={current_user.role}) "
                f"attempted an action requiring {[r.value for r in allowed_roles]}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return current_user

    return _check
