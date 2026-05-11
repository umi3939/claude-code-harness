#!/usr/bin/env python3
"""Vector search and hybrid merge for semantic search Phase 2.

Provides vector storage (sqlite-vec or BLOB fallback), KNN search,
cosine similarity computation, hybrid score merging with FTS5 results,
type-based weight adjustment (P3-9), and temporal decay (P3-2).

Does NOT import semantic_index.py or memory_mcp_server.py directly.
Receives a sqlite3 connection from the caller.
"""

import logging
import heapq
import math
import sqlite3
import struct
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# --- Configuration ---

# Hybrid search weight defaults
DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_FTS_WEIGHT = 0.3

# Maximum candidates for Python fallback cosine similarity
MAX_PYTHON_CANDIDATES = 5000

# --- sqlite-vec availability ---

_SQLITE_VEC_AVAILABLE: bool | None = None  # None = not yet checked


def _check_sqlite_vec() -> bool:
    """Check if sqlite-vec can be loaded. Cached after first call."""
    global _SQLITE_VEC_AVAILABLE
    if _SQLITE_VEC_AVAILABLE is not None:
        return _SQLITE_VEC_AVAILABLE

    try:
        import sqlite_vec
        test_db = sqlite3.connect(":memory:")
        test_db.enable_load_extension(True)
        sqlite_vec.load(test_db)
        test_db.close()
        _SQLITE_VEC_AVAILABLE = True
        logger.info("sqlite-vec is available")
    except Exception as e:
        _SQLITE_VEC_AVAILABLE = False
        logger.info("sqlite-vec not available, using Python fallback: %s", e)

    return _SQLITE_VEC_AVAILABLE


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load sqlite-vec extension into a connection. Returns True on success."""
    if not _check_sqlite_vec():
        return False
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return True
    except Exception as e:
        logger.warning("Failed to load sqlite-vec into connection: %s", e)
        return False


# --- Vector serialization ---


def vector_to_blob(vector: list[float]) -> bytes:
    """Serialize a float vector to a Float32Array blob for sqlite-vec."""
    return struct.pack("f" * len(vector), *vector)


def blob_to_vector(blob: bytes, dimensions: int) -> list[float]:
    """Deserialize a Float32Array blob to a float vector."""
    return list(struct.unpack("f" * dimensions, blob))


# --- Vector table management ---


def create_vector_tables(
    conn: sqlite3.Connection,
    dimensions: int,
    use_sqlite_vec: bool,
) -> None:
    """Create vector storage tables in the database.

    Args:
        conn: SQLite connection (same DB as FTS index).
        dimensions: Embedding vector dimensionality.
        use_sqlite_vec: Whether to use vec0 virtual table.
    """
    if use_sqlite_vec:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_index "
            f"USING vec0(embedding float[{dimensions}])"
        )
    else:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS vec_store (
                doc_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL,
                vector_hash TEXT NOT NULL
            )"""
        )
    conn.commit()


def drop_vector_data(conn: sqlite3.Connection, use_sqlite_vec: bool) -> None:
    """Drop all vector data and tables (for provider switch re-indexing).

    When using sqlite-vec, the vec0 virtual table must be dropped entirely
    (not just rows deleted) because its dimension is fixed at creation time.
    A dimension change requires table recreation.
    """
    try:
        if use_sqlite_vec:
            # Drop vec0 table entirely (dimension is fixed at creation)
            conn.execute("DROP TABLE IF EXISTS vec_index")
        else:
            conn.execute("DROP TABLE IF EXISTS vec_store")
        # Also clear the mapping table
        conn.execute("DELETE FROM vec_doc_map")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Table doesn't exist yet


def create_doc_map_table(conn: sqlite3.Connection) -> None:
    """Create the doc_id <-> rowid mapping table for sqlite-vec.

    sqlite-vec vec0 uses integer rowids, but our documents use text doc_ids.
    This table maps between them.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS vec_doc_map (
            doc_id TEXT PRIMARY KEY,
            vec_rowid INTEGER NOT NULL,
            vector_hash TEXT NOT NULL
        )"""
    )
    conn.commit()


# --- Vector storage operations ---


