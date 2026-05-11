"""Tests for vector_search.py.

Covers P1 (pure functions), P2 (hybrid merge/type weights/temporal decay),
P3 (DB operations). _SQLITE_VEC_AVAILABLE=False fixed (Python fallback only).
"""

import math
import random
import sqlite3
import struct
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Add parent directory to path so we can import vector_search
sys.path.insert(0, str(Path(__file__).parent.parent))
import vector_search


# --- Fixtures ---

@pytest.fixture(autouse=True)
def disable_sqlite_vec(monkeypatch):
    """Force Python fallback path for all tests."""
    monkeypatch.setattr(vector_search, "_SQLITE_VEC_AVAILABLE", False)


@pytest.fixture
def db():
    """Provide an in-memory SQLite connection with vec_store table."""
    conn = sqlite3.connect(":memory:")
    vector_search.create_vector_tables(conn, dimensions=3, use_sqlite_vec=False)
    vector_search.create_doc_map_table(conn)
    yield conn
    conn.close()


# ============================================================
# P1: Pure functions
# ============================================================

class TestVectorToBlob:
    """vector_to_blob / blob_to_vector round-trip tests."""

    def test_roundtrip_simple(self):
        vec = [1.0, 2.0, 3.0]
        blob = vector_search.vector_to_blob(vec)
        result = vector_search.blob_to_vector(blob, 3)
        assert result == pytest.approx(vec)

    def test_roundtrip_empty(self):
        vec = []
        blob = vector_search.vector_to_blob(vec)
        assert blob == b""
        result = vector_search.blob_to_vector(blob, 0)
        assert result == []

    def test_roundtrip_single(self):
        vec = [42.5]
        blob = vector_search.vector_to_blob(vec)
        result = vector_search.blob_to_vector(blob, 1)
        assert result == pytest.approx(vec)

    def test_roundtrip_negative_values(self):
        vec = [-1.0, 0.0, 1.0]
        blob = vector_search.vector_to_blob(vec)
        result = vector_search.blob_to_vector(blob, 3)
        assert result == pytest.approx(vec)

    def test_blob_is_float32(self):
        vec = [1.0, 2.0]
        blob = vector_search.vector_to_blob(vec)
        # float32 = 4 bytes each
        assert len(blob) == 8
        assert blob == struct.pack("ff", 1.0, 2.0)


class TestVectorNorm:
    """_vector_norm tests."""

    def test_zero_vector(self):
        assert vector_search._vector_norm([0.0, 0.0, 0.0]) == 0.0

    def test_unit_vector(self):
        assert vector_search._vector_norm([1.0, 0.0, 0.0]) == pytest.approx(1.0)

    def test_known_value(self):
        # sqrt(3^2 + 4^2) = 5
        assert vector_search._vector_norm([3.0, 4.0]) == pytest.approx(5.0)

    def test_all_ones(self):
        vec = [1.0, 1.0, 1.0]
        assert vector_search._vector_norm(vec) == pytest.approx(math.sqrt(3.0))


