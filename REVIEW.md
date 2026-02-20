# mdb-engine — Framework Review

**Version reviewed:** 0.7.10  
**Date:** February 18, 2026  
**Codebase:** ~70,800 lines across 183 Python files, 40 subpackages  

---

## Scoring Summary

| Category | Score | Weight |
|----------|:-----:|:------:|
| Architecture & Design | 8.5 / 10 | ★★★ |
| Code Quality | 8.0 / 10 | ★★★ |
| Security | 9.0 / 10 | ★★★ |
| Developer Experience | 9.0 / 10 | ★★ |
| Testing | 8.5 / 10 | ★★★ |
| Documentation | 9.0 / 10 | ★★ |
| Examples & Onboarding | 9.0 / 10 | ★★ |
| Feature Completeness | 9.5 / 10 | ★★ |
| Maintainability | 7.5 / 10 | ★★ |
| Production Readiness | 8.0 / 10 | ★★ |

### **Overall: 8.5 / 10**

---

## 1. Architecture & Design — 8.5 / 10

### What works

- **Layered architecture** is clean and intentional. Database → Repository → Service → Dependency → Route. Each layer has a clear contract.
- **Manifest-driven configuration** is a strong differentiator. A single `manifest.json` declares identity, indexes, auth, AI services, CORS, and WebSockets. This eliminates scattered config files and makes apps declarative.
- **Progressive adoption** (Layer 0–3) means the framework doesn't overwhelm. `quickstart("my_app")` gets you running in 3 lines. Auth, memory, GraphRAG layer on incrementally.
- **Mixin-based engine** (`ScopedAccessMixin`, `AppLifecycleMixin`, `FastAPIAppMixin`, `MultiAppMixin`, `GDPRMixin`, `WebSocketMixin`) keeps `MongoDBEngine` extensible without inheritance hell.
- **Protocol-based abstractions** (`MemoryServiceProtocol`, `EmbeddingServiceProtocol`, `AuthorizationProvider`) make services swappable.
- **Scoped data isolation** is the killer feature. Every query gets `app_id` injected automatically. Developers literally cannot leak data across tenants without going out of their way.
- **Dual data-access styles** — Motor-like wrapper (`get_scoped_db`) for quick work, Repository/UnitOfWork pattern (`Entity + UnitOfWork`) for domain-driven design. Both auto-scoped.

### What needs work

- **`MongoDBEngine` is trending toward god-class territory.** Even with mixins, it orchestrates connection management, app registration, index creation, auth wiring, GDPR, and WebSocket setup. Consider extracting an `AppRegistry` or `EngineBuilder`.
- **Large files exist.** `multi_app.py` at 2,250+ lines and `scoped_wrapper.py` at 2,490+ lines are hard to navigate. These should be split into focused modules.
- **Circular import workarounds** (`TYPE_CHECKING` blocks) appear in multiple modules, suggesting coupling that could be reduced with interface extraction.

---

## 2. Code Quality — 8.0 / 10

### What works

- **Type hints everywhere.** Modern Python 3.10+ union syntax (`str | None`), generic types (`Repository[T]`), and `TYPE_CHECKING` guards.
- **Custom exception hierarchy** with context. `MongoDBEngineError` → `InitializationError`, `ManifestValidationError`, `ConfigurationError`, `QueryValidationError`. Exceptions carry actionable messages.
- **Consistent naming.** `get_scoped_db`, `get_memory_service`, `require_user`, `require_role` — the API reads like plain English.
- **Linting is enforced.** Ruff with pycodestyle, pyflakes, isort, bugbear, mccabe, pyupgrade, blind-except, tryceratops, and private-member-access rules. McCabe complexity capped at 25.
- **Docstrings on public APIs** with examples in the skill guide.

### What needs work

- **mypy is in "suppress everything" mode.** 661 existing errors across 91 files are blanket-ignored. The `ignore_errors = true` override on nearly every subpackage means mypy provides zero value today. This is technical debt that should be chipped away module by module.
- **Some files exceed complexity limits** and are explicitly exempted in `pyproject.toml` (`casbin_factory.py`, `csrf.py`, `app_lifecycle.py`, `websockets.py`). These are acknowledged but not yet addressed.
- **Coverage floor is 67%.** Solid for beta, but should target 80%+ before 1.0.