def store_vector(
    conn: sqlite3.Connection,
    doc_id: str,
    vector: list[float],
    text_hash: str,
    use_sqlite_vec: bool,
) -> bool:
    """Store a vector for a document.

    Args:
        conn: SQLite connection.
        doc_id: Document ID (e.g., "episode:ep001").
        vector: Embedding vector.
        text_hash: Hash of the source text (for change detection).
        use_sqlite_vec: Whether using vec0 virtual table.

    Returns:
        True if stored, False on error.
    """
    try:
        blob = vector_to_blob(vector)

        if use_sqlite_vec:
            # Check if already stored
            cur = conn.execute(
                "SELECT vec_rowid FROM vec_doc_map WHERE doc_id = ?",
                (doc_id,),
            )
            row = cur.fetchone()

            if row is not None:
                # Update existing
                old_rowid = row[0]
                conn.execute(
                    "DELETE FROM vec_index WHERE rowid = ?", (old_rowid,)
                )
                conn.execute(
                    "DELETE FROM vec_doc_map WHERE doc_id = ?", (doc_id,)
                )

            # Insert new vector and get rowid
            conn.execute(
                "INSERT INTO vec_index(embedding) VALUES (?)", (blob,)
            )
            new_rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Map doc_id -> rowid
            conn.execute(
                "INSERT INTO vec_doc_map (doc_id, vec_rowid, vector_hash) "
                "VALUES (?, ?, ?)",
                (doc_id, new_rowid, text_hash),
            )
        else:
            # BLOB store: upsert
            conn.execute(
                "INSERT OR REPLACE INTO vec_store (doc_id, vector, vector_hash) "
                "VALUES (?, ?, ?)",
                (doc_id, blob, text_hash),
            )

        conn.commit()
        return True
    except Exception as e:
        logger.error("Failed to store vector for %s: %s", doc_id, e)
        return False


def get_vector_hash(
    conn: sqlite3.Connection,
    doc_id: str,
    use_sqlite_vec: bool,
) -> str | None:
    """Get the stored vector hash for a document.

    Returns None if no vector exists for this document.
    """
    try:
        if use_sqlite_vec:
            cur = conn.execute(
                "SELECT vector_hash FROM vec_doc_map WHERE doc_id = ?",
                (doc_id,),
            )
        else:
            cur = conn.execute(
                "SELECT vector_hash FROM vec_store WHERE doc_id = ?",
                (doc_id,),
            )
        row = cur.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def get_all_vector_hashes(
    conn: sqlite3.Connection,
    use_sqlite_vec: bool,
) -> dict[str, str]:
    """Get all stored vector hashes as a dict of {doc_id: vector_hash}.

    Returns empty dict if no vectors exist or table doesn't exist.
    """
    try:
        if use_sqlite_vec:
            cur = conn.execute("SELECT doc_id, vector_hash FROM vec_doc_map")
        else:
            cur = conn.execute("SELECT doc_id, vector_hash FROM vec_store")
        return {row[0]: row[1] for row in cur.fetchall()}
    except sqlite3.OperationalError:
        return {}


def get_vector_count(
    conn: sqlite3.Connection,
    use_sqlite_vec: bool,
) -> int:
    """Get the number of stored vectors."""
    try:
        if use_sqlite_vec:
            cur = conn.execute("SELECT COUNT(*) FROM vec_doc_map")
        else:
            cur = conn.execute("SELECT COUNT(*) FROM vec_store")
        return cur.fetchone()[0]
    except sqlite3.OperationalError:
        return 0


# --- Vector search ---


def vector_search(
    conn: sqlite3.Connection,
    query_vector: list[float],
    limit: int,
    dimensions: int,
    use_sqlite_vec: bool,
) -> list[tuple[str, float]]:
    """Search for similar vectors.

    Args:
        conn: SQLite connection.
        query_vector: Query embedding vector.
        limit: Maximum results to return.
        dimensions: Vector dimensionality.
        use_sqlite_vec: Whether using vec0 virtual table.

    Returns:
        List of (doc_id, similarity_score) tuples, sorted by similarity
        descending. Similarity score is in [0, 1] range.
    """
    if use_sqlite_vec:
        return _vec_search_sqlite_vec(conn, query_vector, limit)
    else:
        return _vec_search_python(conn, query_vector, limit, dimensions)


