# Frequently Asked Questions (FAQ)

## Architecture Questions

### Why is `allow_anonymous` at the policy level?

`allow_anonymous` is placed at the **authorization policy level** (`auth.policy.allow_anonymous`) because it's fundamentally an **access control decision**, not an authentication mechanism.

**The reasoning:**

1. **Authorization vs Authentication**: 
   - **Authentication** (`auth.users`) = "Who are you?" (user accounts, sessions, login)
   - **Authorization** (`auth.policy`) = "What can you access?" (permissions, roles, access rules)

2. **`allow_anonymous` is an access rule**: It answers the question "Can unauthenticated users access this app?" This is an authorization decision about who is allowed, not about how users authenticate.

3. **Policy controls access**: The authorization policy defines the rules for who can access the app:
   - `required: true` = authentication is mandatory
   - `allow_anonymous: true` = unauthenticated users are explicitly allowed
   - `allowed_roles` = which roles can access
   - `denied_users` = which users are blocked

4. **Separation of concerns**:
   - `auth.users` handles user management (creating accounts, sessions, registration)
   - `auth.policy` handles access control (who can access, what permissions they need)

**Example:**
```json
{
  "auth": {
    "policy": {
      "required": false,        // Authentication not mandatory
      "allow_anonymous": true,  // Explicitly allow anonymous access
      "allowed_roles": ["admin"] // But admins get special access
    },
    "users": {
      "enabled": true,          // User accounts exist
      "allow_registration": true // Users can register
    }
  }
}
```

This configuration means:
- Users can register and login (handled by `auth.users`)
- But anonymous/unauthenticated users can also access the app (handled by `auth.policy`)
- Admins get special permissions (handled by `auth.policy`)

### What's the difference between `auth.policy` and `auth.users`?

**`auth.policy`** = **Authorization** (Who can access what?)
- Controls access rules and permissions
- Defines roles, allowed users, denied users
- Determines if authentication is required
- Handles anonymous access rules
- Integrates with Casbin/OSO for fine-grained permissions

**`auth.users`** = **Authentication** (User accounts and sessions)
- Manages app-level user accounts
- Handles user registration and login
- Manages sessions and cookies
- Creates and manages user records in the database
- Handles demo users and anonymous sessions

**Think of it this way:**
- `auth.users` = "Here's how users log in and manage their accounts"
- `auth.policy` = "Here's who can access the app and what they can do"

### Why did we consolidate `auth_policy` and `sub_auth` into `auth`?

We consolidated these into a single `auth` object for better organization and clarity:

1. **Related concerns**: Authentication and authorization are closely related - they work together to secure your app
2. **Better structure**: Having them nested under `auth.policy` and `auth.users` makes the relationship clear
3. **Cleaner configuration**: One place to configure all authentication/authorization needs
4. **Easier to understand**: The structure reflects the logical relationship between these features

**Before:**
```json
{
  "auth_policy": { ... },
  "sub_auth": { ... }
}
```

**After:**
```json
{
  "auth": {
    "policy": { ... },
    "users": { ... }
  }
}
```

The old format is still supported for backward compatibility, but the new format is recommended.

### What's the difference between `required` and `allow_anonymous`?

Both are in `auth.policy` and control access, but they work together:

- **`required`** (boolean): Whether authentication is mandatory
  - `required: true` = Users MUST be authenticated to access
  - `required: false` = Authentication is optional

- **`allow_anonymous`** (boolean): Whether unauthenticated users are explicitly allowed
  - `allow_anonymous: true` = Unauthenticated users CAN access
  - `allow_anonymous: false` = Unauthenticated users are blocked (if `required: true`)

**How they work together:**

| `required` | `allow_anonymous` | Result |
|------------|-------------------|--------|
| `true` | `false` | Authentication mandatory, anonymous blocked |
| `true` | `true` | Authentication preferred, but anonymous allowed |
| `false` | `false` | Authentication optional, but anonymous blocked (contradictory - defaults to allowing anonymous) |
| `false` | `true` | Authentication optional, anonymous explicitly allowed |

**Best practice:** If you want to allow anonymous access, set `required: false` and `allow_anonymous: true` for clarity.

## Usage Questions

### When should I use `auth.users` vs platform authentication?

**Use `auth.users` (app-level user management) when:**
- Your app needs its own user accounts separate from the platform
- Users register/login specifically for your app
- You want app-specific user profiles and data
- You need app-level sessions and cookies
- Your app is a standalone service with its own user base

**Use platform authentication when:**
- You want users to access multiple apps with one login
- You need cross-app user identity
- You're building a platform where users move between apps
- You want centralized user management

