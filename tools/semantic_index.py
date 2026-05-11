#!/usr/bin/env python3
"""FTS5 full-text search index for episode and lesson memory.

Provides Japanese-aware tokenization and BM25-scored search over episode
summaries/user_utterances and lesson fields. Phase 2 adds vector embedding
support for hybrid search (FTS5 + vector similarity).

Operates as an independent module imported only by memory_mcp_server.py.
Does NOT import episode_memory.py, episode_recall.py, or lessons_registry.py.
Data is passed in from the caller.
"""

import hashlib
import threading
import json
import logging
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Phase 2 imports (graceful degradation) ---

try:
    from embedding_provider import EmbeddingProvider, auto_select_provider
    from vector_search import (
        create_vector_tables,
        create_doc_map_table,
        drop_vector_data,
        store_vector,
        get_vector_hash,
        get_all_vector_hashes,
        get_vector_count,
        vector_search,
        hybrid_merge,
        apply_type_weights,
        apply_temporal_decay,
        _check_sqlite_vec,
        _load_sqlite_vec,
        DEFAULT_VECTOR_WEIGHT,
        DEFAULT_FTS_WEIGHT,
        DEFAULT_TYPE_WEIGHTS,
    )
    _PHASE2_AVAILABLE = True
except ImportError:
    _PHASE2_AVAILABLE = False

# --- Constants ---

DB_FILENAME = "semantic_index.db"
DIRTY_FLAG_FILENAME = ".semantic_dirty"
SCHEMA_VERSION = 2  # Phase 2: vector tables added

# Japanese stop words (high-frequency particles) — query-side only
STOP_WORDS = frozenset(
    "の は が を に で と も た て だ な い し れ".split()
)

# Relative time pattern: e.g., "7d", "24h", "2w"
_RELATIVE_TIME_PATTERN = re.compile(r"^(\d+)([hdw])$", re.IGNORECASE)


# --- Japanese Tokenizer ---


def _is_cjk_ideograph(ch: str) -> bool:
    """Check if a character is a CJK Unified Ideograph (kanji)."""
    cp = ord(ch)
    return (
        (0x4E00 <= cp <= 0x9FFF)
        or (0x3400 <= cp <= 0x4DBF)
        or (0x20000 <= cp <= 0x2A6DF)
        or (0xF900 <= cp <= 0xFAFF)
    )


def _is_katakana(ch: str) -> bool:
    """Check if a character is Katakana."""
    cp = ord(ch)
    return 0x30A0 <= cp <= 0x30FF


def _is_hiragana(ch: str) -> bool:
    """Check if a character is Hiragana."""
    cp = ord(ch)
    return 0x3040 <= cp <= 0x309F


def _script_type(ch: str) -> str:
    """Classify a character into script type for boundary detection."""
    if _is_cjk_ideograph(ch):
        return "kanji"
    if _is_katakana(ch):
        return "katakana"
    if _is_hiragana(ch):
        return "hiragana"
    if ch.isascii() and ch.isalnum():
        return "ascii"
    return "other"


def tokenize_japanese(text: str, remove_stop_words: bool = False) -> list[str]:
    """Tokenize text with Japanese script-boundary splitting.

    - Kanji sequences: bigram (overlapping 2-char windows)
    - Katakana sequences: kept as single token
    - Hiragana sequences: kept as single token
    - ASCII alphanumeric: split on whitespace
    - Script boundaries: split tokens at boundary

    Args:
        text: Input text to tokenize.
        remove_stop_words: If True, remove Japanese stop words from result.

    Returns:
        List of token strings.
    """
    if not text:
        return []

    tokens = []
    current_script = None
    current_chars: list[str] = []

    def flush():
        nonlocal current_chars, current_script
        if not current_chars:
            return
        segment = "".join(current_chars)
        if current_script == "kanji":
            # Bigram
            if len(segment) == 1:
                tokens.append(segment)
            else:
                for i in range(len(segment) - 1):
                    tokens.append(segment[i : i + 2])
        elif current_script == "ascii":
            # Split on whitespace
            for word in segment.split():
                if word:
                    tokens.append(word.lower())
        elif current_script in ("katakana", "hiragana"):
            tokens.append(segment)
        # "other" is discarded (punctuation, spaces between non-ascii, etc.)
        current_chars = []
        current_script = None

    for ch in text:
        st = _script_type(ch)
        if st == "other":
            # Flush current, skip this char
            flush()
            continue
        if st != current_script:
            flush()
            current_script = st
        current_chars.append(ch)

    flush()

    if remove_stop_words:
        tokens = [t for t in tokens if t not in STOP_WORDS]

    return tokens