---

## 3. Security — 9.0 / 10

### What works

- **JWT implementation is thorough:** format validation before decoding, token versioning, JTI for revocation, blacklist with MongoDB TTL indexes, support for HS256/RS256/ES256, token rotation, session fingerprinting.
- **Password hashing** uses bcrypt with auto-salt. Rejects non-bcrypt hashes. Configurable strength validation with optional breach checking.
- **CSRF protection** via double-submit cookie pattern with optional HMAC signing. Configurable exempt routes. WebSocket origin validation (CSWSH prevention).
- **Cookie security** is environment-aware — auto-detects HTTPS to set `Secure` flag. HttpOnly, SameSite=Lax by default.
- **Envelope encryption** (AES-256-GCM) with master key + per-secret DEK pattern for field-level encryption.
- **Rate limiting** with sliding window algorithm, IP + email tracking, in-memory and MongoDB-backed stores.
- **Input validation:** path normalization (traversal prevention), email format validation, next-URL sanitization (open redirect prevention), query validation, resource limiting, collection name validation.
- **GDPR compliance** is a first-class feature: data discovery, export (Right to Access), deletion (Right to Erasure with hard/soft/anonymize strategies), and rectification.
- **Fail-closed defaults.** Token blacklist defaults to deny. Strict session fingerprinting. No information leakage in error responses.

### What needs work

- **Argon2id** should be offered as an alternative to bcrypt for new deployments.
- **Master key rotation** procedures need documentation.
- **WebSocket connection limits** per user/IP are not implemented.
- **Distributed rate limiting** (Redis-backed) would help at scale.

---

## 4. Developer Experience — 9.0 / 10

### What works

- **3-line quickstart** is real and works:
  ```python
  from mdb_engine import quickstart
  app = quickstart("my_app")
  ```
- **`RequestContext`** is a brilliant DX choice — one dependency gives you `db`, `uow`, `user`, `memory`, `llm`, `authz`, `profile`, `embedding_service`, and more. Lazy-loaded so unused services cost nothing.
- **`mdb-engine doctor`** CLI validates MongoDB connectivity, env vars, API keys, and manifest schemas. This alone saves hours of debugging.
- **`mdb-engine new-app`** scaffolds projects with manifest, Docker Compose, and web.py.
- **Env var helpers** (`get_mongo_uri()`, `get_db_name()`, `get_jwt_secret()`) check canonical + all deprecated aliases and emit deprecation warnings. Much better than raw `os.getenv()`.
- **Auto-registered SSO routes.** In 0.7.10, `/auth/callback` and `/logout` are auto-wired — apps no longer need 50+ lines of boilerplate.
- **Testing utilities** (`create_test_client`, `mock_scoped_db`, `mock_user`) make it easy to test without MongoDB.
- **`recurring_task` decorator** with exponential backoff for background jobs.
- **CORS dev defaults** (localhost:8000–8009) eliminate a common friction point.

### What needs work

- **No hot-reload story** for manifest changes — requires restart.
- **Error messages for misconfigured manifests** could include fix suggestions.
- **No interactive REPL or shell** for exploring the database through the engine's scoped lens.

---

## 5. Testing — 8.5 / 10

### What works

- **98 test files, ~433 test functions/classes.** Good coverage breadth.
- **Three test tiers:** unit (mocked), integration (testcontainers with real MongoDB), performance.
- **Async-native:** All async tests use `@pytest.mark.asyncio` with proper event loop fixtures.
- **Strong fixture system:** `conftest.py` provides mock MongoDB client/database/collection, real MongoDB via testcontainers, sample manifests, encryption fixtures, and env var reset.
- **Edge cases covered:** connection failures, validation errors, double initialization, uninitialized access, cross-app access attempts, GDPR workflows, WebSocket auth.
- **Custom mock helpers:** `MockDatabaseWrapper` for dynamic collection access, `mock_scoped_db` for prefilled data.
- **Parallel execution** supported via `pytest-xdist`.

### What needs work

- **Large test files** (`test_scoped_wrapper.py` at 2,500+ lines) mirror the large source files they test. Should be split.
- **Performance test coverage is thin** — only 2 files. Hot paths (scoped queries, memory search, JWT validation) should be benchmarked.
- **No E2E tests** that exercise a full request lifecycle through a running app.
- **Coverage floor of 67%** suggests gaps. The `coverage.report.fail_under = 67` should ratchet up to 80%.
- **Some tests lack docstrings** explaining what scenario they verify.

