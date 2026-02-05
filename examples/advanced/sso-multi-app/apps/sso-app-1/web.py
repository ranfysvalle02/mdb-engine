#!/usr/bin/env python3
"""
Password Generator App - SSO Enabled
====================================

Secure password generator and manager with SSO authentication.
Generates strong passwords and stores them encrypted per user.
"""

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

from mdb_engine.dependencies import get_scoped_db

# Import shared security utilities
try:
    from shared_security import get_cookie_settings, validate_jwt_token_format
except ImportError:
    # Fallback if shared_security not available (shouldn't happen in normal usage)
    def get_cookie_settings():
        return {"httponly": True, "samesite": "lax", "secure": False}
    def validate_jwt_token_format(token: str) -> bool:
        return bool(token and len(token) > 10)

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

try:
    from mdb_engine import MongoDBEngine
except ImportError as e:
    logger.error(f"Failed to import mdb_engine: {e}", exc_info=True)
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

APP_SLUG = "pwd-zero"
APP_SECRET = os.getenv("APP_ENCRYPTION_SECRET", "default-secret-change-in-production")

# Initialize MongoDB Engine
try:
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.getenv("MONGODB_DB", "oblivio_apps")
    logger.info(f"Initializing MongoDBEngine with URI: {mongo_uri[:50]}... (db: {db_name})")

    engine = MongoDBEngine(
        mongo_uri=mongo_uri,
        db_name=db_name,
    )
    logger.info("MongoDBEngine initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize MongoDBEngine: {e}", exc_info=True)
    sys.exit(1)

# Create FastAPI app
try:
    manifest_path = Path(__file__).parent / "manifest.json"
    logger.info(f"Creating FastAPI app with manifest: {manifest_path}")

    app = engine.create_app(
        slug=APP_SLUG,
        manifest=manifest_path,
        title="Password Generator",
        description="Secure password generator and manager",
        version="1.0.0",
    )
    logger.info("FastAPI app created successfully")
except Exception as e:
    logger.error(f"Failed to create FastAPI app: {e}", exc_info=True)
    sys.exit(1)

# Template engine
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================================
# HELPER FUNCTIONS - AUTH HUB URL
# ============================================================================


def get_auth_hub_url() -> str:
    """
    Get auth hub URL from manifest, environment variable, or default.

    Priority:
    1. manifest.auth.auth_hub_url (if mode is "shared")
    2. AUTH_HUB_URL environment variable
    3. Default: http://localhost:8000

    Returns:
        Auth hub URL string
    """
    # Try manifest first
    manifest = getattr(app.state, "manifest", None)
    if manifest:
        auth_config = manifest.get("auth", {})
        if auth_config.get("mode") == "shared":
            auth_hub_url = auth_config.get("auth_hub_url")
            if auth_hub_url:
                return auth_hub_url

    # Fallback to environment variable
    return os.getenv("AUTH_HUB_URL", "http://localhost:8000")


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
        url=f"{get_auth_hub_url()}/login?redirect_to={encoded_callback}", status_code=302
    )


@app.get("/auth/callback")
async def auth_callback(request: Request, token: str = None):
    """Token exchange endpoint - sets cookie for this app after auth hub login."""
    from urllib.parse import unquote_plus

    if not token:
        token = request.query_params.get("token")
    if token:
        token = unquote_plus(token)
    
    # Validate token format before processing
    if not token or not validate_jwt_token_format(token):
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?error=invalid_token", status_code=302
        )

    from mdb_engine.auth.shared_users import SharedUserPool

    pool: SharedUserPool = getattr(app.state, "user_pool", None)
    if not pool:
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?error=pool_not_initialized", status_code=302
        )

    user = await pool.validate_token(token)
    if not user:
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?error=invalid_token", status_code=302
        )

    response = RedirectResponse(url="/", status_code=302)
    cookie_settings = get_cookie_settings()
    response.set_cookie(
        key="mdb_auth_token",
        value=token,
        httponly=cookie_settings["httponly"],
        samesite=cookie_settings["samesite"],
        secure=cookie_settings["secure"],
        max_age=86400,  # 24 hours
        path="/",
    )
    return response


@app.post("/logout")
async def logout(request: Request):
    """Logout and revoke token."""
    from mdb_engine.auth.shared_users import SharedUserPool

    pool: SharedUserPool = getattr(app.state, "user_pool", None)

    # Get token from cookie
    token = request.cookies.get("mdb_auth_token")

    # Revoke token if we have pool and token
    if pool and token:
        try:
            await pool.revoke_token(token, reason="logout")
        except (AttributeError, TypeError) as e:
            logger.warning(f"Failed to revoke token: {e}")

    # Create response redirecting to auth hub
    response = RedirectResponse(url=f"{get_auth_hub_url()}/login", status_code=302)

    # Delete cookie
    cookie_settings = get_cookie_settings()
    response.delete_cookie(
        "mdb_auth_token",
        path="/",
        domain=None,  # Let browser handle domain
        secure=cookie_settings["secure"],
        samesite=cookie_settings["samesite"],
    )

    return response


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
            url=f"{get_auth_hub_url()}/login?redirect_to={encoded_callback}", status_code=302
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
            "auth_hub_url": get_auth_hub_url(),
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
            detail=f"Not authenticated. Please log in at {get_auth_hub_url()}/login",
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
    except Exception as e:
        logger.error(f"Error fetching passwords: {e}", exc_info=True)
        raise HTTPException(500, f"Error fetching passwords: {str(e)}") from e


@app.post("/api/passwords")
async def add_password(request: Request, db=Depends(get_scoped_db)):
    """Add a new password entry."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url()}/login",
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
    except Exception as e:
        logger.error(f"Error adding password: {e}", exc_info=True)
        raise HTTPException(500, f"Error adding password: {str(e)}") from e


@app.put("/api/passwords/{password_id}")
async def update_password(password_id: str, request: Request, db=Depends(get_scoped_db)):
    """Update an existing password entry."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url()}/login",
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
    except Exception as e:
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
            detail=f"Not authenticated. Please log in at {get_auth_hub_url()}/login",
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
    except Exception as e:
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
            detail=f"Not authenticated. Please log in at {get_auth_hub_url()}/login",
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
    except Exception as e:
        logger.error(f"Error generating password: {e}", exc_info=True)
        raise HTTPException(500, f"Error generating password: {str(e)}") from e


@app.get("/api/me")
async def get_me(request: Request):
    """Get current user info."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated. Please log in at {get_auth_hub_url()}/login",
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


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Password Generator App on 0.0.0.0:8000")
    try:
        uvicorn.run(
            app, host="0.0.0.0", port=8000, log_level=os.getenv("LOG_LEVEL", "info").lower()
        )
    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)
        sys.exit(1)
