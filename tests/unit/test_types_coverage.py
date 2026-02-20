"""
Tests for type definitions to ensure coverage.

This module imports and instantiates TypedDicts from mdb_engine.core.types
to ensure that the type definitions are valid and loaded by the interpreter.
"""

from mdb_engine.core.types import (
    AccountLockoutDict,
    AuthAuthorizationDict,
    AuthConfigDict,
    AuthPolicyDict,
    CORSConfigDict,
    DemoUserDict,
    EmbeddingConfigDict,
    HealthChecksConfigDict,
    HealthStatusDict,
    IndexDefinitionDict,
    IndexKeysDict,
    InitialDataDict,
    IPValidationDict,
    LoggingConfigDict,
    ManagedIndexesDict,
    ManifestDict,
    MemoryConfigDict,
    MetricsConfigDict,
    MetricsDict,
    ObservabilityConfigDict,
    PasswordPolicyDict,
    RateLimitingConfigDict,
    RateLimitingDict,
    SecurityConfigDict,
    SessionFingerprintingDict,
    TokenFingerprintingDict,
    TokenManagementDict,
    UsersConfigDict,
    WebSocketAuthDict,
    WebSocketEndpointDict,
    WebSocketsDict,
)


class TestTypesCoverage:
    """Test instantiation of TypedDicts to ensure coverage."""

    def test_index_types(self):
        """Test index-related types."""
        keys: IndexKeysDict = {"field": 1}
        assert keys

        index_def: IndexDefinitionDict = {
            "name": "test",
            "type": "regular",
            "keys": {"field": 1},
            "unique": True,
        }
        assert index_def

        managed: ManagedIndexesDict = {"users": [{"name": "idx", "type": "regular"}]}
        assert managed

    def test_auth_types(self):
        """Test auth-related types."""
        authz: AuthAuthorizationDict = {
            "model": "model.conf",
            "policies_collection": "policies",
            "link_users_roles": True,
            "default_roles": ["user"],
        }
        assert authz

        policy: AuthPolicyDict = {
            "required": True,
            "provider": "casbin",
            "authorization": authz,
        }
        assert policy

        demo_user: DemoUserDict = {
            "email": "test@example.com",
            "password": "pass",
            "role": "admin",
        }
        assert demo_user

        users: UsersConfigDict = {
            "enabled": True,
            "strategy": "app_users",
            "demo_users": [demo_user],
        }
        assert users

        auth: AuthConfigDict = {"policy": policy, "users": users}
        assert auth

    def test_security_types(self):
        """Test security-related types."""
        rate_limit: RateLimitingConfigDict = {"max_attempts": 5, "window_seconds": 60}
        rate_limits: RateLimitingDict = {"login": rate_limit}
        password: PasswordPolicyDict = {"min_length": 8}
        session: SessionFingerprintingDict = {"enabled": True}
        lockout: AccountLockoutDict = {"enabled": True}
        ip: IPValidationDict = {"enabled": True}
        token_fp: TokenFingerprintingDict = {"enabled": True}

        security: SecurityConfigDict = {
            "require_https": True,
            "rate_limiting": rate_limits,
            "password_policy": password,
            "session_fingerprinting": session,
            "account_lockout": lockout,
            "ip_validation": ip,
            "token_fingerprinting": token_fp,
        }
        assert security

        token_mgmt: TokenManagementDict = {"enabled": True, "security": security}
        assert token_mgmt

    def test_websocket_types(self):
        """Test websocket-related types."""
        ws_auth: WebSocketAuthDict = {"required": True}
        ws_endpoint: WebSocketEndpointDict = {"path": "/ws", "auth": ws_auth}
        ws_config: WebSocketsDict = {"chat": ws_endpoint}
        assert ws_config

    def test_service_types(self):
        """Test service configuration types."""
        memory: MemoryConfigDict = {"enabled": True, "collection_name": "memories"}
        embedding: EmbeddingConfigDict = {"enabled": True, "max_tokens_per_chunk": 512}

        health: HealthChecksConfigDict = {"enabled": True}
        metrics: MetricsConfigDict = {"enabled": True}
        logging: LoggingConfigDict = {"level": "INFO"}
        observability: ObservabilityConfigDict = {
            "health_checks": health,
            "metrics": metrics,
            "logging": logging,
        }

        cors: CORSConfigDict = {"enabled": True, "allow_origins": ["*"]}
        initial_data: InitialDataDict = {"users": [{"name": "admin"}]}

        assert memory
        assert embedding
        assert observability
        assert cors
        assert initial_data

    def test_manifest_type(self):
        """Test main manifest type."""
        manifest: ManifestDict = {
            "schema_version": "2.0",
            "slug": "test-app",
            "name": "Test App",
            "status": "active",
        }
        assert manifest

    def test_response_types(self):
        """Test API response types."""
        health: HealthStatusDict = {
            "status": "healthy",
            "checks": {},
            "timestamp": "2023-01-01",
        }
        assert health

        metrics: MetricsDict = {
            "operations": {},
            "summary": {},
            "timestamp": "2023-01-01",
        }
        assert metrics
