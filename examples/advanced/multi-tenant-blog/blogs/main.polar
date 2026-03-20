# Multi-Tenant Blog — OSO Polar Policy
#
# Defines the resource types, roles, and permission mappings used by
# the blog platform.  The mdb-engine policy compiler seeds initial
# role assignments (has_role facts) from each blog's manifest.json;
# this file tells OSO *what those roles mean*.

actor User {}

resource Post {
    permissions = ["read", "write", "create", "delete"];
    roles = ["reader", "editor", "admin"];

    "read" if "reader";
    "write" if "editor";
    "create" if "editor";
    "delete" if "admin";

    # Role hierarchy
    "reader" if "editor";
    "editor" if "admin";
}

resource Comment {
    permissions = ["read", "create"];
    roles = ["reader"];

    "read" if "reader";
    "create" if "reader";
}

resource Activity {
    permissions = ["read", "write", "create", "delete"];
    roles = ["editor", "admin"];

    "read" if "editor";
    "write" if "admin";
    "create" if "admin";
    "delete" if "admin";

    "editor" if "admin";
}

allow(actor, action, resource) if has_permission(actor, action, resource);
