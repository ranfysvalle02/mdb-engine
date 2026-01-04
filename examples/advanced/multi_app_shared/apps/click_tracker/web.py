"""
ClickTracker Application with Shared Auth

Demonstrates shared authentication mode where users authenticate once
and can access multiple apps (SSO). Uses the same user pool as Dashboard.

Features demonstrated:
- auth.mode="shared" in manifest
- Auto-configured SharedAuthMiddleware via engine.create_app()
- User registration and login endpoints
- request.state.user populated by middleware
- Role-based access control
- Secure cookies (auto-configured for HTTPS in production)
- Rate limiting on auth endpoints
- Audit logging for compliance
- Token revocation on logout

Security Features (NEW):
- CSRF protection (auto-enabled for shared auth)
- HSTS headers (auto-enabled in production)
- Password policy with entropy check
- Session binding (fingerprint-based)
- Common password detection
"""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from mdb_engine import MongoDBEngine
from mdb_engine.auth import validate_password_strength, calculate_password_entropy

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

# Create FastAPI app with automatic lifecycle and shared auth
# When auth.mode="shared" in manifest, SharedAuthMiddleware is auto-added
app = engine.create_app(
    slug="click_tracker",
    manifest=Path(__file__).parent / "manifest.json",
    title="Click Tracker (Shared Auth)",
)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# =============================================================================
# Security Helpers
# =============================================================================

def get_client_ip(request: Request) -> Optional[str]:
    """Extract client IP, handling proxies."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


def compute_fingerprint(request: Request) -> str:
    """Compute device fingerprint from request headers."""
    components = [
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
        request.headers.get("accept-encoding", ""),
    ]
    return hashlib.sha256("|".join(components).encode()).hexdigest()


def get_password_policy(request: Request) -> dict:
    """Get password policy from manifest."""
    manifest = getattr(request.app.state, "manifest", {})
    return manifest.get("auth", {}).get("password_policy", {})


def get_session_binding_config(request: Request) -> dict:
    """Get session binding config from manifest."""
    manifest = getattr(request.app.state, "manifest", {})
    return manifest.get("auth", {}).get("session_binding", {})


# =============================================================================
# Request Models
# =============================================================================

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ClickData(BaseModel):
    url: str = "/"
    element: str = "button"


# =============================================================================
# Auth Endpoints (Public - no auth required per manifest)
# =============================================================================

@app.post("/register")
async def register(request: Request, data: RegisterRequest):
    """
    Register a new user in the shared user pool.
    
    The user will be able to access both ClickTracker and Dashboard
    (subject to role requirements).
    
    Security features:
    - Rate limited (see manifest.json)
    - Audit logged
    - Secure cookie settings
    - Password policy enforcement (entropy, common passwords)
    - Session binding (fingerprint)
    """
    user_pool = request.app.state.user_pool
    audit_log = getattr(request.app.state, "audit_log", None)
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    
    # Validate password against policy from manifest
    password_policy = get_password_policy(request)
    is_valid, errors = validate_password_strength(data.password, config=password_policy)
    
    if not is_valid:
        # Calculate entropy for helpful feedback
        entropy = calculate_password_entropy(data.password)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Password does not meet security requirements",
                "errors": errors,
                "entropy_bits": entropy,
                "required_entropy_bits": password_policy.get("min_entropy_bits", 40),
            }
        )
    
    try:
        user = await user_pool.create_user(
            email=data.email,
            password=data.password,
            # Assign default roles for both apps
            app_roles={
                "click_tracker": ["viewer"],
                "dashboard": ["viewer"],
            },
        )
        
        # Get session binding config
        session_binding = get_session_binding_config(request)
        fingerprint = compute_fingerprint(request) if session_binding.get("bind_fingerprint", True) else None
        
        # Auto-login after registration with session binding
        token = await user_pool.authenticate(
            email=data.email,
            password=data.password,
            ip_address=client_ip,
            fingerprint=fingerprint,
            session_binding=session_binding,
        )
        
        # Audit log the registration
        if audit_log:
            await audit_log.log_register(
                email=data.email,
                ip_address=client_ip,
                user_agent=user_agent,
                app_slug="click_tracker",
            )
        
        # Serialize datetime fields
        user_response = {
            "email": user["email"],
            "app_roles": user.get("app_roles", {}),
        }
        
        response = JSONResponse(content={
            "message": "Registration successful",
            "user": user_response,
            "note": "You now have 'viewer' role for both apps.",
            "security": {
                "session_binding": "fingerprint" if fingerprint else "none",
                "password_entropy_bits": calculate_password_entropy(data.password),
            },
        })
        
        # Set auth cookie with secure settings
        cookie_config = user_pool.get_secure_cookie_config(request)
        response.set_cookie(value=token, **cookie_config)
        
        return response
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/login")
async def login(request: Request, data: LoginRequest):
    """
    Login to the shared user pool.
    
    The token works across all apps using shared auth mode (SSO).
    
    Security features:
    - Rate limited (5 attempts per 5 minutes)
    - Audit logged (success and failure)
    - Secure cookie settings
    - Session binding (fingerprint embedded in token)
    """
    user_pool = request.app.state.user_pool
    audit_log = getattr(request.app.state, "audit_log", None)
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    
    # Get session binding config
    session_binding = get_session_binding_config(request)
    fingerprint = compute_fingerprint(request) if session_binding.get("bind_fingerprint", True) else None
    
    # Authenticate with session binding
    token = await user_pool.authenticate(
        email=data.email,
        password=data.password,
        ip_address=client_ip,
        fingerprint=fingerprint,
        session_binding=session_binding,
    )
    
    if not token:
        # Audit log failed login
        if audit_log:
            await audit_log.log_login_failed(
                email=data.email,
                reason="invalid_credentials",
                ip_address=client_ip,
                user_agent=user_agent,
                app_slug="click_tracker",
            )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Audit log successful login
    if audit_log:
        await audit_log.log_login_success(
            email=data.email,
            ip_address=client_ip,
            user_agent=user_agent,
            app_slug="click_tracker",
        )
    
    response = JSONResponse(content={
        "message": "Login successful",
        "token": token,
        "note": "This token works for both click_tracker and dashboard (subject to role requirements).",
        "security": {
            "session_binding": "fingerprint" if fingerprint else "none",
        },
    })
    
    # Set auth cookie with secure settings
    cookie_config = user_pool.get_secure_cookie_config(request)
    response.set_cookie(value=token, **cookie_config)
    
    return response


@app.post("/logout")
async def logout(request: Request):
    """
    Logout by revoking the token and clearing the auth cookie.
    
    Security features:
    - Token revocation (blacklisted until expiry)
    - Audit logged
    """
    user_pool = request.app.state.user_pool
    audit_log = getattr(request.app.state, "audit_log", None)
    user = request.state.user
    client_ip = request.client.host if request.client else None
    
    # Revoke the current token if present
    token = request.cookies.get("mdb_auth_token")
    if token:
        await user_pool.revoke_token(token, reason="logout")
    
    # Audit log the logout
    if audit_log and user:
        await audit_log.log_logout(
            email=user.get("email"),
            ip_address=client_ip,
            app_slug="click_tracker",
        )
    
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("mdb_auth_token")
    return response


# =============================================================================
# Protected Endpoints (require authentication)
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint - shows user info if authenticated."""
    user = request.state.user  # Populated by SharedAuthMiddleware
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "roles": request.state.user_roles,
    })


