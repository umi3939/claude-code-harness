#!/usr/bin/env python3
"""Bot personality context injection for Discord responses.

Collects memory, emotion, and personality context to enhance Discord bot
responses. Designed as a read-only, fail-open, stateless module.

Design: docs/design_g1_bot_personality_phase1.md
Analysis: docs/analysis_g1_bot_personality_phase1_20260322.md
"""

import asyncio
import json
import logging
import os
import re
import sqlite3

logger = logging.getLogger(__name__)

# --- Constants ---

# Memory search result limit (prevents huge prompts)
MEMORY_SEARCH_LIMIT = 5

# Overall timeout for context collection (seconds)
CONTEXT_COLLECT_TIMEOUT = 3.0

# Max prompt length (Windows command line safe, well under 32767)
PROMPT_MAX_LENGTH = 16000

# Placeholders to escape in context output (analysis #3)
_PLACEHOLDER_PATTERN = re.compile(r"\{(sender_id|message)\}")

# --- Personality Template (static) ---

DEFAULT_PERSONALITY_TEMPLATE = (
    "You are the assistant. You are responding to a Discord message.\n"
    "Respond in the same language as the message. "
    "If the message is in Japanese, respond in Japanese.\n"
    "Be concise and direct. Do not use emoji or emoticons.\n"
    "You have your own memories and emotional state "
    "which are provided below as context. "
    "Use them naturally if relevant, but do not force them into the conversation."
)


# --- Placeholder escaping ---

def _escape_placeholders(text: str) -> str:
    """Escape template placeholders to prevent accidental substitution.

    Replaces {sender_id} and {message} with bracket-free versions
    so PromptTemplate.render's str.replace won't match them.
    """
    return _PLACEHOLDER_PATTERN.sub(
        lambda m: "[" + m.group(1) + "]", text
    )


# --- Memory search function factory ---

