# MDB Engine Documentation

Welcome to the MDB Engine documentation. This directory contains comprehensive documentation for developers, contributors, and users.

## 🎯 Start Here: manifest.json

**`manifest.json` is the key to everything.** It's a single configuration file that defines your app's identity, data structure, authentication, indexes, and services. Everything in mdb-engine flows from your manifest.json.

### Essential Reading

1. **[Quick Start Guide](QUICK_START.md)** ⭐ **START HERE** - Learn how manifest.json works and get your first app running
2. **[Manifest Reference](MANIFEST_REFERENCE.md)** 📖 - Complete reference for all manifest.json fields and configurations
3. **[Manifest Deep Dive](MANIFEST_DEEP_DIVE.md)** - Comprehensive analysis of manifest.json, its convenience, and incremental adoption

### Understanding manifest.json

- **Minimal**: Start with just `slug`, `name`, and `schema_version`
- **Powerful**: Add indexes, auth, AI services, WebSockets as needed
- **Automatic**: Everything configured from your manifest.json automatically
- **Version Controlled**: Your entire app config in one file

## Documentation Structure

### Getting Started
- **[Quick Start Guide](QUICK_START.md)** ⭐ - Get up and running with manifest.json
- **[Manifest Reference](MANIFEST_REFERENCE.md)** 📖 - Complete reference for all manifest.json fields
- **[Manifest Deep Dive](MANIFEST_DEEP_DIVE.md)** - Deep analysis of manifest.json, its convenience, and incremental adoption
- **[Best Practices](BEST_PRACTICES.md)** - Dependency injection, patterns, and clean code
- **[Setup Guide](../SETUP.md)** - Development environment setup

### Guides
- **[Authorization Guide](AUTHZ.md)** - Authentication and authorization
- **[Memory Service Guide](MEMORY_SERVICE.md)** ⭐ - Complete guide to the memory service with automatic index management
- **[Cognitive Memory](COGNITIVE_MEMORY.md)** - Advanced cognitive memory features
- **[Cognitive Architecture](COGNITIVE_ARCHITECTURE.md)** - STM + LTM memory architecture
- **[Testing Guide](guides/testing.md)** - Comprehensive testing documentation
- **[Contributing Guide](../CONTRIBUTING.md)** - How to contribute to the project

### API Documentation
- API reference documentation (coming soon)

### Other Documentation
- **[FAQ](FAQ.md)** - Frequently asked questions

## Quick Links

- [Main README](../README.md)
- [Contributing Guidelines](../CONTRIBUTING.md)
- [Development Setup](../SETUP.md)
- [Test Suite](../tests/README.md) - Test structure and examples

## Documentation Standards

When adding documentation:

1. Use clear, concise language
2. Include code examples where helpful
3. Keep documentation up to date with code changes
4. Follow Markdown best practices
5. Link related documentation
