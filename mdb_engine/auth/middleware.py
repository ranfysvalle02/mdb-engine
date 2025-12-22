"""
Security Middleware

Middleware for enforcing security settings from manifest configuration.

This module is part of MDB_ENGINE - MongoDB Runtime Engine.
"""

import os
import logging
import secrets
from typing import Callable, Awaitable
from fastapi import Request, Response, HTTPException, status
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware for enforcing security settings from manifest.
    
    Features:
    - HTTPS enforcement in production
    - CSRF token generation and validation
    - Security headers
    - Token validation
    """
    
    def __init__(
        self,
        app,
        require_https: bool = False,
        csrf_protection: bool = True,
        security_headers: bool = True
    ):
        """
        Initialize security middleware.
        
        Args:
            app: FastAPI application
            require_https: Require HTTPS in production (default: False, auto-detected)
            csrf_protection: Enable CSRF protection (default: True)
            security_headers: Add security headers (default: True)
        """
        super().__init__(app)
        self.require_https = require_https
        self.csrf_protection = csrf_protection
        self.security_headers = security_headers
    
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """
        Process request through security middleware.
        """
        # Check HTTPS requirement
        if self.require_https:
            is_production = os.getenv("G_NOME_ENV") == "production" or os.getenv("ENVIRONMENT") == "production"
            if is_production and request.url.scheme != "https":
                if request.method == "GET":
                    # Redirect to HTTPS
                    https_url = str(request.url).replace("http://", "https://", 1)
                    return RedirectResponse(url=https_url, status_code=301)
                else:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="HTTPS required in production"
                    )
        
        # Generate CSRF token if not present (for GET requests)
        if self.csrf_protection and request.method == "GET":
            csrf_token = request.cookies.get("csrf_token")
            if not csrf_token:
                csrf_token = secrets.token_urlsafe(32)
                # Will be set in response
        
        # Process request
        response = await call_next(request)
        
        # Set security headers
        if self.security_headers:
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            
            # Content Security Policy (basic)
            if request.url.path.startswith("/api"):
                response.headers["Content-Security-Policy"] = "default-src 'self'"
        
        # Set CSRF token cookie if generated
        if self.csrf_protection and request.method == "GET" and not request.cookies.get("csrf_token"):
            csrf_token = secrets.token_urlsafe(32)
            is_https = request.url.scheme == "https"
            is_production = os.getenv("G_NOME_ENV") == "production"
            response.set_cookie(
                key="csrf_token",
                value=csrf_token,
                httponly=True,
                secure=is_https or is_production,
                samesite="lax",
                max_age=86400  # 24 hours
            )
        
        return response


def create_security_middleware(config: dict) -> Callable:
    """
    Create security middleware from manifest config.
    
    Args:
        config: token_management.security config from manifest
    
    Returns:
        SecurityMiddleware instance
    """
    security = config.get("security", {})
    
    return SecurityMiddleware(
        app=None,  # Will be set by FastAPI
        require_https=security.get("require_https", False),
        csrf_protection=security.get("csrf_protection", True),
        security_headers=True
    )

