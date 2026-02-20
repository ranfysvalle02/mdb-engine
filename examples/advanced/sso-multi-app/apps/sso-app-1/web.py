#!/usr/bin/env python3
"""
Password Generator App - SSO Enabled
====================================

Secure password generator and manager with SSO authentication.
Generates strong passwords and stores them encrypted per user.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import string
import sys
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from pymongo.errors import PyMongoError

from mdb_engine.dependencies import get_scoped_db

# Import shared security utilities


logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

APP_SLUG = "pwd-zero"
APP_SECRET = os.getenv("APP_ENCRYPTION_SECRET", "default-secret-change-in-production")

# `app` and `engine` are injected by create_multi_app before this module runs.

# Template engine
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================================
# HELPER FUNCTIONS - AUTH HUB URL
# ============================================================================


def get_auth_hub_url(request: Request = None) -> str:
    """
    Get auth hub URL. In multi-app mode, uses the path prefix from
    request.state.auth_hub_url (set by AppContextMiddleware).

    Priority:
    1. request.state.auth_hub_url (multi-app mode)
    2. manifest.auth.auth_hub_url (if mode is "shared")
    3. AUTH_HUB_URL environment variable
    4. Default: /auth-hub

    Returns:
        Auth hub URL string
    """
    # In multi-app mode, AppContextMiddleware sets this
    if request is not None:
        auth_url = getattr(request.state, "auth_hub_url", None)
        if auth_url:
            return auth_url

    # Try manifest
    manifest = getattr(app.state, "manifest", None) or getattr(
        app.state, "app_manifest", None
    )
    if manifest:
        auth_config = manifest.get("auth", {})
        if auth_config.get("mode") == "shared":
            auth_hub_url = auth_config.get("auth_hub_url")
            if auth_hub_url:
                return auth_hub_url

    return os.getenv("AUTH_HUB_URL", "/auth-hub")


# ============================================================================
# ENCRYPTION HELPERS
# ============================================================================


async def get_user_salt(db, user_email: str) -> bytes:
    """
    Get or create a per-user salt for encryption key derivation.
    
    Uses a separate collection to store per-user salts for enhanced security.
    """
    salt_doc = await db.user_salts.find_one({"user_email": user_email})
    if salt_doc:
        return salt_doc["salt"]
    
    # Generate and store new salt for this user
    salt = secrets.token_bytes(16)
    await db.user_salts.insert_one({
        "user_email": user_email,
        "salt": salt,
        "created_at": datetime.utcnow(),
    })
    return salt


async def get_user_encryption_key(db, user_email: str) -> bytes:
    """
    Derive a Fernet encryption key for a specific user using per-user salt.
    
    Uses PBKDF2 with per-user salt stored in database for enhanced security.
    """
    salt = await get_user_salt(db, user_email)
    
    # Use PBKDF2 to derive a key from user email + app secret with per-user salt
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,  # Per-user salt for enhanced security
        iterations=600000,  # Increased iterations for better security
    )
    key_material = f"{user_email}:{APP_SECRET}".encode()
    key = base64.urlsafe_b64encode(kdf.derive(key_material))
    return key


async def encrypt_password(db, password: str, user_email: str) -> str:
    """Encrypt a password using user-specific key with per-user salt."""
    key = await get_user_encryption_key(db, user_email)
    f = Fernet(key)
    return f.encrypt(password.encode()).decode()


async def decrypt_password(db, encrypted_password: str, user_email: str) -> str:
    """Decrypt a password using user-specific key with per-user salt."""
    key = await get_user_encryption_key(db, user_email)
    f = Fernet(key)
    return f.decrypt(encrypted_password.encode()).decode()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def get_current_user(request: Request) -> dict | None:
    """Get user from request.state (populated by SharedAuthMiddleware)."""
    return getattr(request.state, "user", None)


def get_roles(user: dict | None) -> list:
    """Get user's roles for this app."""
    if not user:
        return []
    app_roles = user.get("app_roles", {})
    return app_roles.get(APP_SLUG, [])


# ============================================================================
# ROUTES
# ============================================================================


@app.get("/login")
async def login_redirect(request: Request):
    """Redirect to auth hub login with redirect back to this app."""
    from urllib.parse import quote_plus

    base_url = f"{request.url.scheme}://{request.url.hostname}:{request.url.port}"
    callback_url = f"{base_url}/auth/callback"
    encoded_callback = quote_plus(callback_url)

    return RedirectResponse(
        url=f"{get_auth_hub_url(request)}/login?redirect_to={encoded_callback}", status_code=302
    )