def _vec_search_sqlite_vec(
    conn: sqlite3.Connection,
    query_vector: list[float],
    limit: int,
) -> list[tuple[str, float]]:
    """KNN search using sqlite-vec vec0 virtual table."""
    try:
        query_blob = vector_to_blob(query_vector)
        cur = conn.execute(
            "SELECT rowid, distance FROM vec_index "
            "WHERE embedding MATCH ? AND k = ?",
            (query_blob, limit),
        )
        rows = cur.fetchall()

        if not rows:
            return []

        # Map rowids back to doc_ids in batch using IN clause
        rowid_to_distance = {rowid: distance for rowid, distance in rows}
        placeholders = ",".join("?" * len(rows))
        rowid_list = list(rowid_to_distance.keys())
        cur2 = conn.execute(
            f"SELECT vec_rowid, doc_id FROM vec_doc_map WHERE vec_rowid IN ({placeholders})",
            rowid_list,
        )
        rowid_to_doc_id = {r[0]: r[1] for r in cur2.fetchall()}

        results = []
        for rowid, distance in rows:
            doc_id = rowid_to_doc_id.get(rowid)
            if doc_id is not None:
                # Convert distance to similarity: 0.0 (identical) -> 1.0 score
                # sqlite-vec distance is L2 distance for float vectors
                # Normalize: similarity = 1 / (1 + distance)
                similarity = 1.0 / (1.0 + distance)
                results.append((doc_id, similarity))

        return results
    except Exception as e:
        logger.error("sqlite-vec search error: %s", e)
        return []


def _vec_search_python(
    conn: sqlite3.Connection,
    query_vector: list[float],
    limit: int,
    dimensions: int,
) -> list[tuple[str, float]]:
    """Brute-force cosine similarity search (Python fallback)."""
    try:
        cur = conn.execute(
            "SELECT doc_id, vector FROM vec_store LIMIT ?",
            (MAX_PYTHON_CANDIDATES,),
        )
        rows = cur.fetchall()

        if not rows:
            return []

        results = []
        query_norm = _vector_norm(query_vector)
        if query_norm == 0.0:
            return []

        for doc_id, blob in rows:
            vec = blob_to_vector(blob, dimensions)
            similarity = _cosine_similarity(query_vector, vec, query_norm)
            if similarity > 0.0:
                results.append((doc_id, similarity))

        # Use heapq.nlargest for O(n log k) instead of O(n log n) full sort
        return heapq.nlargest(limit, results, key=lambda x: x[1])
    except Exception as e:
        logger.error("Python vector search error: %s", e)
        return []


def _vector_norm(vec: list[float]) -> float:
    """Compute L2 norm of a vector."""
    return math.sqrt(sum(x * x for x in vec))


def _cosine_similarity(
    a: list[float], b: list[float], a_norm: float | None = None
) -> float:
    """Compute cosine similarity between two vectors.

    Returns value in [-1, 1] range. Converts to [0, 1] for consistency.
    """
    if a_norm is None:
        a_norm = _vector_norm(a)
    b_norm = _vector_norm(b)
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    cosine = dot / (a_norm * b_norm)
    # Convert from [-1, 1] to [0, 1]
    return (cosine + 1.0) / 2.0


# --- Hybrid merge ---