**You can use both:**
- Platform auth for cross-app access
- `auth.users` for app-specific features and data
- Use the `hybrid` strategy in `auth.users.strategy` to combine both

### How does `link_users_roles` work?

`link_users_roles` (in `auth.policy.authorization.link_users_roles`) automatically bridges authentication and authorization:

1. **When enabled**: Automatically assigns Casbin roles to app-level users when they are created or updated
2. **How it works**:
   - When a user is created via `auth.users`, the system checks if `link_users_roles: true`
   - If true, it automatically calls `sync_app_user_to_casbin()` to assign the user's role in Casbin
   - The user's `app_user_id` becomes the Casbin subject
   - The user's `role` field (from the user document) becomes the Casbin role

**Example:**
```json
{
  "auth": {
    "policy": {
      "authorization": {
        "link_users_roles": true,  // Auto-link users to roles
        "default_roles": ["user", "admin"]
      }
    },
    "users": {
      "enabled": true
    }
  }
}
```

When a user is created with `role: "admin"`, they automatically get the "admin" role in Casbin, allowing them to access resources that require admin permissions.

**Benefits:**
- No manual role assignment needed
- Users automatically get permissions based on their role
- Seamless integration between user management and authorization

### What's the difference between `strategy` options in `auth.users`?

The `strategy` field in `auth.users` determines how user authentication works:

- **`app_users`**: App-specific user accounts (default)
  - Users register/login with email/password
  - Accounts are stored in the app's users collection
  - Sessions are app-specific

- **`anonymous_session`**: Session-based anonymous users
  - No registration required
  - Users get anonymous IDs (e.g., "guest_abc123")
  - Useful for temporary access without accounts

- **`oauth`**: OAuth integration
  - Users authenticate via OAuth providers (Google, GitHub, etc.)
  - No password management needed
  - User data comes from OAuth provider

- **`hybrid`**: Combine platform auth with app-specific identities
  - Platform users can have app-specific profiles
  - Links platform authentication with app-level user data
  - Best of both worlds

## Migration Questions

### How do I migrate from the old format?

The system automatically migrates old manifests to the new format. However, for clarity, here's the mapping:

**Old format:**
```json
{
  "auth_policy": {
    "provider": "casbin",
    "required": true,
    "authorization": {
      "link_sub_auth_roles": true
    }
  },
  "sub_auth": {
    "enabled": true,
    "strategy": "app_users"
  }
}
```

**New format:**
```json
{
  "auth": {
    "policy": {
      "provider": "casbin",
      "required": true,
      "authorization": {
        "link_users_roles": true
      }
    },
    "users": {
      "enabled": true,
      "strategy": "app_users"
    }
  }
}
```

**Key changes:**
- `auth_policy` → `auth.policy`
- `sub_auth` → `auth.users`
- `link_sub_auth_roles` → `link_users_roles`
- `get_app_sub_user()` → `get_app_user()`

### Is backward compatibility maintained?

Yes! The system automatically detects and migrates old format manifests. You'll see a deprecation warning in the logs, but your app will continue to work. However, we recommend migrating to the new format for clarity and to avoid future issues.

## Function Naming

### Why was `get_app_sub_user` renamed to `get_app_user`?

The "sub" terminology was confusing and didn't accurately describe what the function does. The function retrieves app-level users, not "sub-users". The new name `get_app_user` is clearer and more intuitive.

**Migration:**
```python
# Old
from mdb_engine.auth import get_app_sub_user
user = await get_app_sub_user(request, slug_id, db, config)

# New
from mdb_engine.auth import get_app_user
user = await get_app_user(request, slug_id, db, config)
```

## Common Patterns

### How do I create a public app with optional login?

```json
{
  "auth": {
    "policy": {
      "required": false,
      "allow_anonymous": true
    },
    "users": {
      "enabled": true,
      "allow_registration": true
    }
  }
}
```

### How do I create a private app requiring authentication?

```json
{
  "auth": {
    "policy": {
      "required": true,
      "allow_anonymous": false
    },
    "users": {
      "enabled": true,
      "allow_registration": false  // Admin creates users
    }
  }
}
```

### How do I create an app with role-based access?

```json
{
  "auth": {
    "policy": {
      "required": true,
      "authorization": {
        "link_users_roles": true,
        "default_roles": ["user", "admin", "editor"],
        "allowed_roles": ["admin", "editor"]  // Only these roles can access
      }
    },
    "users": {
      "enabled": true,
      "allow_registration": true
    }
  }
}
```