---

## 6. Documentation — 9.0 / 10

### What works

- **51+ markdown files** organized into guides, API docs, and reference.
- **Progressive structure:** Beginner's Guide (with real-world analogies) → Quick Start → Feature guides → Advanced topics.
- **Manifest Reference** is 1,200+ lines covering every config option.
- **Specialized guides:** Memory System Complete Reference, GraphRAG, Context Engineering, OSI integration, GDPR compliance, Production Deployment, Security Deep Dive.
- **Troubleshooting guides** for CORS, WebSocket, and CSRF.
- **Architecture diagrams** (Mermaid) in key docs.
- **Upgrade guide** (UPGRADE-0.7.10.md) is exemplary — quick checklist, before/after code, migration script, rationale for every breaking change.
- **Cursor AI skill file** (SKILL.md) is a comprehensive cheat sheet that enables AI assistants to write correct mdb-engine code.

### What needs work

- **No generated API reference** (e.g., mkdocstrings output). The `docs` dependency group includes mkdocs-material but no built site is published.
- **No changelog** beyond upgrade guides.
- **Search/index** across docs is missing.
- **Some API docs** could use more parameter descriptions and return type documentation.

---

## 7. Examples & Onboarding — 9.0 / 10

### What works

- **17 example projects** spanning basic → advanced.
- **Clear learning ladder:**
  - `hello_world` (15 lines) → `memory_quickstart` (25 lines) → `chit_chat` (2,400 lines) → `sso-multi-app` (production multi-tenant SSO).
- **Every example includes:** README, Docker Compose, `.env.example`, manifest.json.
- **Real-world patterns:** OAuth demo, GDPR demo, WebSocket ticket auth, vector search/hacking, multi-app SSO.
- **Docker Compose files** are production-like (MongoDB service, env vars, volume mounts).

### What needs work

- **No dedicated testing example** showing how to test an mdb-engine app.
- **No Kubernetes/cloud deployment example.**
- **No CI/CD example** (GitHub Actions workflow for an mdb-engine app).
- **Example validation tests exist** but aren't run in CI.

---

## 8. Feature Completeness — 9.5 / 10

### Feature inventory

| Feature | Status | Notes |
|---------|--------|-------|
| Auto-scoped DB access | ✅ Complete | Killer feature — `app_id` injected on every query |
| Repository/UoW pattern | ✅ Complete | Type-safe, generic, with `Entity` base class |
| Manifest-driven config | ✅ Complete | Schema-validated, versioned |
| Declarative index management | ✅ Complete | Regular, text, vector, TTL, compound |
| JWT authentication | ✅ Complete | Multi-algorithm, rotation, blacklist |
| RBAC (Casbin + OSO) | ✅ Complete | Pluggable providers, role hierarchy |
| SSO / SharedUserPool | ✅ Complete | Cross-app auth with per-app roles |
| OAuth integration | ✅ Complete | Via authlib |
| CSRF protection | ✅ Complete | Double-submit + HMAC |
| Rate limiting | ✅ Complete | Sliding window, multi-store |
| Memory service (AI) | ✅ Complete | Add, search, inject, cognitive features |
| ChatEngine | ✅ Complete | STM + LTM orchestration |
| Embeddings | ✅ Complete | OpenAI + Azure |
| GraphRAG | ✅ Complete | Knowledge graph with $graphLookup |
| GDPR compliance | ✅ Complete | Discovery, export, delete, rectify |
| WebSocket support | ✅ Complete | Ticket auth, rooms, broadcasting |
| DI container | ✅ Complete | Singleton/Request/Transient scopes |
| Background tasks | ✅ Complete | Recurring with backoff |
| CLI tooling | ✅ Complete | doctor, new-app, validate, migrate |
| OpenTelemetry | ✅ Complete | Traces, metrics, FastAPI + PyMongo instrumentation |
| Envelope encryption | ✅ Complete | AES-256-GCM, DEK pattern |
| Multi-app platform | ✅ Complete | Shared DB, cross-app navigation |
| Profile service | ✅ Complete | User preference management |
| Structured logging | ✅ Complete | JSON (prod) / human (dev), correlation IDs |