def tokenize_for_index(text: str) -> str:
    """Tokenize text for FTS5 indexing (no stop word removal).

    Returns space-joined token string suitable for FTS5 insertion.
    """
    tokens = tokenize_japanese(text, remove_stop_words=False)
    return " ".join(tokens)


def tokenize_for_query(text: str) -> str | None:
    """Tokenize text for FTS5 query (with stop word removal).

    Returns an FTS5 MATCH expression with OR-joined quoted tokens,
    or None if no tokens remain after stop word removal.
    BM25 scoring ensures documents matching more tokens rank higher.
    """
    tokens = tokenize_japanese(text, remove_stop_words=True)
    if not tokens:
        return None
    # Strip double quotes from tokens to prevent FTS5 syntax errors
    sanitized = [t.replace('"', '') for t in tokens]
    sanitized = [t for t in sanitized if t]  # Remove empty after stripping
    if not sanitized:
        return None
    # Quote each token and OR-join for FTS5
    # OR allows partial matches; BM25 naturally ranks documents with
    # more matching tokens higher, so relevance ordering is preserved.
    quoted = [f'"{t}"' for t in sanitized]
    return " OR ".join(quoted)


# --- Text extraction helpers ---


def _episode_to_text(episode: dict) -> str:
    """Extract searchable text from an episode dict.

    Combines summary and user_utterances texts.
    """
    parts = []
    summary = episode.get("summary", "")
    if summary:
        parts.append(summary)
    for utt in episode.get("user_utterances", []):
        text = utt.get("text", "")
        if text:
            parts.append(text)
    return " ".join(parts)


def _lesson_to_text(lesson: dict) -> str:
    """Extract searchable text from a lesson dict.

    Combines action, why, fix, lesson, rule fields.
    """
    fields = ["action", "why", "fix", "lesson", "rule"]
    parts = [lesson.get(f, "") for f in fields if lesson.get(f)]
    return " ".join(parts)