def _create_memory_search_fn(memory_dir: str) -> callable:
    """Create a memory search function using FTS5 via a new SQLite connection.

    Opens a new connection per call (analysis #2: thread safety).
    Uses check_same_thread=False since we open/close per call.
    """
    db_path = os.path.join(memory_dir, "semantic_index.db")

    def search_fn(query: str, limit: int) -> list:
        if not os.path.exists(db_path):
            return []

        # Import tokenizer from semantic_index
        try:
            import semantic_index as si
            fts_query = si.tokenize_for_query(query)
        except (ImportError, Exception):
            # Fallback: simple quoting
            fts_query = f'"{query}"'

        if fts_query is None:
            return []

        conn = None
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            cur = conn.execute(
                """SELECT f.doc_id, rank
                   FROM fts_index f
                   WHERE f.tokenized_text MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            )
            fts_results = cur.fetchall()

            if not fts_results:
                return []

            results = []
            for doc_id, rank in fts_results:
                cur2 = conn.execute(
                    """SELECT source_type, source_id, original_text,
                              timestamp, session_id, episode_type, tags
                       FROM documents WHERE doc_id = ?""",
                    (doc_id,),
                )
                row = cur2.fetchone()
                if row is None:
                    continue

                source_type, source_id, original_text, timestamp, session_id, episode_type, tags_json = row
                score = -rank if rank else 0.0

                results.append({
                    "doc_id": doc_id,
                    "source_type": source_type,
                    "source_id": source_id,
                    "original_text": original_text,
                    "score": score,
                    "timestamp": timestamp,
                    "session_id": session_id,
                    "episode_type": episode_type,
                    "tags": json.loads(tags_json) if tags_json else [],
                })

            return results
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.warning("Memory search FTS5 error: %s", e)
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    logger.debug(f"DB close warning: {e}")

    return search_fn


# --- Emotion read function factory ---

def _create_emotion_read_fn(memory_dir: str):
    """Create an emotion read function that reads from emotion_state.json.

    Uses emotion_state.load_state + apply_session_decay (analysis #7).
    """
    def read_fn() -> dict:
        try:
            import emotion_state as es
            state = es.load_state(memory_dir)
            state = es.apply_session_decay(state)
            return state
        except (ImportError, Exception) as e:
            logger.warning("Emotion state read error: %s", e)
            # Fallback: try direct file read
            try:
                filepath = os.path.join(memory_dir, "emotion_state.json")
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {
                    "fulfillment": 0.0,
                    "tension": 0.0,
                    "affinity": 0.0,
                    "last_updated": "",
                }

    return read_fn


# --- Collector ---

class PersonalityContextCollector:
    """Collects personality, memory, and emotion context for prompt enhancement.

    All external dependencies are injected via constructor (DI).
    Stateless: no state carried between calls.
    Fail-open: any component failure produces empty context for that component.
    """

    def __init__(
        self,
        memory_search_fn=None,
        emotion_read_fn=None,
        tone_compute_fn=None,
        personality_template: str | None = None,
    ):
        """Initialize the collector.

        Args:
            memory_search_fn: Callable(query: str, limit: int) -> list[dict].
                              Can be sync or async.
            emotion_read_fn: Callable() -> dict with fulfillment/tension/affinity.
                             Can be sync or async.
            tone_compute_fn: Callable() -> dict with primary_tone/tone_weights/description.
                             Can be sync or async. Optional (DI for compute_tone).
            personality_template: Static personality text. Defaults to
                                 DEFAULT_PERSONALITY_TEMPLATE.
        """
        self.memory_search_fn = memory_search_fn
        self.emotion_read_fn = emotion_read_fn
        self.tone_compute_fn = tone_compute_fn
        self.personality_template = (
            personality_template if personality_template is not None
            else DEFAULT_PERSONALITY_TEMPLATE
        )

    async def _search_memory(self, query: str) -> str:
        """Search memory and format results. Returns empty string on failure."""
        if self.memory_search_fn is None:
            return ""

        try:
            fn = self.memory_search_fn
            if asyncio.iscoroutinefunction(fn):
                results = await fn(query, MEMORY_SEARCH_LIMIT)
            else:
                results = await asyncio.to_thread(fn, query, MEMORY_SEARCH_LIMIT)

            if not results:
                return ""

            lines = []
            for r in results[:MEMORY_SEARCH_LIMIT]:
                text = r.get("original_text", "")
                if text:
                    # Truncate individual results
                    if len(text) > 200:
                        text = text[:200] + "..."
                    lines.append(f"- {text}")

            raw = "\n".join(lines)
            return _escape_placeholders(raw)
        except Exception as e:
            logger.warning("Memory search failed (fail-open): %s", e)
            return ""

    async def _compute_tone_result(self) -> dict | None:
        """Compute tone via injected function. Returns None on failure."""
        if self.tone_compute_fn is None:
            return None

        try:
            fn = self.tone_compute_fn
            if asyncio.iscoroutinefunction(fn):
                return await fn()
            else:
                return await asyncio.to_thread(fn)
        except Exception as e:
            logger.warning("Tone compute failed (fail-open): %s", e)
            return None

    async def _read_emotion_raw(self) -> dict | None:
        """Read raw emotion state dict. Returns None on failure."""
        if self.emotion_read_fn is None:
            return None

        try:
            fn = self.emotion_read_fn
            if asyncio.iscoroutinefunction(fn):
                return await fn()
            else:
                return await asyncio.to_thread(fn)
        except Exception as e:
            logger.warning("Emotion raw read failed (fail-open): %s", e)
            return None

    async def collect_context(self, message: str) -> dict:
        """Collect all context for prompt enhancement.

        Args:
            message: The user's message text.

        Returns:
            Dict with keys: personality_template, memory_context,
            emotion_context, tone_context.
        """
        memory_context = ""
        emotion_context = ""
        tone_context = ""
        emotion_state_raw = None
        tone_result_raw = None

        try:
            # Run memory search, emotion raw read, and tone compute concurrently
            # (Pattern A: 3 parallel. emotion double-read via compute_tone is
            #  acceptable per design doc section 4.2)
            memory_task = asyncio.ensure_future(self._search_memory(message))
            emotion_raw_task = asyncio.ensure_future(self._read_emotion_raw())
            tone_task = asyncio.ensure_future(self._compute_tone_result())

            done, pending = await asyncio.wait(
                [memory_task, emotion_raw_task, tone_task],
                timeout=CONTEXT_COLLECT_TIMEOUT,
            )

            # Cancel any pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # Collect results from completed tasks
            if memory_task in done and not memory_task.cancelled():
                try:
                    memory_context = memory_task.result()
                except Exception:
                    memory_context = ""

            if emotion_raw_task in done and not emotion_raw_task.cancelled():
                try:
                    emotion_state_raw = emotion_raw_task.result()
                except Exception:
                    emotion_state_raw = None

            if tone_task in done and not tone_task.cancelled():
                try:
                    tone_result_raw = tone_task.result()
                except Exception:
                    tone_result_raw = None

        except Exception as e:
            logger.warning("Context collection failed (fail-open): %s", e)

        # Format emotion context from raw state
        if emotion_state_raw and isinstance(emotion_state_raw, dict):
            axes = ["fulfillment", "tension", "affinity"]
            parts = []
            for axis in axes:
                val = emotion_state_raw.get(axis)
                if val is not None and isinstance(val, (int, float)):
                    parts.append(f"{axis}={val:+.3f}")
            raw_emotion = ", ".join(parts) if parts else ""
            emotion_context = _escape_placeholders(raw_emotion)

        # Generate tone instruction text
        try:
            from emotion_tone_converter import generate_tone_instruction
            raw_tone = generate_tone_instruction(emotion_state_raw, tone_result=tone_result_raw)
            tone_context = _escape_placeholders(raw_tone)
        except Exception as e:
            logger.warning("Tone instruction generation failed (fail-open): %s", e)
            try:
                from emotion_tone_converter import NEUTRAL_DEFAULT_INSTRUCTION
                tone_context = _escape_placeholders(NEUTRAL_DEFAULT_INSTRUCTION)
            except Exception:
                tone_context = ""

        return {
            "personality_template": self.personality_template,
            "memory_context": memory_context,
            "emotion_context": emotion_context,
            "tone_context": tone_context,
        }


# --- Prompt builder ---

def build_enhanced_prompt(
    context: dict,
    message: str,
    sender_id: str = "",
) -> str:
    """Build an enhanced prompt from collected context and message.

    Order: personality -> tone -> emotion -> memory -> message (per design C20-2).
    Does NOT include sender_id in the output (analysis #3).

    Args:
        context: Dict from PersonalityContextCollector.collect_context().
        message: The user's message text.
        sender_id: The sender's Discord ID (NOT included in prompt).

    Returns:
        Complete prompt string.
    """
    sections = []

    # 1. Personality template (always present)
    template = context.get("personality_template", "")
    if template:
        sections.append(template)

    # 2. Tone instruction (C20-2)
    tone = context.get("tone_context", "")
    if tone:
        sections.append(f"\n[Tone instruction]\n{tone}")

    # 3. Emotion state
    emotion = context.get("emotion_context", "")
    if emotion:
        sections.append(f"\n[Current emotional state]\n{emotion}")

    # 4. Related memories
    memory = context.get("memory_context", "")
    if memory:
        sections.append(f"\n[Related memories]\n{memory}")

    # 5. User message
    sections.append(f"\nMessage from user:\n{message}")

    prompt = "\n".join(sections)

    # Enforce size limit (truncate memory section if needed)
    if len(prompt) > PROMPT_MAX_LENGTH:
        # Rebuild with truncated memory
        tone_len = len(tone) if tone else 0
        memory_budget = PROMPT_MAX_LENGTH - len(template) - tone_len - len(emotion) - len(message) - 200
        if memory_budget < 0:
            memory_budget = 0
        if memory_budget > 0 and len(memory) > memory_budget:
            truncated_memory = memory[:memory_budget] + "..."
        elif memory_budget > 0:
            truncated_memory = memory
        else:
            truncated_memory = ""

        sections_truncated = []
        if template:
            sections_truncated.append(template)
        if tone:
            sections_truncated.append(f"\n[Tone instruction]\n{tone}")
        if emotion:
            sections_truncated.append(f"\n[Current emotional state]\n{emotion}")
        if truncated_memory:
            sections_truncated.append(f"\n[Related memories]\n{truncated_memory}")
        sections_truncated.append(f"\nMessage from user:\n{message}")
        prompt = "\n".join(sections_truncated)

    return prompt


# --- Factory ---

def _create_tone_compute_fn(memory_dir: str):
    """Create a tone compute function that calls tone_modulation.compute_tone.

    Uses a closure to capture memory_dir. Returns None on import failure.
    """
    def compute_fn() -> dict:
        try:
            import tone_modulation
            return tone_modulation.compute_tone(memory_dir)
        except Exception as e:
            logger.warning("tone_modulation.compute_tone error: %s", e)
            return None

    return compute_fn


def create_collector(memory_dir: str, personality_template: str | None = None) -> PersonalityContextCollector:
    """Create a PersonalityContextCollector with real dependencies.

    Args:
        memory_dir: Path to the memory directory containing semantic_index.db
                    and emotion_state.json.
        personality_template: Optional custom personality template.

    Returns:
        A configured PersonalityContextCollector.
    """
    search_fn = _create_memory_search_fn(memory_dir)
    emotion_fn = _create_emotion_read_fn(memory_dir)
    tone_fn = _create_tone_compute_fn(memory_dir)

    return PersonalityContextCollector(
        memory_search_fn=search_fn,
        emotion_read_fn=emotion_fn,
        tone_compute_fn=tone_fn,
        personality_template=personality_template,
    )
