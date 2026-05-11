"""
Discord message filter (3-layer).

Layer 1: Bot self-exclusion
Layer 2: Allow-list check
Layer 3: Message length check
"""

from __future__ import annotations

from typing import Dict, Any

from discord_receiver_models import ReceiveConfig


class MessageFilter:
    """3-layer message filter.

    Layer 1: Bot self-exclusion (bot_id match)
    Layer 2: Allow-list check (user ID or channel ID)
    Layer 3: Message length check
    """

    def __init__(self, bot_id: str, config: ReceiveConfig):
        self.bot_id = bot_id
        self.config = config

    def check(self, message_data: Dict[str, Any]) -> tuple[bool, str]:
        """Filter a message.

        Returns (passed, reject_reason).
        passed=True means the message should be buffered.
        reject_reason is empty if passed, otherwise describes why rejected.
        """
        author = message_data.get("author", {})
        author_id = author.get("id", "")
        channel_id = message_data.get("channel_id", "")
        content = message_data.get("content", "")

        # Layer 1: Bot self-exclusion
        if author.get("bot", False) or (self.bot_id and author_id == self.bot_id):
            return False, "bot_message"

        # Layer 2: Allow-list
        if not self.config.is_allowed(author_id, channel_id):
            return False, "not_allowed"

        # Layer 3: Message length
        if len(content) > self.config.message_max_length:
            return False, f"message_too_long ({len(content)} > {self.config.message_max_length})"

        return True, ""
