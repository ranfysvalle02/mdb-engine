"""Tests for mdb_engine.env — canonical environment variable reader."""

import warnings


class TestGetEnv:
    """Tests for the get_env() function."""

    def setup_method(self):
        """Clear the warned-set between tests so warnings fire again."""
        import mdb_engine.env as env_mod

        env_mod._warned.clear()

    def test_reads_canonical_name(self, monkeypatch):
        from mdb_engine.env import get_env

        monkeypatch.setenv("MY_CANONICAL", "canonical_value")
        assert get_env("MY_CANONICAL") == "canonical_value"

    def test_falls_back_to_deprecated(self, monkeypatch):
        from mdb_engine.env import get_env

        monkeypatch.delenv("MY_CANONICAL", raising=False)
        monkeypatch.setenv("OLD_NAME", "old_value")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = get_env("MY_CANONICAL", deprecated=["OLD_NAME"])
            assert result == "old_value"
            assert len(w) == 1
            assert "deprecated" in str(w[0].message).lower()
            assert "OLD_NAME" in str(w[0].message)
            assert "MY_CANONICAL" in str(w[0].message)

    def test_canonical_takes_priority_over_deprecated(self, monkeypatch):
        from mdb_engine.env import get_env

        monkeypatch.setenv("MY_CANONICAL", "canonical_value")
        monkeypatch.setenv("OLD_NAME", "old_value")
        assert get_env("MY_CANONICAL", deprecated=["OLD_NAME"]) == "canonical_value"

    def test_returns_default_when_nothing_set(self, monkeypatch):
        from mdb_engine.env import get_env

        monkeypatch.delenv("MY_CANONICAL", raising=False)
        monkeypatch.delenv("OLD_NAME", raising=False)
        assert get_env("MY_CANONICAL", deprecated=["OLD_NAME"], default="fallback") == "fallback"

    def test_returns_none_when_nothing_set_no_default(self, monkeypatch):
        from mdb_engine.env import get_env

        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert get_env("NONEXISTENT_VAR") is None

    def test_warning_emitted_only_once(self, monkeypatch):
        from mdb_engine.env import get_env

        monkeypatch.delenv("MY_CANONICAL", raising=False)
        monkeypatch.setenv("OLD_NAME", "old_value")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            get_env("MY_CANONICAL", deprecated=["OLD_NAME"])
            get_env("MY_CANONICAL", deprecated=["OLD_NAME"])
            dep_warnings = [x for x in w if "OLD_NAME" in str(x.message)]
            assert len(dep_warnings) == 1


class TestConvenienceFunctions:
    """Tests for get_mongo_uri, get_db_name, get_jwt_secret."""

    def setup_method(self):
        import mdb_engine.env as env_mod

        env_mod._warned.clear()

    def test_get_mongo_uri_canonical(self, monkeypatch):
        from mdb_engine.env import get_mongo_uri

        monkeypatch.setenv("MDB_MONGO_URI", "mongodb://canonical:27017")
        monkeypatch.delenv("MONGODB_URI", raising=False)
        assert get_mongo_uri() == "mongodb://canonical:27017"

    def test_get_mongo_uri_deprecated_fallback(self, monkeypatch):
        from mdb_engine.env import get_mongo_uri

        monkeypatch.delenv("MDB_MONGO_URI", raising=False)
        monkeypatch.setenv("MONGODB_URI", "mongodb://old:27017")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert get_mongo_uri() == "mongodb://old:27017"

    def test_get_mongo_uri_default(self, monkeypatch):
        from mdb_engine.env import get_mongo_uri

        monkeypatch.delenv("MDB_MONGO_URI", raising=False)
        monkeypatch.delenv("MONGODB_URI", raising=False)
        monkeypatch.delenv("MONGO_URI", raising=False)
        assert get_mongo_uri() == "mongodb://localhost:27017"

    def test_get_db_name_canonical(self, monkeypatch):
        from mdb_engine.env import get_db_name

        monkeypatch.setenv("MDB_DB_NAME", "my_db")
        assert get_db_name() == "my_db"

    def test_get_db_name_default(self, monkeypatch):
        from mdb_engine.env import get_db_name

        monkeypatch.delenv("MDB_DB_NAME", raising=False)
        monkeypatch.delenv("MONGODB_DB", raising=False)
        monkeypatch.delenv("MONGO_DB_NAME", raising=False)
        monkeypatch.delenv("DB_NAME", raising=False)
        assert get_db_name() == "mdb_engine"

    def test_get_jwt_secret_canonical(self, monkeypatch):
        from mdb_engine.env import get_jwt_secret

        monkeypatch.setenv("MDB_JWT_SECRET", "supersecret")
        assert get_jwt_secret() == "supersecret"

    def test_get_jwt_secret_none_when_unset(self, monkeypatch):
        from mdb_engine.env import get_jwt_secret

        for var in ["MDB_JWT_SECRET", "MDB_ENGINE_JWT_SECRET", "FLASK_SECRET_KEY", "SECRET_KEY", "APP_SECRET_KEY"]:
            monkeypatch.delenv(var, raising=False)
        assert get_jwt_secret() is None
