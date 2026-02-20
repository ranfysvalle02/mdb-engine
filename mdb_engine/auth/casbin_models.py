"""
Default Casbin Models

Provides default Casbin model configurations for authorization.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

# Default RBAC (Role-Based Access Control) model
# This model supports:
# - Subject (user/role) -> Object (resource) -> Action (permission)
# - Role inheritance via g (grouping) rules
# - Policy effect: allow if any policy matches

DEFAULT_RBAC_MODEL = """
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj, act

[role_definition]
g = _, _

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = g(r.sub, p.sub) && r.obj == p.obj && r.act == p.act
"""

# Alternative: Simple ACL model (no roles)
# Use this if you don't need role-based access control
SIMPLE_ACL_MODEL = """
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj, act

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = r.sub == p.sub && r.obj == p.obj && r.act == p.act
"""
