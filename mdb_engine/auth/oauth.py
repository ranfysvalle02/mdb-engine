"""
OAuth 2.0 / OpenID Connect Integration Module

Provides OAuth/OIDC login via external identity providers (Google, GitHub,
Keycloak, Auth0, etc.) using Authlib.

After a successful OAuth callback the user is matched or created in the local
user collection and issued standard MDB-Engine JWT tokens so that all existing
auth middleware, decorators, and dependencies continue to work unchanged.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment-variable interpolation helper
# ---------------------------------------------------------------------------
_ENV_VAR_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _resolve_env(value: str) -> str:
    """Resolve ``${ENV_VAR}`` patterns to their environment values.

    If the value does not match the pattern it is returned as-is.
    """
    match = _ENV_VAR_RE.match(value)
    if match:
        env_name = match.group(1)
        resolved = os.getenv(env_name, "")
        if not resolved:
            logger.warning("OAuth config references ${%s} but it is not set", env_name)
        return resolved
    return value


# ---------------------------------------------------------------------------
# OAuthService
# ---------------------------------------------------------------------------


class OAuthService:
    """Manages the Authlib OAuth registry and provider interactions.

    Parameters
    ----------
    oauth_config:
        The ``auth.oauth`` block from the manifest.
    db:
        A scoped database wrapper for the app (used for user look-ups).
    slug:
        The app slug (used for cookie names and logging).
    """

    def __init__(self, oauth_config: dict[str, Any], db: Any, slug: str) -> None:
        try:
            from authlib.integrations.starlette_client import OAuth
        except ImportError as exc:
            raise ImportError(
                "authlib is required for OAuth support. " "Install with: pip install mdb-engine[oauth]"
            ) from exc

        self._config = oauth_config
        self._db = db
        self._slug = slug
        self._user_strategy = oauth_config.get("user_strategy", "link_or_create")
        self._default_role = oauth_config.get("default_role", "user")
        self._redirect_after_login = oauth_config.get("redirect_after_login", "/")
        self._redirect_after_logout = oauth_config.get("redirect_after_logout", "/")

        # Build Authlib registry
        self._oauth = OAuth()
        self._provider_names: list[str] = []
        self._register_providers(oauth_config.get("providers", {}))

    # ------------------------------------------------------------------
    # Provider registration
    # ------------------------------------------------------------------

    def _register_providers(self, providers: dict[str, dict[str, Any]]) -> None:
        """Register each provider declared in the manifest."""
        for name, prov_cfg in providers.items():
            client_id = _resolve_env(prov_cfg.get("client_id", ""))
            client_secret = _resolve_env(prov_cfg.get("client_secret", ""))

            if not client_id or not client_secret:
                logger.warning(
                    "OAuth provider '%s' skipped: missing client_id or client_secret",
                    name,
                )
                continue

            kwargs: dict[str, Any] = {
                "name": name,
                "client_id": client_id,
                "client_secret": client_secret,
                "client_kwargs": {},
            }

            # OIDC discovery
            if "server_metadata_url" in prov_cfg:
                kwargs["server_metadata_url"] = prov_cfg["server_metadata_url"]

            # Manual endpoint URLs
            if "authorize_url" in prov_cfg:
                kwargs["authorize_url"] = prov_cfg["authorize_url"]
            if "access_token_url" in prov_cfg:
                kwargs["access_token_url"] = prov_cfg["access_token_url"]
            if "userinfo_url" in prov_cfg:
                kwargs["userinfo_endpoint"] = prov_cfg["userinfo_url"]

            # Scopes
            scopes = prov_cfg.get("scopes", [])
            if scopes:
                kwargs["client_kwargs"]["scope"] = " ".join(scopes)

            self._oauth.register(**kwargs)
            self._provider_names.append(name)
            logger.info("OAuth provider '%s' registered for app '%s'", name, self._slug)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def provider_names(self) -> list[str]:
        """Return the list of registered provider names."""
        return list(self._provider_names)

    async def get_login_redirect(self, provider_name: str, request: Request) -> RedirectResponse:
        """Return a redirect response that sends the user to the provider's login page."""
        client = self._get_client(provider_name)
        redirect_uri = str(request.url_for(f"oauth_callback_{provider_name}"))
        return await client.authorize_redirect(request, redirect_uri)

    async def handle_callback(self, provider_name: str, request: Request) -> RedirectResponse:
        """Handle the OAuth callback: exchange code, upsert user, issue JWT cookies."""
        client = self._get_client(provider_name)

        # Exchange authorization code for tokens
        token = await client.authorize_access_token(request)

        # Extract user info
        userinfo = await self._extract_userinfo(client, token, provider_name)
        if not userinfo:
            logger.error("OAuth callback for '%s': could not extract userinfo", provider_name)
            return RedirectResponse(url=self._redirect_after_login)

        # Upsert local user
        user = await self._get_or_create_oauth_user(provider_name, userinfo)

        # Issue MDB-Engine JWT tokens and set cookies
        response = RedirectResponse(url=self._redirect_after_login)
        await self._issue_tokens(user, request, response)

        logger.info(
            "OAuth login via '%s' for user '%s' (app '%s')",
            provider_name,
            user.get("email", "unknown"),
            self._slug,
        )
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self, provider_name: str):
        """Retrieve a registered Authlib OAuth client by name."""
        client = self._oauth.create_client(provider_name)
        if client is None:
            raise ValueError(
                f"OAuth provider '{provider_name}' is not registered. " f"Available providers: {self._provider_names}"
            )
        return client

    async def _extract_userinfo(self, client: Any, token: dict[str, Any], provider_name: str) -> dict[str, Any] | None:
        """Extract user information from the OAuth token response."""
        # For OIDC providers the ID token carries userinfo directly
        userinfo = token.get("userinfo")
        if userinfo:
            return dict(userinfo)

        # Fallback: call the userinfo endpoint explicitly
        try:
            resp = await client.userinfo(token=token)
            if resp:
                return dict(resp)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            logger.debug("userinfo() call failed for '%s', trying GET", provider_name)

        # Last resort: use the access token to call a configured userinfo URL
        try:
            resp = await client.get("user", token=token)
            if resp and resp.status_code == 200:
                return resp.json()
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            logger.warning("Could not fetch userinfo for provider '%s': %s", provider_name, exc)

        return None

    async def _get_or_create_oauth_user(self, provider_name: str, userinfo: dict[str, Any]) -> dict[str, Any]:
        """Find or create a local user record for the OAuth identity."""
        oauth_id = str(userinfo.get("sub") or userinfo.get("id") or userinfo.get("login") or "")
        email = userinfo.get("email") or userinfo.get("mail") or f"{provider_name}_{oauth_id}@oauth.local"
        display_name = userinfo.get("name") or userinfo.get("login") or userinfo.get("preferred_username") or email
        avatar_url = userinfo.get("picture") or userinfo.get("avatar_url") or ""

        collection = self._db.users

        # Strategy: link_or_create – match by provider+id first, then by email
        if self._user_strategy == "link_or_create":
            # Try exact provider match
            user = await collection.find_one(
                {"oauth_linked_providers": {"$elemMatch": {"provider": provider_name, "id": oauth_id}}}
            )
            if user:
                # Update last login
                await collection.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"last_login": datetime.now(timezone.utc)}},
                )
                user["app_user_id"] = str(user["_id"])
                return user

            # Try email match (link existing account)
            user = await collection.find_one({"email": email})
            if user:
                # Append this provider to linked providers
                linked = user.get("oauth_linked_providers", [])
                linked.append({"provider": provider_name, "id": oauth_id})
                await collection.update_one(
                    {"_id": user["_id"]},
                    {
                        "$set": {
                            "oauth_linked_providers": linked,
                            "oauth_provider": provider_name,
                            "oauth_id": oauth_id,
                            "last_login": datetime.now(timezone.utc),
                        }
                    },
                )
                user["app_user_id"] = str(user["_id"])
                return user

        # Create new user
        user_doc: dict[str, Any] = {
            "email": email,
            "display_name": display_name,
            "role": self._default_role,
            "oauth_provider": provider_name,
            "oauth_id": oauth_id,
            "oauth_linked_providers": [{"provider": provider_name, "id": oauth_id}],
            "avatar_url": avatar_url,
            "created_at": datetime.now(timezone.utc),
            "last_login": datetime.now(timezone.utc),
        }
        result = await collection.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        user_doc["app_user_id"] = str(result.inserted_id)

        logger.info(
            "Created OAuth user '%s' via '%s' for app '%s'",
            email,
            provider_name,
            self._slug,
        )
        return user_doc

    async def _issue_tokens(
        self,
        user: dict[str, Any],
        request: Request,
        response: RedirectResponse,
    ) -> None:
        """Generate an MDB-Engine JWT token pair and set auth cookies."""
        from .cookie_utils import set_auth_cookies
        from .dependencies import SECRET_KEY
        from .jwt import generate_token_pair

        user_data = {
            "email": user.get("email", ""),
            "user_id": str(user.get("_id", "")),
            "app_user_id": str(user.get("_id", "")),
            "role": user.get("role", self._default_role),
            "app_slug": self._slug,
        }

        access_token, refresh_token, _meta = generate_token_pair(
            user_data=user_data,
            secret_key=str(SECRET_KEY),
        )

        set_auth_cookies(
            response,
            access_token=access_token,
            refresh_token=refresh_token,
            request=request,
        )


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_oauth_routes(app: FastAPI, oauth_service: OAuthService) -> None:
    """Dynamically register OAuth login / callback / providers routes.

    Routes created:
    - ``GET /auth/oauth/providers`` – list available providers
    - ``GET /auth/oauth/{provider}/login`` – redirect to provider login
    - ``GET /auth/oauth/{provider}/callback`` – handle provider callback
    """

    @app.get("/auth/oauth/providers")
    async def oauth_providers_list() -> JSONResponse:
        """Return the list of configured OAuth providers."""
        return JSONResponse({"providers": oauth_service.provider_names})

    # Create per-provider routes so that Authlib can resolve the callback URL
    # via ``request.url_for(...)`` without path-parameter ambiguity.
    for provider_name in oauth_service.provider_names:

        async def _login(
            request: Request,
            _name: str = provider_name,
        ) -> RedirectResponse:
            return await oauth_service.get_login_redirect(_name, request)

        async def _callback(
            request: Request,
            _name: str = provider_name,
        ) -> RedirectResponse:
            return await oauth_service.handle_callback(_name, request)

        app.get(f"/auth/oauth/{provider_name}/login", name=f"oauth_login_{provider_name}")(_login)
        app.get(f"/auth/oauth/{provider_name}/callback", name=f"oauth_callback_{provider_name}")(_callback)

    logger.info(
        "OAuth routes registered: providers=%s",
        oauth_service.provider_names,
    )
