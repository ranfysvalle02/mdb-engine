# OSO Policy for Hello World Example
# Resource-based authorization using OSO's resource block syntax

actor User {}

resource Document {
    permissions = ["read", "write"];
    roles = ["viewer", "editor"];

    # Link permissions to roles
    # Viewers get "read" permission
    "read" if "viewer";

    # Editors get "write" permission
    "write" if "editor";

    # Editors inherit viewer permissions (optional but common)
    "viewer" if "editor";
}

# Standard entry point - connects resource block logic to allow rules
allow(actor, action, resource) if
    has_permission(actor, action, resource);

# Note: has_role facts are inserted from the application code via OSO Cloud API
# The resource block will automatically look for has_role(actor, role_name, resource) facts
# No need to declare has_role here - OSO Cloud makes inserted facts available automatically
