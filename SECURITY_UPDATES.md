# Security Updates

Security-specific changelog for mdb-engine. For general changes, see [CHANGELOG.md](./CHANGELOG.md).

---

## 0.8.9 — Security Patch (2026-03-19)

### Critical Fixes

- **Tenant isolation: blocked `app_id` tampering via update operations** —
  `ScopedCollectionWrapper.update_one` and `update_many` now validate that the
  update body does not contain operators (`$set`, `$unset`, `$rename`, etc.)
  targeting the `app_id` field. Previously, a scoped update with
  `{"$set": {"app_id": "other-tenant"}}` would pass through because only the
  *filter* was scoped, not the update payload. This could allow a compromised or
  malicious application-level caller to reassign documents across tenants.

- **Role evaluation: `user["roles"]` as a string split into characters** —
  `get_effective_roles` used `set(user.get("roles", []))`, which when given a
  string like `"admin"` produced `{'a','d','m','i','n'}` instead of `{"admin"}`.
  This could cause role checks to match single-character role names or fail to
  match the intended role. Now handles string, list, tuple, set, frozenset, and
  None safely.

- **`public_read` auth gate misconfigured** — When a collection declared
  `public_read: true` without explicit roles, the `_use_provider` flag was
  incorrectly activated (because `_public_read` was included in the condition).
  This forced the read router through `require_collection_permission`, which
  always demands an authenticated user — defeating the purpose of public reads.
  Anonymous GET requests returned 401 instead of 200.

### High-Severity Fixes

- **Pipeline validation: nested subpipelines not checked for dangerous stages** —
  `QueryValidator.validate_pipeline` only checked top-level pipeline stages for
  `$out`, `$merge`, and `$unionWith`. Subpipelines inside `$lookup`, `$facet`,
  and `$unionWith` were not recursively validated. An attacker could embed
  `$out` inside a `$lookup.pipeline` to write to arbitrary collections,
  bypassing tenant scoping. Validation now recurses into all nested subpipelines.

- **Tenant isolation: unscoped Motor methods forwarded via `__getattr__`** —
  `ScopedCollectionWrapper.__getattr__` forwarded several Motor methods directly
  to the underlying collection without `app_id` injection:
  - `distinct` — returns values across all tenants
  - `watch` — receives change events across all tenants
  - `find_raw_batches` / `aggregate_raw_batches` — return raw BSON across all tenants
  - `estimated_document_count` — counts all documents, not just the tenant's

  These are now blocked alongside the previously blocked methods (`bulk_write`,
  `replace_one`, `rename`, `drop`, etc.).

### Medium-Severity Fixes

- **Query parser: `nan`/`inf` accepted as float values** — `_coerce_value` used
  `float()` which accepts `"nan"`, `"inf"`, and `"-inf"`. These values produce
  surprising comparison behavior in MongoDB (e.g., `{"$gt": NaN}` matches
  nothing; `{"$lt": Infinity}` matches everything). Values that parse to
  non-finite floats are now returned as raw strings.

- **Semgrep false positive on code comments** — The `mdb-no-dangerous-operator-
  literals` rule flagged a comment in `auto_crud.py` that mentioned `"$where"` as
  an example. Reworded the comment to avoid triggering the regex-based rule.

### Security Infrastructure (added in 0.8.8 development)

- **Static analysis** — 9 custom Semgrep rules in `.semgrep-security.yml`
  targeting mdb-engine-specific antipatterns: raw MongoDB access, private `_db`
  field access, dangerous operator literals, `$out`/`$merge` in app code,
  `eval`/`exec`, manual JWT decode, hardcoded secrets, shared client misuse,
  and unsanitized request body insertion.

- **Dynamic security tests** — 103 adversarial tests across 5 new test files:
  - `test_mql_injection.py` — MQL injection via filters, operators, and nested
    structures
  - `test_scope_bypass.py` — tenant isolation bypass attempts via
    `ScopedCollectionWrapper` and `ScopedMongoWrapper`
  - `test_template_injection.py` — template resolver adversarial inputs
    (`{{user.*}}`, `{{env.*}}`, `{{doc.*}}`)
  - `test_auth_bypass.py` — JWT algorithm confusion, token tampering, role
    escalation
  - `test_aggregation_security.py` — dangerous pipeline stages, cross-tenant
    `$lookup`, operator smuggling

- **Code hardening** —
  - `QueryValidator.validate_pipeline` blocks `$out`, `$merge`, `$unionWith`
    pipeline stages (cross-tenant write/read risk)
  - `auto_crud.sanitize_body` strips all `$`-prefixed keys from request bodies
    (operator injection via document fields)
  - `ScopedCollectionWrapper.__getattr__` blocks `bulk_write`, `replace_one`,
    and other unscoped Motor methods
  - `template_resolver` env var denylist prevents leaking secrets via
    `{{env.*}}` templates

- **CI integration** — `make test-security` target runs the full pipeline:
  Semgrep (custom + exception rules), Bandit, pip-audit, security test suite,
  and security-related unit tests. GitHub Actions `security.yml` workflow updated
  with `framework-security` job.

---

## Threat Model Coverage

| Attack Vector | Prevention Layer | Tests |
|---|---|---|
| MQL injection ($where, $eval, $function) | QueryValidator + Semgrep rules | 22 tests |
| Tenant isolation bypass | ScopedCollectionWrapper + blocked methods | 17 tests |
| Pipeline data exfiltration ($out, $merge) | QueryValidator + nested validation | 17 tests |
| Template injection (env/user/doc) | template_resolver denylist + depth limits | 18 tests |
| Auth bypass (JWT, roles, public_read) | dependencies.py + auto_crud auth gates | 12 tests |
| Update body scope tampering | _validate_update_no_scope_tampering | Runtime enforcement |
| Non-finite float injection (nan/inf) | _coerce_value guard | Runtime enforcement |