For a beta framework, this is an unusually complete feature set. Most frameworks at this stage offer DB access + auth and call it done.

---

## 9. Maintainability — 7.5 / 10

### What works

- **Modular package structure** with clear subpackages (auth, core, database, memory, graph, etc.).
- **Mixin pattern** keeps `MongoDBEngine` from being a 10,000-line monolith.
- **Protocol-based abstractions** mean new providers can be added without touching existing code.
- **Ruff + mypy + semgrep** in the toolchain.
- **Per-file complexity exemptions** are tracked for future refactoring.

### What needs work

- **mypy is effectively disabled** (`ignore_errors = true` on all internal modules). This is the single biggest maintainability risk. Type errors compound over time.
- **5 files exceed 2,000 lines.** Large files are correlated with merge conflicts, longer review times, and harder onboarding.
- **40 subpackages for 183 files** is a lot of surface area. Some consolidation (e.g., merging `profile/` into `memory/`, `indexes/` into `core/`) could reduce cognitive load.
- **Optional dependency handling** (`try/except ImportError` scattered across modules) adds conditional paths that are hard to test exhaustively.
- **No architecture decision records** (ADRs). When someone asks "why mixins instead of composition?" there's no written answer.

---

## 10. Production Readiness — 8.0 / 10

### What works

- **Connection pool monitoring** and health checks.
- **Structured JSON logging** in production mode with correlation IDs.
- **OpenTelemetry instrumentation** for distributed tracing.
- **Resource limiting** and query validation to prevent abuse.
- **Graceful shutdown** handling for background tasks and connections.
- **Docker Compose examples** that mirror real deployments.
- **Envelope encryption** for sensitive data at rest.

### What needs work

- **Beta status** (PyPI classifier: `Development Status :: 4 - Beta`). Breaking changes are still expected. The UPGRADE-0.7.10 doc explicitly says "backward compatibility is not a goal."
- **No published benchmarks.** How many concurrent connections? What's the p99 latency overhead of scoped queries?
- **No documented scaling guidance.** Horizontal scaling, connection pool tuning, Atlas cluster sizing.
- **No Kubernetes manifests** or Helm chart.
- **Coverage at 67%** leaves production paths potentially untested.
- **PyPI presence is early.** No download badges, no community adoption metrics visible.

---

## Verdict

**mdb-engine is an ambitious, well-designed framework that delivers on its core promise: make MongoDB-backed Python apps safe, fast to build, and feature-rich.**

The scoped data isolation alone justifies its existence — it eliminates an entire class of multi-tenant bugs. The progressive Layer 0–3 adoption model means you can use it as a simple DB wrapper or as a full AI-powered platform. The manifest-driven approach is clean and opinionated in the right ways.

The main risks are typical of a pre-1.0 project: disabled type checking, large files that resist refactoring, breaking changes between versions, and no production benchmarks. But the trajectory is strong — the 0.7.10 release shows a clear focus on eliminating boilerplate, hardening defaults, and shipping developer tooling (CLI, testing utilities, env helpers).

### Biggest strengths
1. **Automatic data isolation** — the scoped query system is genuinely innovative for the Python/MongoDB ecosystem
2. **Feature density** — auth, AI memory, GraphRAG, GDPR, WebSockets, DI, encryption, observability in one package
3. **Developer experience** — 3-line quickstart to production SSO is a smooth ramp
4. **Security posture** — defense-in-depth with fail-closed defaults

### Biggest risks
1. **mypy disabled** — type safety exists in annotations but isn't enforced
2. **Large files** — 5+ files over 2,000 lines will slow refactoring velocity
3. **Pre-1.0 instability** — breaking changes are intentional and ongoing
4. **No benchmarks** — performance characteristics are unknown

### Recommendation

**For new MongoDB + Python projects: strongly consider it.** The DX advantage over raw Motor/FastAPI is significant, and the security defaults alone save weeks of work.

**For existing production apps: wait for 1.0** unless you're willing to absorb breaking changes between versions. Pin your version and read upgrade guides carefully.

---

*Review conducted by analyzing the full source tree, test suite, documentation, examples, and dependency configuration.*