def hybrid_merge(
    fts_results: list[dict],
    vec_results: list[tuple[str, float]],
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    fts_weight: float = DEFAULT_FTS_WEIGHT,
) -> list[dict]:
    """Merge FTS5 and vector search results with weighted score combination.

    Args:
        fts_results: FTS5 search results (list of dicts with 'doc_id', 'score').
        vec_results: Vector search results (list of (doc_id, similarity) tuples).
        vector_weight: Weight for vector scores.
        fts_weight: Weight for FTS scores.

    Returns:
        Merged results sorted by hybrid score descending. Each result dict
        has an updated 'score' field with the hybrid score.
    """
    if not fts_results and not vec_results:
        return []

    # If only FTS results (no vector search)
    if not vec_results:
        return fts_results

    # If only vector results (no FTS matches)
    if not fts_results:
        # Return vector-only results as partial dicts with doc_id and score.
        # Caller (hybrid_search) is responsible for enriching with metadata.
        vec_raw = {doc_id: score for doc_id, score in vec_results}
        vec_normalized = _min_max_normalize(vec_raw)
        return [
            {
                "doc_id": doc_id,
                "score": score,
                "_vec_only": True,
                "fts_raw_score": None,
                "vec_raw_score": vec_raw[doc_id],
                "fts_weight": fts_weight,
                "vec_weight": vector_weight,
            }
            for doc_id, score in sorted(
                vec_normalized.items(), key=lambda x: x[1], reverse=True
            )
        ]

    # Preserve raw scores before normalization
    fts_raw_scores = {r["doc_id"]: r["score"] for r in fts_results}
    vec_raw_scores = {doc_id: score for doc_id, score in vec_results}

    # Normalize FTS scores (min-max within this result set)
    fts_normalized = _min_max_normalize(fts_raw_scores)

    # Normalize vector scores
    vec_normalized = _min_max_normalize(vec_raw_scores)

    # Compute hybrid scores
    all_doc_ids = set(fts_normalized.keys()) | set(vec_normalized.keys())
    hybrid_scores: dict[str, float] = {}

    for doc_id in all_doc_ids:
        fts_s = fts_normalized.get(doc_id)
        vec_s = vec_normalized.get(doc_id)

        if fts_s is not None and vec_s is not None:
            # Both scores available: weighted combination
            hybrid_scores[doc_id] = fts_s * fts_weight + vec_s * vector_weight
        elif fts_s is not None:
            # FTS only
            hybrid_scores[doc_id] = fts_s * fts_weight
        else:
            # Vector only (will be handled below if metadata exists)
            hybrid_scores[doc_id] = vec_s * vector_weight

    # Build result list from FTS results (which have full metadata).
    # Vec-only documents (present in vec_results but not fts_results) are
    # intentionally excluded here.  They lack the metadata fields that FTS
    # results carry.  Instead, hybrid_search() in semantic_index.py detects
    # vec-only entries via the "_vec_only" marker (set in the vec-only early
    # return path above) and enriches them with metadata in a separate step.
    result_by_id = {r["doc_id"]: dict(r) for r in fts_results}

    # Update scores and add breakdown fields
    for doc_id, result in result_by_id.items():
        if doc_id in hybrid_scores:
            result["score"] = hybrid_scores[doc_id]
        result["fts_raw_score"] = fts_raw_scores.get(doc_id)
        result["vec_raw_score"] = vec_raw_scores.get(doc_id)
        result["fts_weight"] = fts_weight
        result["vec_weight"] = vector_weight

    # Sort by hybrid score descending
    merged = sorted(result_by_id.values(), key=lambda r: r["score"], reverse=True)
    return merged


def _min_max_normalize(scores: dict[str, float]) -> dict[str, float]:
    """Normalize scores to [0, 1] range using min-max normalization.

    If all scores are identical, all normalized scores are 1.0.
    """
    if not scores:
        return {}

    values = list(scores.values())
    min_val = min(values)
    max_val = max(values)

    if max_val == min_val:
        return {k: 1.0 for k in scores}

    range_val = max_val - min_val
    return {k: (v - min_val) / range_val for k, v in scores.items()}


# --- P3-9: Type-based weight adjustment ---

# Default type-specific weight distributions
DEFAULT_TYPE_WEIGHTS = {
    "episode": {"vector_weight": 0.8, "fts_weight": 0.2},
    "lesson": {"vector_weight": 0.3, "fts_weight": 0.7},
}