def _text_hash(text: str) -> str:
    """Compute SHA-256 hash of text for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --- Embed timeout guard ---


def _embed_with_timeout(provider, text: str, timeout: float = 15.0):
    """Run embed_single with a timeout guard. Returns None if slow.

    Bug fix (4th hang reoccurrence): default loosened to 60s in commit 0cb1ced
    caused memory_search to hang for 374-1579s when the embedding API was slow,
    triggering MCP server disconnect loops ("stdio transport error"). Tightening
    back to 15s preserves enough headroom for normal API latency while bounding
    the worst case to FTS-only fallback. See MEMORY.md entries #121/#122/#128
    and the watchdog wrapper in memory_mcp_server._run_with_watchdog for the
    outer 90s upper bound on memory_search end-to-end.
    """
    result = [None]

    def _run():
        try:
            result[0] = provider.embed_single(text)
        except Exception as e:
            logger.warning("embed_single failed in background thread: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        logger.warning(
            "embed_single timed out after %.1fs, falling back to FTS-only", timeout
        )
        return None
    return result[0]


# --- Database management ---


class SemanticIndex:
    """FTS5 full-text search index manager.

    Manages a SQLite database with FTS5 virtual table for episode and
    lesson text search with BM25 scoring.
    """

    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir
        self.db_path = os.path.join(memory_dir, DB_FILENAME)
        self.dirty_flag_path = os.path.join(memory_dir, DIRTY_FLAG_FILENAME)
        self._conn: sqlite3.Connection | None = None
        # Phase 2 state (lazy initialized)
        self._provider: "EmbeddingProvider | None" = None
        self._provider_initialized: bool = False
        self._use_sqlite_vec: bool | None = None  # None = not yet determined
        self._vector_enabled: bool = False

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is not None:
            return self._conn
        # Ensure directory exists
        os.makedirs(self.memory_dir, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        """Create tables if they don't exist. Handles Phase 1 -> Phase 2 migration."""
        conn = self._conn
        if conn is None:
            return

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                tokenized_text TEXT NOT NULL,
                original_text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                timestamp TEXT,
                session_id TEXT,
                episode_type TEXT,
                tags TEXT
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(
                tokenized_text,
                doc_id UNINDEXED
            );
        """
        )

        # Set schema version if not exists
        cur = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_sync', '')"
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('episode_count', '0')"
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('lesson_count', '0')"
            )
            conn.commit()

        # Phase 2 migration: add vector tables if Phase 2 modules are available
        if _PHASE2_AVAILABLE:
            self._migrate_to_phase2(conn)

    def _migrate_to_phase2(self, conn: sqlite3.Connection) -> None:
        """Migrate schema from Phase 1 to Phase 2 (add vector tables)."""
        try:
            cur = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            )
            row = cur.fetchone()
            current_version = int(row[0]) if row else 1

            if current_version >= 2:
                # Already migrated, restore state from meta
                self._restore_phase2_state(conn)
                return

            # Determine sqlite-vec availability (fixed at first table creation)
            use_vec = _check_sqlite_vec()
            if use_vec:
                use_vec = _load_sqlite_vec(conn)
            self._use_sqlite_vec = use_vec

            # Create vector tables
            # We need a dimension to create vec0 table, but we don't know
            # the provider yet. We'll create tables lazily on first embed.
            # For now, just create the doc_map and meta entries.
            create_doc_map_table(conn)

            # Add Phase 2 meta entries
            _meta_defaults = {
                "embedding_provider": "",
                "embedding_model": "",
                "embedding_dimensions": "0",
                "vector_enabled": "false",
                "use_sqlite_vec": str(use_vec).lower(),
            }
            for key, value in _meta_defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                    (key, value),
                )

            # Update schema version
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '2')"
            )
            conn.commit()
            logger.info("Migrated semantic index schema to Phase 2")

        except Exception as e:
            logger.warning("Phase 2 migration failed (FTS-only mode): %s", e)

    def _restore_phase2_state(self, conn: sqlite3.Connection) -> None:
        """Restore Phase 2 state from meta table."""
        try:
            cur = conn.execute(
                "SELECT value FROM meta WHERE key = 'use_sqlite_vec'"
            )
            row = cur.fetchone()
            self._use_sqlite_vec = row[0] == "true" if row else False

            # If using sqlite-vec, load the extension
            if self._use_sqlite_vec:
                if not _load_sqlite_vec(conn):
                    self._use_sqlite_vec = False

            cur = conn.execute(
                "SELECT value FROM meta WHERE key = 'vector_enabled'"
            )
            row = cur.fetchone()
            self._vector_enabled = row[0] == "true" if row else False

        except Exception as e:
            logger.warning("Failed to restore Phase 2 state: %s", e)

    def _get_provider(self) -> "EmbeddingProvider | None":
        """Lazy-initialize and return the embedding provider.

        Returns None if no provider is available (graceful degradation).
        """
        if not _PHASE2_AVAILABLE:
            return None

        if self._provider_initialized:
            return self._provider

        self._provider_initialized = True
        self._provider = auto_select_provider()
        return self._provider

    def _ensure_vector_tables(self, conn: sqlite3.Connection, dimensions: int) -> None:
        """Ensure vector tables exist with correct dimensions.

        Called lazily on first embedding generation.
        """
        if self._use_sqlite_vec is None:
            return

        try:
            create_vector_tables(conn, dimensions, self._use_sqlite_vec)
        except Exception as e:
            logger.warning("Failed to create vector tables: %s", e)

    def _check_provider_switch(self, conn: sqlite3.Connection) -> bool:
        """Check if the embedding provider has changed.

        Returns True if provider switched (vectors need re-indexing).
        Distinguishes between provider absence and provider change per design.
        """
        provider = self._get_provider()

        # Get stored provider info
        cur = conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_provider'"
        )
        row = cur.fetchone()
        stored_provider = row[0] if row else ""

        cur = conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_model'"
        )
        row = cur.fetchone()
        stored_model = row[0] if row else ""

        if provider is None:
            # Provider absent: do NOT invalidate existing vectors.
            # Just disable vector search. Existing data preserved.
            if self._vector_enabled:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) "
                    "VALUES ('vector_enabled', 'false')"
                )
                conn.commit()
                self._vector_enabled = False
            return False

        current_name = provider.provider_name
        current_model = provider.model_id

        if not stored_provider:
            # First time: no previous provider, no switch
            return False

        if current_name == stored_provider and current_model == stored_model:
            # Same provider, no switch
            return False

        # Provider has changed: invalidate all vectors
        logger.info(
            "Provider switch detected: %s/%s -> %s/%s. Invalidating vectors.",
            stored_provider, stored_model, current_name, current_model,
        )
        drop_vector_data(conn, self._use_sqlite_vec)

        # Update meta
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_provider', ?)",
            (current_name,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_model', ?)",
            (current_model,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_dimensions', ?)",
            (str(provider.dimensions),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('vector_enabled', 'true')"
        )
        conn.commit()
        self._vector_enabled = True

        # Set dirty flag to trigger full re-embedding
        self.set_dirty()
        return True

    def sync_vectors(self, conn: sqlite3.Connection | None = None) -> int:
        """Generate embeddings for documents that don't have them.

        This is called separately from FTS sync to avoid adding API latency
        to search operations. Returns count of vectors generated.
        """
        if not _PHASE2_AVAILABLE:
            return 0

        provider = self._get_provider()
        if provider is None:
            return 0

        if conn is None:
            conn = self._get_conn()

        # Check for provider switch
        self._check_provider_switch(conn)

        # Ensure vector tables exist
        self._ensure_vector_tables(conn, provider.dimensions)

        # Update meta with current provider info (first time or after switch)
        cur = conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_provider'"
        )
        row = cur.fetchone()
        if not row or not row[0]:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_provider', ?)",
                (provider.provider_name,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_model', ?)",
                (provider.model_id,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('embedding_dimensions', ?)",
                (str(provider.dimensions),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('vector_enabled', 'true')"
            )
            conn.commit()
            self._vector_enabled = True

        # Find documents that need embedding (batch lookup to avoid N+1)
        cur = conn.execute(
            "SELECT doc_id, original_text, text_hash FROM documents"
        )
        all_docs = cur.fetchall()

        existing_hashes = get_all_vector_hashes(conn, self._use_sqlite_vec)

        docs_to_embed = []
        for doc_id, original_text, text_hash in all_docs:
            if existing_hashes.get(doc_id) != text_hash:
                docs_to_embed.append((doc_id, original_text, text_hash))

        if not docs_to_embed:
            return 0

        # Batch embed
        texts = [text for _, text, _ in docs_to_embed]
        vectors = provider.embed_texts(texts)

        stored = 0
        for i, (doc_id, _, text_hash) in enumerate(docs_to_embed):
            vec = vectors[i] if i < len(vectors) else None
            if vec is not None:
                if store_vector(conn, doc_id, vec, text_hash, self._use_sqlite_vec):
                    stored += 1
                else:
                    logger.warning("Failed to store vector for %s", doc_id)
            else:
                logger.warning("Failed to generate embedding for %s", doc_id)

        logger.info("Generated %d/%d vectors", stored, len(docs_to_embed))
        return stored

    def hybrid_search(
        self,
        query: str,
        limit: int = 20,
        tags: list[str] | None = None,
        last: str | None = None,
        vector_weight: float | None = None,
        fts_weight: float | None = None,
        temporal_decay: bool = True,
    ) -> list[dict]:
        """Hybrid search combining FTS5 and vector similarity.

        Falls back to FTS-only if vector search is unavailable.
        When vector search is available and explicit weights are not provided,
        applies type-based weight adjustment (P3-9) and temporal decay (P3-2).

        Args:
            query: Natural language search query.
            limit: Maximum results to return.
            tags: Optional tag filter.
            last: Optional time range filter.
            vector_weight: Override default vector weight (0.7).
                When specified, type-based weights are disabled.
            fts_weight: Override default FTS weight (0.3).
                When specified, type-based weights are disabled.
            temporal_decay: Whether to apply temporal decay to episodes.
                Defaults to True.

        Returns:
            List of result dicts (same format as search()).
        """
        # Always run FTS search first
        fts_results = self.search(query, limit=limit, tags=tags, last=last)

        # Try vector search if available
        if not _PHASE2_AVAILABLE or not self._vector_enabled:
            # FTS-only path: still apply temporal decay if enabled
            if temporal_decay:
                fts_results = apply_temporal_decay(fts_results, enabled=temporal_decay)
            return fts_results

        provider = self._get_provider()
        if provider is None:
            if temporal_decay:
                fts_results = apply_temporal_decay(fts_results, enabled=temporal_decay)
            return fts_results

        conn = self._get_conn()

        # Check vector count
        vec_count = get_vector_count(conn, self._use_sqlite_vec)
        if vec_count == 0:
            if temporal_decay:
                fts_results = apply_temporal_decay(fts_results, enabled=temporal_decay)
            return fts_results

        # Generate query embedding with timeout guard
        # Bug fix: embed_single can block 90s+ when API is unreachable
        # (30s timeout x 3 retries). Guard with 5s overall timeout.
        query_vec = _embed_with_timeout(provider, query, timeout=60.0)
        if query_vec is None:
            if temporal_decay:
                fts_results = apply_temporal_decay(fts_results, enabled=temporal_decay)
            return fts_results

        # Get stored dimensions
        cur = conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_dimensions'"
        )
        row = cur.fetchone()
        dimensions = int(row[0]) if row else provider.dimensions

        # Vector search
        vec_results = vector_search(
            conn, query_vec, limit, dimensions, self._use_sqlite_vec
        )

        if not vec_results:
            if temporal_decay:
                fts_results = apply_temporal_decay(fts_results, enabled=temporal_decay)
            return fts_results

        # Determine merge strategy:
        # If explicit weights are provided, use standard hybrid_merge (backward compat)
        # Otherwise, use type-based weight adjustment (P3-9)
        use_type_weights = vector_weight is None and fts_weight is None

        if use_type_weights:
            merged = apply_type_weights(fts_results, vec_results)
        else:
            vw = vector_weight if vector_weight is not None else DEFAULT_VECTOR_WEIGHT
            fw = fts_weight if fts_weight is not None else DEFAULT_FTS_WEIGHT
            merged = hybrid_merge(fts_results, vec_results, vw, fw)

        if not merged:
            if temporal_decay:
                fts_results = apply_temporal_decay(fts_results, enabled=temporal_decay)
            return fts_results

        # Enrich vector-only results (marked with _vec_only) with metadata
        vec_only_ids = [r["doc_id"] for r in merged if r.get("_vec_only")]
        if vec_only_ids:
            placeholders = ",".join("?" * len(vec_only_ids))
            cur2 = conn.execute(
                f"""SELECT doc_id, source_type, source_id, original_text,
                           timestamp, session_id, episode_type, tags
                    FROM documents WHERE doc_id IN ({placeholders})""",
                vec_only_ids,
            )
            meta_map = {}
            for row in cur2.fetchall():
                meta_map[row[0]] = {
                    "source_type": row[1],
                    "source_id": row[2],
                    "original_text": row[3],
                    "timestamp": row[4],
                    "session_id": row[5],
                    "episode_type": row[6],
                    "tags": json.loads(row[7]) if row[7] else [],
                }

            enriched = []
            for r in merged:
                if r.get("_vec_only"):
                    meta = meta_map.get(r["doc_id"])
                    if meta is None:
                        logger.warning(
                            "hybrid_search: vec-only doc_id %s not found in "
                            "documents table (orphan vector), skipping",
                            r["doc_id"],
                        )
                        continue  # doc_id not found in documents table
                    r.update(meta)
                    del r["_vec_only"]
                enriched.append(r)
            merged = enriched

        # Apply temporal decay (P3-2) — after merge, before return
        merged = apply_temporal_decay(merged, enabled=temporal_decay)

        return merged

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- Dirty flag ---

    def set_dirty(self) -> None:
        """Set the dirty flag (file-based)."""
        try:
            Path(self.dirty_flag_path).touch()
        except OSError:
            pass

    def is_dirty(self) -> bool:
        """Check if the dirty flag is set."""
        return os.path.exists(self.dirty_flag_path)

    def clear_dirty(self) -> None:
        """Clear the dirty flag."""
        try:
            os.remove(self.dirty_flag_path)
        except OSError:
            pass

    # --- Index operations ---

    def add_episode(self, episode: dict) -> bool:
        """Add a single episode to the index.

        Returns True if added, False if already indexed (same hash).
        """
        episode_id = episode.get("episode_id", "")
        if not episode_id:
            return False

        doc_id = f"episode:{episode_id}"
        original_text = _episode_to_text(episode)
        if not original_text.strip():
            return False

        text_hash = _text_hash(original_text)

        conn = self._get_conn()

        # Check if already indexed with same hash
        cur = conn.execute(
            "SELECT text_hash FROM documents WHERE doc_id = ?", (doc_id,)
        )
        row = cur.fetchone()
        if row is not None and row[0] == text_hash:
            return False  # Already indexed, no change

        tokenized = tokenize_for_index(original_text)

        # Collect metadata
        tags_json = json.dumps(episode.get("tags", []))
        timestamp = episode.get("timestamp", "")
        session_id = episode.get("session_id", "")
        episode_type = episode.get("episode_type", "")

        if row is not None:
            # Update existing
            conn.execute(
                """UPDATE documents SET tokenized_text=?, original_text=?,
                   text_hash=?, timestamp=?, session_id=?, episode_type=?, tags=?
                   WHERE doc_id=?""",
                (
                    tokenized,
                    original_text,
                    text_hash,
                    timestamp,
                    session_id,
                    episode_type,
                    tags_json,
                    doc_id,
                ),
            )
            # Update FTS
            conn.execute(
                "DELETE FROM fts_index WHERE doc_id = ?", (doc_id,)
            )
        else:
            # Insert new
            conn.execute(
                """INSERT INTO documents
                   (doc_id, source_type, source_id, tokenized_text, original_text,
                    text_hash, timestamp, session_id, episode_type, tags)
                   VALUES (?, 'episode', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id,
                    episode_id,
                    tokenized,
                    original_text,
                    text_hash,
                    timestamp,
                    session_id,
                    episode_type,
                    tags_json,
                ),
            )

        conn.execute(
            "INSERT INTO fts_index (tokenized_text, doc_id) VALUES (?, ?)",
            (tokenized, doc_id),
        )
        conn.commit()

        # Update count
        self._update_count("episode_count")
        return True

    def add_lesson(self, lesson: dict, lesson_number: int) -> bool:
        """Add a single lesson to the index.

        Returns True if added, False if already indexed (same hash).
        """
        doc_id = f"lesson:{lesson_number}"
        original_text = _lesson_to_text(lesson)
        if not original_text.strip():
            return False

        text_hash = _text_hash(original_text)

        conn = self._get_conn()

        # Check if already indexed with same hash
        cur = conn.execute(
            "SELECT text_hash FROM documents WHERE doc_id = ?", (doc_id,)
        )
        row = cur.fetchone()
        if row is not None and row[0] == text_hash:
            return False

        tokenized = tokenize_for_index(original_text)
        date_str = lesson.get("date", "")

        if row is not None:
            conn.execute(
                """UPDATE documents SET tokenized_text=?, original_text=?,
                   text_hash=?, timestamp=?
                   WHERE doc_id=?""",
                (tokenized, original_text, text_hash, date_str, doc_id),
            )
            conn.execute(
                "DELETE FROM fts_index WHERE doc_id = ?", (doc_id,)
            )
        else:
            conn.execute(
                """INSERT INTO documents
                   (doc_id, source_type, source_id, tokenized_text, original_text,
                    text_hash, timestamp, session_id, episode_type, tags)
                   VALUES (?, 'lesson', ?, ?, ?, ?, ?, '', '', '[]')""",
                (
                    doc_id,
                    str(lesson_number),
                    tokenized,
                    original_text,
                    text_hash,
                    date_str,
                ),
            )

        conn.execute(
            "INSERT INTO fts_index (tokenized_text, doc_id) VALUES (?, ?)",
            (tokenized, doc_id),
        )
        conn.commit()

        self._update_count("lesson_count")
        return True

    def _update_count(self, key: str) -> None:
        """Update document count in meta table."""
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_type = ?",
            (key.replace("_count", ""),),
        )
        count = cur.fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, str(count)),
        )
        conn.commit()

    def sync_episodes(self, all_episodes: list[dict]) -> int:
        """Sync episodes into the index (add new/changed, skip existing).

        Returns number of episodes added/updated.
        """
        added = 0
        for ep in all_episodes:
            if self.add_episode(ep):
                added += 1
        return added

    def sync_lessons(self, lessons: list[dict]) -> int:
        """Sync lessons into the index (incremental: only new lessons).

        Uses the synced lesson count stored in meta to skip already-indexed
        lessons, leveraging the append-only nature of the lessons registry.

        Returns number of lessons added/updated.
        """
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT value FROM meta WHERE key = 'synced_lesson_count'"
        )
        row = cur.fetchone()
        synced_count = int(row[0]) if row else 0

        added = 0
        for i, lesson in enumerate(lessons, 1):
            if i <= synced_count:
                continue  # Already indexed
            if self.add_lesson(lesson, i):
                added += 1

        # Update synced lesson count
        new_count = len(lessons)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('synced_lesson_count', ?)",
            (str(new_count),),
        )
        conn.commit()
        return added

    def rebuild(
        self, all_episodes: list[dict], lessons: list[dict]
    ) -> dict:
        """Full rebuild: drop all data and reindex everything.

        Returns dict with counts of indexed episodes and lessons.
        """
        conn = self._get_conn()

        # Clear all data (including synced lesson count)
        conn.execute("DELETE FROM fts_index")
        conn.execute("DELETE FROM documents")
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('synced_lesson_count', '0')"
        )

        # Clear vector data too (Phase 2)
        if _PHASE2_AVAILABLE and self._use_sqlite_vec is not None:
            drop_vector_data(conn, self._use_sqlite_vec)

        conn.commit()

        ep_count = self.sync_episodes(all_episodes)
        les_count = self.sync_lessons(lessons)

        # Update sync timestamp
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)",
            (now,),
        )
        conn.commit()

        return {"episodes_indexed": ep_count, "lessons_indexed": les_count}

    # --- Search ---

    def search(
        self,
        query: str,
        limit: int = 20,
        tags: list[str] | None = None,
        last: str | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Search the FTS index with BM25 scoring.

        Args:
            query: Natural language search query.
            limit: Maximum results to return.
            tags: Optional tag filter (episode results only).
            last: Optional time range filter (e.g., "7d", "24h").
            offset: Number of results to skip (for pagination).

        Returns:
            List of result dicts with keys: doc_id, source_type, source_id,
            original_text, score, timestamp, session_id, episode_type, tags.
        """
        fts_query = tokenize_for_query(query)
        if fts_query is None:
            return []

        conn = self._get_conn()

        try:
            # FTS5 MATCH with BM25 scoring
            # rank is negative (more relevant = more negative)
            cur = conn.execute(
                """SELECT f.doc_id, rank
                   FROM fts_index f
                   WHERE f.tokenized_text MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit * 3),  # Fetch extra for post-filtering
            )
            fts_results = cur.fetchall()
        except sqlite3.OperationalError:
            # Invalid FTS query
            return []

        if not fts_results:
            return []

        # Load document metadata for results
        results = []
        for doc_id, rank in fts_results:
            cur2 = conn.execute(
                """SELECT source_type, source_id, original_text,
                          timestamp, session_id, episode_type, tags
                   FROM documents WHERE doc_id = ?""",
                (doc_id,),
            )
            doc_row = cur2.fetchone()
            if doc_row is None:
                # Ghost: FTS entry without document (skip silently)
                continue

            source_type, source_id, original_text, timestamp, session_id, episode_type, tags_json = doc_row

            # BM25 score conversion: rank is negative, more negative = more relevant
            score = _bm25_rank_to_score(rank)

            result = {
                "doc_id": doc_id,
                "source_type": source_type,
                "source_id": source_id,
                "original_text": original_text,
                "score": score,
                "timestamp": timestamp,
                "session_id": session_id,
                "episode_type": episode_type,
                "tags": json.loads(tags_json) if tags_json else [],
            }
            results.append(result)

        # Apply tags filter (Python-side, episodes only)
        if tags:
            tag_set = {t.strip().lower() for t in tags if t.strip()}
            filtered = []
            for r in results:
                if r["source_type"] == "lesson":
                    # Lessons pass through tag filter (they don't have tags)
                    filtered.append(r)
                else:
                    # Episode: check if any tag matches
                    ep_tags = {t.lower() for t in r["tags"]}
                    if ep_tags & tag_set:
                        filtered.append(r)
            results = filtered

        # Apply time range filter (Python-side, UNIX timestamp comparison)
        if last:
            delta = _parse_relative_time(last)
            if delta is not None:
                cutoff_ts = (datetime.now(timezone.utc) - delta).timestamp()
                filtered_by_time = []
                for r in results:
                    ts_str = r.get("timestamp", "")
                    if not ts_str:
                        # Empty timestamp: exclude from time-filtered results
                        continue
                    parsed_ts = _parse_iso_timestamp(ts_str)
                    if parsed_ts is not None and parsed_ts >= cutoff_ts:
                        filtered_by_time.append(r)
                results = filtered_by_time

        if offset > 0:
            results = results[offset:]
        return results[:limit]

    def get_indexed_episode_ids(self) -> set[str]:
        """Get all episode IDs currently in the index."""
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT source_id FROM documents WHERE source_type = 'episode'"
        )
        return {row[0] for row in cur.fetchall()}

    def get_stats(self) -> dict:
        """Get index statistics."""
        conn = self._get_conn()
        ep_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_type = 'episode'"
        ).fetchone()[0]
        les_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source_type = 'lesson'"
        ).fetchone()[0]
        last_sync = conn.execute(
            "SELECT value FROM meta WHERE key = 'last_sync'"
        ).fetchone()
        schema_ver = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

        stats = {
            "episode_count": ep_count,
            "lesson_count": les_count,
            "last_sync": last_sync[0] if last_sync else "",
            "schema_version": int(schema_ver[0]) if schema_ver else 0,
            "db_path": self.db_path,
        }

        # Phase 2 stats
        if _PHASE2_AVAILABLE and self._use_sqlite_vec is not None:
            vec_count = get_vector_count(conn, self._use_sqlite_vec)
            stats["vector_count"] = vec_count
            stats["vector_enabled"] = self._vector_enabled
            stats["use_sqlite_vec"] = self._use_sqlite_vec

            # Provider info from meta
            for key in ("embedding_provider", "embedding_model", "embedding_dimensions"):
                cur = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (key,)
                )
                row = cur.fetchone()
                stats[key] = row[0] if row else ""

        return stats


