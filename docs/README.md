# MDB Engine Documentation

Welcome to the MDB Engine documentation. This directory contains comprehensive documentation for developers, contributors, and users.

## Start Here: manifest.json

**`manifest.json` is the key to everything.** It's a single configuration file that defines your app's identity, data structure, authentication, indexes, and services. Everything in mdb-engine flows from your manifest.json.

### Essential Reading

1. **[Beginner's Guide](BEGINNERS_GUIDE.md)** - Start here if you're new to the memory system
2. **[Manifest Reference](MANIFEST_REFERENCE.md)** - Complete reference for all manifest.json fields and configurations
3. **[MDB Engine 101](MDB_ENGINE_101.md)** - Complete LLM-assisted development guide

### Understanding manifest.json

- **Minimal**: Start with just `slug`, `name`, and `schema_version`
- **Powerful**: Add indexes, auth, AI services, WebSockets as needed
- **Automatic**: Everything configured from your manifest.json automatically
- **Version Controlled**: Your entire app config in one file

## Documentation Structure

### Getting Started
- **[Beginner's Guide](BEGINNERS_GUIDE.md)** - Get started with the memory system
- **[MDB Engine 101](MDB_ENGINE_101.md)** - Complete development guide
- **[Manifest Reference](MANIFEST_REFERENCE.md)** - Complete reference for all manifest.json fields
- **[Best Practices](BEST_PRACTICES.md)** - Dependency injection, patterns, and clean code

### Guides
- **[Memory Service Guide](MEMORY_SERVICE.md)** - Complete guide to the memory service (developer guide, API, Perfect Brain features)
- **[Customize the Cognitive Engine](CUSTOMIZE_COGNITIVE_ENGINE.md)** - Build your own scoring, decay, extraction, importance, persona, and reflection strategies
- **[Memory System Complete Reference](MEMORY_SYSTEM_COMPLETE.md)** - Technical architecture, data flows, and implementation details
- **[Context Engineering](CONTEXT_ENGINEERING.md)** - Context-engineered prompt construction
- **[Graph Service](GRAPH_SERVICE.md)** - Knowledge graph API reference
- **[GraphRAG](GRAPHRAG.md)** - GraphRAG implementation guide
- **[Deep Analysis](DEEP_ANALYSIS.md)** - Architecture deep dive
- **[Files and Buckets](guides/FILES_AND_BUCKETS.md)** - Memory bucket organization

### OSI (Open Semantic Interchange)
- **[OSI Overview](../OSI.md)** - Strategic analysis of OSI and MDB-Engine alignment
- **[OSI Integration Blueprint](../OSI_PROPOSED_INTEGRATION.md)** - Engineering blueprint (5 phases, all implemented)
- **[OSI Manifest Guide](../OSI_MANIFEST.md)** - Manifest configuration (3 tiers: one-liner, config section, inline models)

### Reference
- **[MDB Engine Bible](../MDB_ENGINE_BIBLE.md)** - The complete, authoritative guide to every feature
- **[LLM Reference](../llms.txt)** - LLM-optimized quick reference
- **[API Documentation](../docs.md)** - Primary API documentation

## Quick Links

- [Main README](../README.md)
- [Examples](../examples/README.md)
- [Test Suite](../tests/README.md) - Test structure and examples

## Documentation Standards

When adding documentation:

1. Use clear, concise language
2. Include code examples where helpful
3. Keep documentation up to date with code changes
4. Follow Markdown best practices
5. Link related documentation