class TestCosineSimilarity:
    """_cosine_similarity tests."""

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        # cosine=1.0 -> normalized (1+1)/2 = 1.0
        assert vector_search._cosine_similarity(v, v) == pytest.approx(1.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        # cosine=-1.0 -> normalized (-1+1)/2 = 0.0
        assert vector_search._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        # cosine=0.0 -> normalized (0+1)/2 = 0.5
        assert vector_search._cosine_similarity(a, b) == pytest.approx(0.5)

    def test_zero_vector_a(self):
        assert vector_search._cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_zero_vector_b(self):
        assert vector_search._cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_precomputed_a_norm(self):
        a = [3.0, 4.0]
        b = [3.0, 4.0]
        a_norm = 5.0
        result = vector_search._cosine_similarity(a, b, a_norm=a_norm)
        assert result == pytest.approx(1.0)

    def test_result_range(self):
        """Result should always be in [0, 1]."""
        random.seed(42)
        for _ in range(20):
            a = [random.uniform(-10, 10) for _ in range(5)]
            b = [random.uniform(-10, 10) for _ in range(5)]
            sim = vector_search._cosine_similarity(a, b)
            assert 0.0 <= sim <= 1.0 + 1e-9


class TestMinMaxNormalize:
    """_min_max_normalize tests."""

    def test_empty_dict(self):
        assert vector_search._min_max_normalize({}) == {}

    def test_single_element(self):
        result = vector_search._min_max_normalize({"a": 5.0})
        assert result == {"a": 1.0}

    def test_all_same_values(self):
        result = vector_search._min_max_normalize({"a": 3.0, "b": 3.0, "c": 3.0})
        assert all(v == 1.0 for v in result.values())

    def test_normal_case(self):
        result = vector_search._min_max_normalize({"a": 0.0, "b": 5.0, "c": 10.0})
        assert result["a"] == pytest.approx(0.0)
        assert result["b"] == pytest.approx(0.5)
        assert result["c"] == pytest.approx(1.0)

    def test_negative_values(self):
        result = vector_search._min_max_normalize({"x": -10.0, "y": 0.0, "z": 10.0})
        assert result["x"] == pytest.approx(0.0)
        assert result["y"] == pytest.approx(0.5)
        assert result["z"] == pytest.approx(1.0)

    def test_two_elements(self):
        result = vector_search._min_max_normalize({"lo": 2.0, "hi": 8.0})
        assert result["lo"] == pytest.approx(0.0)
        assert result["hi"] == pytest.approx(1.0)


class TestComputeDecayFactor:
    """_compute_decay_factor tests."""

    def _now(self):
        return datetime(2026, 3, 22, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_timestamp(self):
        assert vector_search._compute_decay_factor(None, self._now(), 30.0, 0.2) == 1.0

    def test_empty_string(self):
        assert vector_search._compute_decay_factor("", self._now(), 30.0, 0.2) == 1.0

    def test_invalid_format(self):
        assert vector_search._compute_decay_factor("not-a-date", self._now(), 30.0, 0.2) == 1.0

    def test_future_timestamp(self):
        future = "2026-03-23T12:00:00+00:00"
        assert vector_search._compute_decay_factor(future, self._now(), 30.0, 0.2) == 1.0

    def test_same_time(self):
        ts = "2026-03-22T12:00:00+00:00"
        assert vector_search._compute_decay_factor(ts, self._now(), 30.0, 0.2) == 1.0

    def test_exactly_half_life(self):
        # 30 days ago -> factor = 2^(-1) = 0.5
        ts_30_days_ago = "2026-02-20T12:00:00+00:00"
        result = vector_search._compute_decay_factor(ts_30_days_ago, self._now(), 30.0, 0.2)
        assert result == pytest.approx(0.5)

    def test_double_half_life(self):
        # 60 days ago -> factor = 2^(-2) = 0.25
        ts_60_days_ago = "2026-01-21T12:00:00+00:00"
        result = vector_search._compute_decay_factor(ts_60_days_ago, self._now(), 30.0, 0.2)
        assert result == pytest.approx(0.25)

    def test_floor_applied(self):
        # 300 days ago -> raw factor very small, floor=0.2 applied
        ts_old = "2025-05-27T12:00:00+00:00"
        result = vector_search._compute_decay_factor(ts_old, self._now(), 30.0, 0.2)
        assert result == pytest.approx(0.2)

    def test_z_suffix_timestamp(self):
        ts = "2026-02-20T12:00:00Z"
        result = vector_search._compute_decay_factor(ts, self._now(), 30.0, 0.2)
        assert result == pytest.approx(0.5)

    def test_naive_timestamp_treated_as_utc(self):
        ts = "2026-02-20T12:00:00"
        result = vector_search._compute_decay_factor(ts, self._now(), 30.0, 0.2)
        assert result == pytest.approx(0.5)


# ============================================================
# P2: Hybrid merge, type weights, temporal decay
# ============================================================

class TestHybridMerge:
    """hybrid_merge tests."""

    def test_empty_inputs(self):
        assert vector_search.hybrid_merge([], []) == []

    def test_fts_only(self):
        fts = [{"doc_id": "a", "score": 5.0}, {"doc_id": "b", "score": 3.0}]
        result = vector_search.hybrid_merge(fts, [])
        assert result == fts

    def test_vec_only(self):
        vec = [("a", 0.9), ("b", 0.5)]
        result = vector_search.hybrid_merge([], vec)
        assert len(result) == 2
        assert result[0]["doc_id"] == "a"
        assert result[0]["_vec_only"] is True
        assert result[0]["fts_raw_score"] is None

    def test_both_results_same_doc(self):
        fts = [{"doc_id": "a", "score": 10.0}]
        vec = [("a", 0.8)]
        result = vector_search.hybrid_merge(fts, vec)
        assert len(result) == 1
        assert result[0]["doc_id"] == "a"
        # Both normalized to 1.0 (single element), hybrid = 1.0*0.3 + 1.0*0.7 = 1.0
        assert result[0]["score"] == pytest.approx(1.0)

    def test_both_results_different_docs(self):
        fts = [{"doc_id": "a", "score": 10.0}]
        vec = [("b", 0.8)]
        result = vector_search.hybrid_merge(fts, vec)
        # FTS result "a" is included, vec-only "b" is NOT in result
        # (vec-only docs are excluded from hybrid merge per code comment)
        assert len(result) == 1
        assert result[0]["doc_id"] == "a"

    def test_score_breakdown_fields(self):
        fts = [{"doc_id": "x", "score": 5.0}]
        vec = [("x", 0.7)]
        result = vector_search.hybrid_merge(fts, vec)
        r = result[0]
        assert "fts_raw_score" in r
        assert "vec_raw_score" in r
        assert "fts_weight" in r
        assert "vec_weight" in r
        assert r["fts_raw_score"] == 5.0
        assert r["vec_raw_score"] == 0.7

    def test_custom_weights(self):
        fts = [{"doc_id": "a", "score": 10.0}]
        vec = [("a", 0.8)]
        result = vector_search.hybrid_merge(fts, vec, vector_weight=0.5, fts_weight=0.5)
        # Both normalize to 1.0 (single element), hybrid = 0.5 + 0.5 = 1.0
        assert result[0]["score"] == pytest.approx(1.0)

    def test_sorted_by_score(self):
        fts = [
            {"doc_id": "a", "score": 1.0},
            {"doc_id": "b", "score": 10.0},
        ]
        vec = [("a", 0.1), ("b", 0.9)]
        result = vector_search.hybrid_merge(fts, vec)
        assert result[0]["doc_id"] == "b"
        assert result[1]["doc_id"] == "a"


class TestApplyTypeWeights:
    """apply_type_weights tests."""

    def test_empty_inputs(self):
        assert vector_search.apply_type_weights([], []) == []

    def test_episode_weights(self):
        fts = [{"doc_id": "ep1", "score": 5.0, "source_type": "episode"}]
        vec = [("ep1", 0.9)]
        result = vector_search.apply_type_weights(fts, vec)
        r = result[0]
        # Episode: vec_weight=0.8, fts_weight=0.2
        assert r["type_weights"]["vector_weight"] == 0.8
        assert r["type_weights"]["fts_weight"] == 0.2

    def test_lesson_weights(self):
        fts = [{"doc_id": "ls1", "score": 5.0, "source_type": "lesson"}]
        vec = [("ls1", 0.9)]
        result = vector_search.apply_type_weights(fts, vec)
        r = result[0]
        # Lesson: vec_weight=0.3, fts_weight=0.7
        assert r["type_weights"]["vector_weight"] == 0.3
        assert r["type_weights"]["fts_weight"] == 0.7

    def test_unknown_type_gets_defaults(self):
        fts = [{"doc_id": "u1", "score": 5.0, "source_type": "unknown"}]
        vec = [("u1", 0.9)]
        result = vector_search.apply_type_weights(fts, vec)
        r = result[0]
        assert r["type_weights"]["vector_weight"] == vector_search.DEFAULT_VECTOR_WEIGHT
        assert r["type_weights"]["fts_weight"] == vector_search.DEFAULT_FTS_WEIGHT

    def test_mixed_types_ordering(self):
        fts = [
            {"doc_id": "ep1", "score": 5.0, "source_type": "episode"},
            {"doc_id": "ls1", "score": 8.0, "source_type": "lesson"},
        ]
        vec = [("ep1", 0.9), ("ls1", 0.3)]
        result = vector_search.apply_type_weights(fts, vec)
        # Results should be sorted by hybrid score
        assert len(result) == 2
        assert result[0]["score"] >= result[1]["score"]

    def test_vec_only_doc_included(self):
        fts = [{"doc_id": "a", "score": 5.0, "source_type": "episode"}]
        vec = [("a", 0.9), ("b", 0.5)]
        result = vector_search.apply_type_weights(fts, vec)
        doc_ids = {r["doc_id"] for r in result}
        assert "b" in doc_ids
        b_result = [r for r in result if r["doc_id"] == "b"][0]
        assert b_result.get("_vec_only") is True

    def test_custom_type_weights(self):
        custom = {"episode": {"vector_weight": 0.5, "fts_weight": 0.5}}
        fts = [{"doc_id": "ep1", "score": 5.0, "source_type": "episode"}]
        vec = [("ep1", 0.9)]
        result = vector_search.apply_type_weights(fts, vec, type_weights=custom)
        assert result[0]["type_weights"]["vector_weight"] == 0.5


class TestApplyTemporalDecay:
    """apply_temporal_decay tests."""

    def _now(self):
        return datetime(2026, 3, 22, 12, 0, 0, tzinfo=timezone.utc)

    def test_empty_results(self):
        assert vector_search.apply_temporal_decay([], now=self._now()) == []

    def test_disabled(self):
        results = [{"doc_id": "a", "score": 1.0, "source_type": "episode",
                     "timestamp": "2025-01-01T00:00:00+00:00"}]
        out = vector_search.apply_temporal_decay(results, enabled=False, now=self._now())
        assert out[0]["score"] == 1.0
        assert "decay_factor" not in out[0]

    def test_episode_decayed(self):
        # 30 days ago -> factor ~0.5
        ts = (self._now() - timedelta(days=30)).isoformat()
        results = [{"doc_id": "a", "score": 1.0, "source_type": "episode", "timestamp": ts}]
        out = vector_search.apply_temporal_decay(results, now=self._now())
        assert out[0]["decay_factor"] == pytest.approx(0.5)
        assert out[0]["score"] == pytest.approx(0.5)

    def test_lesson_not_decayed(self):
        ts = (self._now() - timedelta(days=300)).isoformat()
        results = [{"doc_id": "ls1", "score": 1.0, "source_type": "lesson", "timestamp": ts}]
        out = vector_search.apply_temporal_decay(results, now=self._now())
        assert out[0]["score"] == 1.0
        assert "decay_factor" not in out[0]

    def test_resorting_after_decay(self):
        now = self._now()
        results = [
            {"doc_id": "old", "score": 1.0, "source_type": "episode",
             "timestamp": (now - timedelta(days=90)).isoformat()},
            {"doc_id": "new", "score": 0.5, "source_type": "episode",
             "timestamp": (now - timedelta(days=1)).isoformat()},
        ]
        out = vector_search.apply_temporal_decay(results, now=now)
        # "new" should rank higher because "old" gets heavy decay
        assert out[0]["doc_id"] == "new"

    def test_no_source_type_not_decayed(self):
        results = [{"doc_id": "x", "score": 1.0, "timestamp": "2025-01-01T00:00:00+00:00"}]
        out = vector_search.apply_temporal_decay(results, now=self._now())
        assert out[0]["score"] == 1.0

    def test_does_not_mutate_input(self):
        results = [{"doc_id": "a", "score": 1.0, "source_type": "episode",
                     "timestamp": "2026-02-20T12:00:00+00:00"}]
        original_score = results[0]["score"]
        vector_search.apply_temporal_decay(results, now=self._now())
        assert results[0]["score"] == original_score  # Input unchanged


# ============================================================
# P3: DB operations
# ============================================================

class TestCreateTables:
    """create_vector_tables / create_doc_map_table tests."""

    def test_create_vec_store_table(self):
        conn = sqlite3.connect(":memory:")
        vector_search.create_vector_tables(conn, dimensions=3, use_sqlite_vec=False)
        # Verify table exists by inserting
        conn.execute("INSERT INTO vec_store (doc_id, vector, vector_hash) VALUES ('a', x'00', 'h1')")
        conn.commit()
        row = conn.execute("SELECT doc_id FROM vec_store").fetchone()
        assert row[0] == "a"
        conn.close()

    def test_create_tables_idempotent(self):
        conn = sqlite3.connect(":memory:")
        vector_search.create_vector_tables(conn, dimensions=3, use_sqlite_vec=False)
        vector_search.create_vector_tables(conn, dimensions=3, use_sqlite_vec=False)
        # No error on second call
        conn.close()

    def test_create_doc_map_table(self):
        conn = sqlite3.connect(":memory:")
        vector_search.create_doc_map_table(conn)
        conn.execute(
            "INSERT INTO vec_doc_map (doc_id, vec_rowid, vector_hash) VALUES ('a', 1, 'h1')"
        )
        conn.commit()
        row = conn.execute("SELECT doc_id FROM vec_doc_map").fetchone()
        assert row[0] == "a"
        conn.close()

    def test_create_doc_map_idempotent(self):
        conn = sqlite3.connect(":memory:")
        vector_search.create_doc_map_table(conn)
        vector_search.create_doc_map_table(conn)
        conn.close()


class TestStoreVector:
    """store_vector tests (use_sqlite_vec=False)."""

    def test_store_and_retrieve(self, db):
        vec = [1.0, 2.0, 3.0]
        ok = vector_search.store_vector(db, "doc1", vec, "hash1", use_sqlite_vec=False)
        assert ok is True
        row = db.execute("SELECT vector, vector_hash FROM vec_store WHERE doc_id='doc1'").fetchone()
        assert row is not None
        assert row[1] == "hash1"
        retrieved = vector_search.blob_to_vector(row[0], 3)
        assert retrieved == pytest.approx(vec)

    def test_upsert_overwrites(self, db):
        vector_search.store_vector(db, "doc1", [1.0, 0.0, 0.0], "h1", use_sqlite_vec=False)
        vector_search.store_vector(db, "doc1", [0.0, 1.0, 0.0], "h2", use_sqlite_vec=False)
        row = db.execute("SELECT vector_hash FROM vec_store WHERE doc_id='doc1'").fetchone()
        assert row[0] == "h2"
        count = db.execute("SELECT COUNT(*) FROM vec_store WHERE doc_id='doc1'").fetchone()[0]
        assert count == 1


class TestGetVectorHash:
    """get_vector_hash tests."""

    def test_existing_doc(self, db):
        vector_search.store_vector(db, "doc1", [1.0, 2.0, 3.0], "abc123", use_sqlite_vec=False)
        h = vector_search.get_vector_hash(db, "doc1", use_sqlite_vec=False)
        assert h == "abc123"

    def test_missing_doc(self, db):
        h = vector_search.get_vector_hash(db, "nonexistent", use_sqlite_vec=False)
        assert h is None

    def test_no_table(self):
        conn = sqlite3.connect(":memory:")
        h = vector_search.get_vector_hash(conn, "x", use_sqlite_vec=False)
        assert h is None
        conn.close()


class TestGetVectorCount:
    """get_vector_count tests."""

    def test_empty(self, db):
        assert vector_search.get_vector_count(db, use_sqlite_vec=False) == 0

    def test_after_inserts(self, db):
        vector_search.store_vector(db, "a", [1.0, 0.0, 0.0], "h1", use_sqlite_vec=False)
        vector_search.store_vector(db, "b", [0.0, 1.0, 0.0], "h2", use_sqlite_vec=False)
        assert vector_search.get_vector_count(db, use_sqlite_vec=False) == 2

    def test_no_table(self):
        conn = sqlite3.connect(":memory:")
        assert vector_search.get_vector_count(conn, use_sqlite_vec=False) == 0
        conn.close()


class TestGetAllVectorHashes:
    """get_all_vector_hashes tests."""

    def test_empty(self, db):
        assert vector_search.get_all_vector_hashes(db, use_sqlite_vec=False) == {}

    def test_multiple(self, db):
        vector_search.store_vector(db, "a", [1.0, 0.0, 0.0], "h1", use_sqlite_vec=False)
        vector_search.store_vector(db, "b", [0.0, 1.0, 0.0], "h2", use_sqlite_vec=False)
        result = vector_search.get_all_vector_hashes(db, use_sqlite_vec=False)
        assert result == {"a": "h1", "b": "h2"}

    def test_no_table(self):
        conn = sqlite3.connect(":memory:")
        assert vector_search.get_all_vector_hashes(conn, use_sqlite_vec=False) == {}
        conn.close()


class TestVecSearchPython:
    """_vec_search_python brute-force cosine search tests."""

    def test_empty_store(self, db):
        result = vector_search._vec_search_python(db, [1.0, 0.0, 0.0], limit=5, dimensions=3)
        assert result == []

    def test_finds_similar(self, db):
        vector_search.store_vector(db, "close", [0.9, 0.1, 0.0], "h1", use_sqlite_vec=False)
        vector_search.store_vector(db, "far", [0.0, 0.0, 1.0], "h2", use_sqlite_vec=False)
        result = vector_search._vec_search_python(db, [1.0, 0.0, 0.0], limit=5, dimensions=3)
        assert len(result) >= 1
        # "close" should be first (more similar to [1,0,0])
        assert result[0][0] == "close"

    def test_limit_respected(self, db):
        for i in range(10):
            v = [0.0] * 3
            v[i % 3] = 1.0
            vector_search.store_vector(db, f"d{i}", v, f"h{i}", use_sqlite_vec=False)
        result = vector_search._vec_search_python(db, [1.0, 0.0, 0.0], limit=3, dimensions=3)
        assert len(result) <= 3

    def test_zero_query_vector(self, db):
        vector_search.store_vector(db, "a", [1.0, 2.0, 3.0], "h1", use_sqlite_vec=False)
        result = vector_search._vec_search_python(db, [0.0, 0.0, 0.0], limit=5, dimensions=3)
        assert result == []

    def test_similarity_scores_positive(self, db):
        vector_search.store_vector(db, "a", [1.0, 2.0, 3.0], "h1", use_sqlite_vec=False)
        result = vector_search._vec_search_python(db, [1.0, 2.0, 3.0], limit=5, dimensions=3)
        assert len(result) == 1
        assert result[0][1] > 0.0


class TestDropVectorData:
    """drop_vector_data tests."""

    def test_drop_vec_store(self, db):
        vector_search.store_vector(db, "a", [1.0, 0.0, 0.0], "h1", use_sqlite_vec=False)
        vector_search.drop_vector_data(db, use_sqlite_vec=False)
        # Table should be dropped
        with pytest.raises(sqlite3.OperationalError):
            db.execute("SELECT * FROM vec_store")

    def test_drop_nonexistent_table(self):
        conn = sqlite3.connect(":memory:")
        # Should not raise
        vector_search.drop_vector_data(conn, use_sqlite_vec=False)
        conn.close()


class TestVectorSearch:
    """vector_search (top-level) dispatching test."""

    def test_dispatches_to_python(self, db):
        vector_search.store_vector(db, "a", [1.0, 0.0, 0.0], "h1", use_sqlite_vec=False)
        result = vector_search.vector_search(db, [1.0, 0.0, 0.0], limit=5, dimensions=3, use_sqlite_vec=False)
        assert len(result) >= 1
        assert result[0][0] == "a"