# /auth/callback and /logout are auto-registered by the engine for shared-auth apps


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page - requires authentication."""
    user = get_current_user(request)
    if not user:
        # Redirect to auth hub login with callback URL to return here after login
        from urllib.parse import quote_plus

        base_url = f"{request.url.scheme}://{request.url.hostname}:{request.url.port}"
        callback_url = f"{base_url}/auth/callback"
        encoded_callback = quote_plus(callback_url)
        return RedirectResponse(
            url=f"{get_auth_hub_url(request)}/login?redirect_to={encoded_callback}", status_code=302
        )

    roles = get_roles(user)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "roles": roles,
            "app_name": "Password Generator",
            "app_description": "Generate and manage secure passwords",
            "auth_hub_url": get_auth_hub_url(request),
        },
    )


@app.get("/api/passwords")
async def get_passwords(request: Request, db=Depends(get_scoped_db)):
    """Get all passwords for the current user."""
    user = get_current_user(request)
    if not user:
        # For API routes, return 401 with redirect info for frontend to handle
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url(request)}/login",
        )

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(400, "User email not found")

    try:
        # Find passwords for this user
        passwords_cursor = db.passwords.find({"user_email": user_email})
        passwords = await passwords_cursor.to_list(length=1000)

        # Decrypt passwords
        decrypted_passwords = []
        for pwd in passwords:
            try:
                decrypted_passwords.append(
                    {
                        "id": str(pwd["_id"]),
                        "website": pwd.get("website", ""),
                        "username": pwd.get("username", ""),
                        "password": await decrypt_password(db, pwd["encrypted_password"], user_email),
                        "created_at": pwd.get("created_at", datetime.utcnow()).isoformat()
                        if isinstance(pwd.get("created_at"), datetime)
                        else str(pwd.get("created_at", "")),
                    }
                )
            except (ValueError, TypeError, AttributeError) as e:
                logger.warning(f"Failed to decrypt password {pwd.get('_id')}: {e}")
                continue

        return JSONResponse(
            {
                "success": True,
                "data": decrypted_passwords,
                "count": len(decrypted_passwords),
            }
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Error fetching passwords: {e}", exc_info=True)
        raise HTTPException(500, f"Error fetching passwords: {str(e)}") from e


@app.post("/api/passwords")
async def add_password(request: Request, db=Depends(get_scoped_db)):
    """Add a new password entry."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url(request)}/login",
        )

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(400, "User email not found")

    try:
        data = await request.json()
        website = data.get("website", "").strip()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not all([website, username, password]):
            raise HTTPException(400, "Website, username, and password are required")

        # Encrypt password
        encrypted_password = encrypt_password(password, user_email)

        # Store in database
        result = await db.passwords.insert_one(
            {
                "user_email": user_email,
                "website": website,
                "username": username,
                "encrypted_password": encrypted_password,
                "created_at": datetime.utcnow(),
            }
        )

        return JSONResponse(
            {
                "success": True,
                "id": str(result.inserted_id),
                "website": website,
                "username": username,
                "password": password,  # Return plaintext for immediate UI update
            },
            status_code=201,
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Error adding password: {e}", exc_info=True)
        raise HTTPException(500, f"Error adding password: {str(e)}") from e


@app.put("/api/passwords/{password_id}")
async def update_password(password_id: str, request: Request, db=Depends(get_scoped_db)):
    """Update an existing password entry."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url(request)}/login",
        )

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(400, "User email not found")

    try:
        data = await request.json()
        website = data.get("website", "").strip()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not all([website, username, password]):
            raise HTTPException(400, "Website, username, and password are required")

        # Encrypt password
        encrypted_password = await encrypt_password(db, password, user_email)

        # Update in database
        from bson import ObjectId

        result = await db.passwords.update_one(
            {"_id": ObjectId(password_id), "user_email": user_email},
            {
                "$set": {
                    "website": website,
                    "username": username,
                    "encrypted_password": encrypted_password,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        if result.matched_count == 0:
            raise HTTPException(404, "Password not found or access denied")

        return JSONResponse(
            {
                "success": True,
                "id": password_id,
                "website": website,
                "username": username,
                "password": password,  # Return plaintext for immediate UI update
            }
        )
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Error updating password: {e}", exc_info=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(500, f"Error updating password: {str(e)}") from e


@app.delete("/api/passwords/{password_id}")
async def delete_password(password_id: str, request: Request, db=Depends(get_scoped_db)):
    """Delete a password entry."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url(request)}/login",
        )

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(400, "User email not found")

    try:
        from bson import ObjectId

        result = await db.passwords.delete_one(
            {"_id": ObjectId(password_id), "user_email": user_email}
        )

        if result.deleted_count == 0:
            raise HTTPException(404, "Password not found or access denied")

        return JSONResponse({"success": True})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Error deleting password: {e}", exc_info=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(500, f"Error deleting password: {str(e)}") from e


@app.post("/api/generate-password")
async def generate_password(request: Request):
    """Generate a secure password based on options."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url(request)}/login",
        )

    try:
        options = await request.json()
        length = options.get("length", 16)
        use_upper = options.get("uppercase", True)
        use_lower = options.get("lowercase", True)
        use_numbers = options.get("numbers", True)
        use_symbols = options.get("symbols", True)

        # Validate length
        try:
            length = int(length)
            if not 12 <= length <= 99:
                raise ValueError()
        except (ValueError, TypeError) as e:
            raise HTTPException(400, "Password length must be an integer between 12 and 99") from e

        # Build character set
        alphabet = ""
        password_parts = []

        if use_upper:
            alphabet += string.ascii_uppercase
            password_parts.append(secrets.choice(string.ascii_uppercase))
        if use_lower:
            alphabet += string.ascii_lowercase
            password_parts.append(secrets.choice(string.ascii_lowercase))
        if use_numbers:
            alphabet += string.digits
            password_parts.append(secrets.choice(string.digits))
        if use_symbols:
            safe_symbols = "!@#$%^&*()_+-=[]{}|;:,.<>?"
            alphabet += safe_symbols
            password_parts.append(secrets.choice(safe_symbols))

        if not alphabet:
            raise HTTPException(400, "At least one character type must be selected")

        # Fill the rest
        remaining_length = length - len(password_parts)
        for _ in range(remaining_length):
            password_parts.append(secrets.choice(alphabet))

        # Shuffle
        secrets.SystemRandom().shuffle(password_parts)
        password = "".join(password_parts)

        return JSONResponse({"password": password})
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Error generating password: {e}", exc_info=True)
        raise HTTPException(500, f"Error generating password: {str(e)}") from e


@app.get("/api/me")
async def get_me(request: Request):
    """Get current user info."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url(request)}/login",
        )

    return {
        "email": user["email"],
        "roles": get_roles(user),
        "app": APP_SLUG,
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "app": APP_SLUG, "auth": "shared"}



# To run the multi-app platform: uvicorn multi_app_main:app --reload