# --- Helper functions ---


def _bm25_rank_to_score(rank: float) -> float:
    """Convert FTS5 BM25 rank to a [0, 1] score.

    FTS5 rank is negative; more relevant = more negative.
    """
    if rank < 0:
        relevance = -rank
        return relevance / (1.0 + relevance)
    return 1.0 / (1.0 + rank)


def _parse_iso_timestamp(ts_str: str) -> float | None:
    """Parse an ISO 8601 timestamp string to UNIX timestamp (float).

    Handles both 'Z' suffix and '+HH:MM'/'-HH:MM' timezone offsets.
    Returns None if parsing fails.
    """
    if not ts_str:
        return None
    try:
        # Python 3.11+ fromisoformat handles 'Z' suffix
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _parse_relative_time(relative_str: str) -> timedelta | None:
    """Parse a relative time string like '7d', '24h', '2w'."""
    match = _RELATIVE_TIME_PATTERN.match(relative_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    elif unit == "w":
        return timedelta(weeks=value)
    return None


# --- P3-6: Snippet generation and score breakdown ---


def extract_query_terms(query: str) -> list[str]:
    """Extract query terms for snippet matching.

    Splits on whitespace, removes hiragana stop words.
    Uses original query text (not FTS5 tokenized form).

    Returns:
        List of query term strings for snippet matching.
    """
    if not query or not query.strip():
        return []
    # Split on whitespace
    raw_terms = query.strip().split()
    # Remove hiragana stop words and empty strings, lowercase ASCII
    terms = []
    for t in raw_terms:
        t = t.strip()
        if not t:
            continue
        if t in STOP_WORDS:
            continue
        # Lowercase ASCII parts
        if all(c.isascii() for c in t):
            t = t.lower()
        terms.append(t)
    return terms


def generate_snippet(
    text: str,
    query_terms: list[str],
    context_chars: int = 50,
    max_snippet_len: int = 200,
) -> str | None:
    """Generate a snippet with matched term highlighted using <<>> markers.

    Searches for query terms in text (case-insensitive), extracts context
    window around first match.

    Args:
        text: Original text to search in.
        query_terms: List of terms to find.
        context_chars: Number of characters before/after match.
        max_snippet_len: Maximum snippet length.

    Returns:
        Snippet string with <<matched_term>> markers, or None if no match.
    """
    if not text or not query_terms:
        return None

    text_lower = text.lower()

    # Find first matching term
    best_pos = -1
    best_term = ""
    for term in query_terms:
        pos = text_lower.find(term.lower())
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos
            best_term = term

    if best_pos < 0:
        return None

    # Extract the actual matched text (preserving original case)
    matched_text = text[best_pos:best_pos + len(best_term)]

    # Calculate context window
    start = max(0, best_pos - context_chars)
    end = min(len(text), best_pos + len(best_term) + context_chars)

    # Build snippet
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""

    before = text[start:best_pos]
    after = text[best_pos + len(best_term):end]

    snippet = f"{prefix}{before}<<{matched_text}>>{after}{suffix}"

    # Trim if too long
    if len(snippet) > max_snippet_len:
        snippet = snippet[:max_snippet_len] + "..."

    return snippet


def format_score_breakdown(result: dict) -> str:
    """Format score breakdown for display.

    Args:
        result: Search result dict, may contain fts_raw_score, vec_raw_score,
                fts_weight, vec_weight fields.

    Returns:
        Formatted score string.
    """
    score = result.get("score", 0.0)
    fts_raw = result.get("fts_raw_score")
    vec_raw = result.get("vec_raw_score")
    fts_w = result.get("fts_weight")
    vec_w = result.get("vec_weight")

    # No breakdown fields: just show score
    if fts_raw is None and vec_raw is None:
        return f"{score:.4f}"

    # NOTE: fts_raw / vec_raw are pre-normalization scores.  The final
    # hybrid score is computed from min-max normalized values, so the
    # raw contributions shown here will NOT sum to the final score.
    # They are displayed as reference values for debugging/transparency.
    parts = []
    if fts_raw is not None and fts_w is not None:
        parts.append(f"raw FTS: {fts_raw:.2f} * {fts_w}")
    if vec_raw is not None and vec_w is not None:
        parts.append(f"raw Vec: {vec_raw:.2f} * {vec_w}")

    if parts:
        return f"{score:.4f} ({', '.join(parts)})"
    return f"{score:.4f}"


def get_lessons_mtime(memory_dir: str) -> float:
    """Get the modification time of the lessons registry file.

    Returns 0.0 if the file doesn't exist.
    """
    lessons_path = os.path.join(memory_dir, "lessons_registry.md")
    try:
        return os.path.getmtime(lessons_path)
    except OSError:
        return 0.0
