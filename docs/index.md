# MDB Engine

**Zero-config MongoDB runtime with scoping, auth, memory, and GraphRAG.**

MDB Engine is a Python framework that turns a single `manifest.json` into a fully operational MongoDB-backed application with scoped database access, authentication, memory services, and graph-based RAG.

## Quick Links

- [Quick Start](QUICK_START.md) -- Get running in under 5 minutes
- [MDB Engine 101](MDB_ENGINE_101.md) -- Complete development guide
- [Manifest Reference](MANIFEST_REFERENCE.md) -- Every configuration option explained
- [API Reference](api/core.md) -- Auto-generated from source docstrings

## Installation

```bash
pip install mdb-engine
```

With optional AI features:

```bash
pip install mdb-engine[ai]
```

## Minimal Example

```python
from mdb_engine import quickstart

app = quickstart()
```

That's it. Define your `manifest.json`, point at a MongoDB instance, and you have a FastAPI app with scoped collections, auth, and more.