def apply_type_weights(
    fts_results: list[dict],
    vec_results: list[tuple[str, float]],
    type_weights: dict[str, dict[str, float]] | None = None,
) -> list[dict]:
    """Apply source_type-specific vec/fts weight distributions.

    Performs unified min-max normalization across ALL types first,
    then computes per-document scores using type-specific weights.
    This avoids normalization bias from per-type splitting (MED #1).

    Args:
        fts_results: FTS5 search results (list of dicts with 'doc_id', 'score', 'source_type').
        vec_results: Vector search results (list of (doc_id, similarity) tuples).
        type_weights: Per-source_type weight config. Keys are source_type strings,
            values are dicts with 'vector_weight' and 'fts_weight'.

    Returns:
        Merged results sorted by hybrid score descending, with type_weights
        and raw score fields.
    """
    if not fts_results and not vec_results:
        return []

    if type_weights is None:
        type_weights = DEFAULT_TYPE_WEIGHTS

    # Build source_type lookup from FTS results.
    # Vec-only documents (present in vec_results but absent from fts_results)
    # will not appear in this map, so source_type_map.get() returns "".
    # This is intentional: at this stage, vec-only docs lack metadata
    # (including source_type).  They receive default weights here, and
    # hybrid_search() in semantic_index.py enriches them with full metadata
    # (including source_type) in a separate step afterward.
    source_type_map = {r["doc_id"]: r.get("source_type", "") for r in fts_results}

    # Preserve raw scores
    fts_raw_scores = {r["doc_id"]: r["score"] for r in fts_results}
    vec_raw_scores = {doc_id: score for doc_id, score in vec_results}

    # Unified min-max normalization across ALL documents
    fts_normalized = _min_max_normalize(fts_raw_scores)
    vec_normalized = _min_max_normalize(vec_raw_scores)

    # Compute hybrid scores using type-specific weights
    all_doc_ids = set(fts_normalized.keys()) | set(vec_normalized.keys())
    hybrid_scores: dict[str, float] = {}
    applied_weights: dict[str, dict[str, float]] = {}

    for doc_id in all_doc_ids:
        source_type = source_type_map.get(doc_id, "")
        weights = type_weights.get(
            source_type,
            {"vector_weight": DEFAULT_VECTOR_WEIGHT, "fts_weight": DEFAULT_FTS_WEIGHT},
        )
        vw = weights["vector_weight"]
        fw = weights["fts_weight"]
        applied_weights[doc_id] = weights

        fts_s = fts_normalized.get(doc_id)
        vec_s = vec_normalized.get(doc_id)

        if fts_s is not None and vec_s is not None:
            hybrid_scores[doc_id] = fts_s * fw + vec_s * vw
        elif fts_s is not None:
            hybrid_scores[doc_id] = fts_s * fw
        else:
            hybrid_scores[doc_id] = vec_s * vw

    # Build result list from FTS results (which have full metadata)
    result_by_id = {r["doc_id"]: dict(r) for r in fts_results}

    # Add vec-only documents as partial results
    for doc_id in all_doc_ids:
        if doc_id not in result_by_id:
            result_by_id[doc_id] = {
                "doc_id": doc_id,
                "_vec_only": True,
            }

    # Update scores and add breakdown/type_weights fields
    for doc_id, result in result_by_id.items():
        if doc_id in hybrid_scores:
            result["score"] = hybrid_scores[doc_id]
        result["fts_raw_score"] = fts_raw_scores.get(doc_id)
        result["vec_raw_score"] = vec_raw_scores.get(doc_id)
        result["fts_weight"] = applied_weights.get(doc_id, {}).get("fts_weight", DEFAULT_FTS_WEIGHT)
        result["vec_weight"] = applied_weights.get(doc_id, {}).get("vector_weight", DEFAULT_VECTOR_WEIGHT)
        result["type_weights"] = applied_weights.get(
            doc_id,
            {"vector_weight": DEFAULT_VECTOR_WEIGHT, "fts_weight": DEFAULT_FTS_WEIGHT},
        )

    # Sort by hybrid score descending
    merged = sorted(result_by_id.values(), key=lambda r: r.get("score", 0), reverse=True)
    return merged


# --- P3-2: Temporal decay ---

# Default decay parameters
DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_DECAY_FLOOR = 0.2


def apply_temporal_decay(
    results: list[dict],
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    floor: float = DEFAULT_DECAY_FLOOR,
    enabled: bool = True,
    now: datetime | None = None,
) -> list[dict]:
    """Apply time-based decay to episode scores.

    Exponential decay with configurable half-life and floor.
    Only applies to episodes (source_type == "episode").
    Lessons are never decayed.

    Args:
        results: Search results (list of dicts with 'score', 'source_type', 'timestamp').
        half_life_days: Days until score is halved.
        floor: Minimum decay factor (0 < floor <= 1).
        enabled: If False, skip decay entirely.
        now: Current time (for testing). Defaults to UTC now.

    Returns:
        Results with adjusted scores and decay_factor field (episodes only),
        re-sorted by score descending.
    """
    if not results or not enabled:
        return results

    if now is None:
        now = datetime.now(timezone.utc)

    out = []
    for r in results:
        r = dict(r)  # shallow copy
        if r.get("source_type") == "episode":
            factor = _compute_decay_factor(
                r.get("timestamp"), now, half_life_days, floor
            )
            r["decay_factor"] = factor
            r["score"] = r["score"] * factor
        out.append(r)

    # Re-sort by score descending
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


def _compute_decay_factor(
    timestamp_str: str | None,
    now: datetime,
    half_life_days: float,
    floor: float,
) -> float:
    """Compute exponential decay factor for a timestamp.

    Returns 1.0 if timestamp is missing/invalid.
    """
    if not timestamp_str:
        return 1.0

    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return 1.0

    elapsed_days = (now - ts).total_seconds() / 86400.0
    if elapsed_days <= 0:
        return 1.0

    # Exponential decay: factor = 2^(-elapsed/half_life)
    raw_factor = math.pow(2.0, -elapsed_days / half_life_days)
    return max(raw_factor, floor)