@app.get("/me")
async def get_me(request: Request):
    """Get current user info."""
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    return {
        "user": user,
        "roles": request.state.user_roles,
        "app": "click_tracker",
    }


@app.post("/track")
async def track_click(request: Request, click_data: Optional[ClickData] = None):
    """Track a click event - requires authentication."""
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    db = engine.get_scoped_db("click_tracker")
    
    click_doc = {
        "user_id": user["email"],
        "timestamp": datetime.utcnow(),
        "url": click_data.url if click_data else "/",
        "element": click_data.element if click_data else "button",
    }
    
    result = await db.clicks.insert_one(click_doc)
    
    return {
        "click_id": str(result.inserted_id),
        "status": "tracked",
        "user": user["email"],
    }


@app.get("/clicks")
async def get_clicks(request: Request, limit: int = 50):
    """Get click history - requires authentication."""
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    db = engine.get_scoped_db("click_tracker")
    
    # Users see only their own clicks (unless admin)
    query = {}
    if "admin" not in request.state.user_roles:
        query["user_id"] = user["email"]
    
    clicks = await db.clicks.find(query).sort("timestamp", -1).limit(limit).to_list(length=limit)
    
    # Convert ObjectIds
    for click in clicks:
        click["_id"] = str(click["_id"])
        click["timestamp"] = click["timestamp"].isoformat()
    
    return {"clicks": clicks, "count": len(clicks)}


# =============================================================================
# Public Endpoints (defined in manifest's public_routes)
# =============================================================================

@app.get("/api")
async def api_info():
    """API info - public endpoint."""
    return {
        "app": "click_tracker",
        "auth_mode": "shared",
        "note": "This app uses shared authentication. Login once, access multiple apps.",
        "endpoints": {
            "POST /register": "Register new user (public)",
            "POST /login": "Login and get SSO token (public)",
            "POST /logout": "Logout (public)",
            "GET /me": "Get current user info (requires auth)",
            "POST /track": "Track a click event (requires auth)",
            "GET /clicks": "Get click history (requires auth)",
            "GET /health": "Health check (public)",
        },
    }


@app.get("/health")
async def health():
    """Health check - public endpoint."""
    return {"status": "healthy", "app": "click_tracker", "auth_mode": "shared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

