"""
Analytics Dashboard with Shared Auth

Demonstrates shared authentication mode with higher role requirements.
Dashboard requires "viewer" role, while ClickTracker also requires "viewer".

Features demonstrated:
- auth.mode="shared" with require_role="viewer"
- Cross-app data access (reads from click_tracker)
- Role-based access (only viewers, editors, and admins can access)
- Admin-only role management endpoint
- Secure cookies (auto-configured for HTTPS in production)
- Rate limiting on auth endpoints
- Audit logging for compliance
- Token revocation on logout
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr

from mdb_engine import MongoDBEngine

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

# Create FastAPI app with shared auth
# require_role="editor" means only users with editor or admin role can access
app = engine.create_app(
    slug="dashboard",
    manifest=Path(__file__).parent / "manifest.json",
    title="Analytics Dashboard (Shared Auth)",
)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# =============================================================================
# Request Models
# =============================================================================

class UpdateRolesRequest(BaseModel):
    email: EmailStr
    roles: List[str]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# =============================================================================
# Auth Endpoints (Public per manifest)
# =============================================================================

@app.post("/login")
async def login(request: Request, data: LoginRequest):
    """
    Login endpoint - same user pool as ClickTracker.
    
    Note: Even after successful login, you need "viewer" role to access
    other endpoints on this app.
    
    Security features:
    - Rate limited (5 attempts per 5 minutes)
    - Audit logged (success and failure)
    - Secure cookie settings
    """
    user_pool = request.app.state.user_pool
    audit_log = getattr(request.app.state, "audit_log", None)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    
    token = await user_pool.authenticate(data.email, data.password)
    
    if not token:
        # Audit log failed login
        if audit_log:
            await audit_log.log_login_failed(
                email=data.email,
                reason="invalid_credentials",
                ip_address=client_ip,
                user_agent=user_agent,
                app_slug="dashboard",
            )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Check if user has required role for dashboard
    user = await user_pool.validate_token(token)
    dashboard_roles = user.get("app_roles", {}).get("dashboard", [])
    
    # Allow viewer access
    has_access = "viewer" in dashboard_roles or "editor" in dashboard_roles or "admin" in dashboard_roles
    
    # Audit log successful login
    if audit_log:
        await audit_log.log_login_success(
            email=data.email,
            ip_address=client_ip,
            user_agent=user_agent,
            app_slug="dashboard",
        )
    
    response = JSONResponse(content={
        "message": "Login successful",
        "token": token,
        "dashboard_roles": dashboard_roles,
        "has_dashboard_access": has_access,
        "note": "You need 'viewer' role to access dashboard endpoints." if not has_access else "You have access to dashboard!",
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
            app_slug="dashboard",
        )
    
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("mdb_auth_token")
    return response


# =============================================================================
# Protected Endpoints (require "editor" role per manifest)
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Dashboard home - requires editor role."""
    user = request.state.user
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "roles": request.state.user_roles,
    })


@app.get("/me")
async def get_me(request: Request):
    """Get current user info with roles."""
    user = request.state.user
    return {
        "user": user,
        "roles": request.state.user_roles,
        "app": "dashboard",
        "note": "You have editor or admin role - that's why you can access this!",
    }


@app.get("/analytics")
async def get_analytics(request: Request, hours: int = 24):
    """
    Get click analytics from ClickTracker app.
    
    Demonstrates cross-app data access - Dashboard reads from ClickTracker's data.
    Requires editor role.
    """
    user = request.state.user
    
    # Access click_tracker's data (cross-app read via read_scopes in manifest)
    db = engine.get_scoped_db("dashboard")  # Dashboard has read access to click_tracker
    
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Query ClickTracker's clicks collection
    clicks = await db.get_collection("click_tracker_clicks").find(
        {"timestamp": {"$gte": since}}
    ).sort("timestamp", -1).to_list(length=1000)
    
    # Convert ObjectIds and datetimes
    for click in clicks:
        click["_id"] = str(click["_id"])
        if isinstance(click.get("timestamp"), datetime):
            click["timestamp"] = click["timestamp"].isoformat()
    
    # Aggregate analytics
    total_clicks = len(clicks)
    unique_users = len(set(c.get("user_id") for c in clicks))
    
    # Top URLs
    url_counts = {}
    for click in clicks:
        url = click.get("url", "unknown")
        url_counts[url] = url_counts.get(url, 0) + 1
    top_urls = sorted(url_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    
    return {
        "period_hours": hours,
        "total_clicks": total_clicks,
        "unique_users": unique_users,
        "top_urls": [{"url": url, "count": count} for url, count in top_urls],
        "recent_clicks": clicks[:20],
        "requested_by": user["email"],
    }


# =============================================================================
# Admin-Only Endpoints
# =============================================================================

@app.post("/admin/grant-role")
async def grant_role(request: Request, data: UpdateRolesRequest):
    """
    Grant roles to a user - Admin only.
    
    This is how you can promote a user from "viewer" to "editor"
    so they can access the dashboard.
    
    Audit logged for compliance.
    """
    user = request.state.user
    audit_log = getattr(request.app.state, "audit_log", None)
    client_ip = request.client.host if request.client else None
    
    # Check if current user is admin
    if "admin" not in request.state.user_roles:
        raise HTTPException(status_code=403, detail="Admin role required")
    
    user_pool = request.app.state.user_pool
    
    # Get old roles for audit
    target_user = await user_pool.get_user_by_email(data.email)
    old_roles = target_user.get("app_roles", {}).get("dashboard", []) if target_user else []
    
    # Update roles for the target user
    success = await user_pool.update_user_roles(
        email=data.email,
        app_slug="dashboard",
        roles=data.roles,
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Audit log the role change
    if audit_log:
        await audit_log.log_role_change(
            email=data.email,
            app_slug="dashboard",
            old_roles=old_roles,
            new_roles=data.roles,
            changed_by=user["email"],
            ip_address=client_ip,
        )
    
    return {
        "message": f"Roles updated for {data.email}",
        "new_roles": data.roles,
        "updated_by": user["email"],
    }


@app.post("/admin/grant-click-tracker-role")
async def grant_click_tracker_role(request: Request, data: UpdateRolesRequest):
    """Grant roles for the ClickTracker app - Admin only."""
    user = request.state.user
    
    if "admin" not in request.state.user_roles:
        raise HTTPException(status_code=403, detail="Admin role required")
    
    user_pool = request.app.state.user_pool
    
    success = await user_pool.update_user_roles(
        email=data.email,
        app_slug="click_tracker",
        roles=data.roles,
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "message": f"ClickTracker roles updated for {data.email}",
        "new_roles": data.roles,
        "updated_by": user["email"],
    }


# =============================================================================
# Public Endpoints
# =============================================================================

@app.get("/api")
async def api_info():
    """API info - public endpoint."""
    return {
        "app": "dashboard",
        "auth_mode": "shared",
        "require_role": "editor",
        "note": "This app requires 'editor' or 'admin' role. Register on ClickTracker first, then have an admin grant you 'editor' role.",
        "endpoints": {
            "POST /login": "Login (public)",
            "GET /me": "Get current user (requires editor)",
            "GET /analytics": "Get click analytics (requires editor)",
            "POST /admin/grant-role": "Grant roles to user (requires admin)",
            "GET /health": "Health check (public)",
        },
    }


@app.get("/health")
async def health():
    """Health check - public endpoint."""
    return {"status": "healthy", "app": "dashboard", "auth_mode": "shared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

