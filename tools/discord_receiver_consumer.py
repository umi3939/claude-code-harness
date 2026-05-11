"""
Discord buffer consumer — serial message processing loop.

Consumes buffered messages by executing CLI and sending responses.
Handles security sanitization, personality injection, rate limiting,
retries, and hook dispatch.
"""

from __future__ import annotations

import asyncio
from typing import Optional, Dict, Any

from discord_receiver_models import (
    ReceiveConfig,
    BufferEntry,
    ReceiveLogEntry,
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_DISCARDED,
    _now_iso,
)
from discord_receiver_buffer import ReceiveBuffer, ReceiveLog
from discord_receiver_executor import PromptTemplate, CLIExecutor, ResponseSender

try:
    from security_sanitizer import SecuritySanitizer
except ImportError:
    SecuritySanitizer = None  # type: ignore

try:
    from message_event_hooks import (
        MessageEventContext,
        EVENT_SANITIZED,
        EVENT_SENT,
    )
    _HOOKS_AVAILABLE = True
except ImportError:
    _HOOKS_AVAILABLE = False


class BufferConsumer:
    """Consumes buffered messages by executing CLI and sending responses.

    - Runs as an asyncio task alongside the Gateway event loop
    - Processes one message at a time (serial)
    - Respects rate limits
    - Handles retries and discards
    """

    def __init__(self, buffer: ReceiveBuffer, executor: CLIExecutor,
                 template: PromptTemplate, sender: ResponseSender,
                 receive_log: ReceiveLog, config: ReceiveConfig,
                 logger=None, sanitizer=None,
                 hook_dispatcher=None, personality_collector=None):
        self.buffer = buffer
        self.executor = executor
        self.template = template
        self.sender = sender
        self.receive_log = receive_log
        self.config = config
        self.logger = logger
        self.sanitizer = sanitizer
        self.hook_dispatcher = hook_dispatcher
        self._personality_collector = personality_collector
        self._running = False
        self._poll_interval = 5.0  # seconds between buffer checks
        # Stats
        self.processed_count = 0
        self.failed_count = 0
        self.discarded_count = 0
        self.security_blocked_count = 0

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    def _build_hook_context(self, entry: BufferEntry, event: str,
                            **kwargs) -> "MessageEventContext":
        """Build a MessageEventContext from a BufferEntry for hook dispatch."""
        is_dm = entry.sender_type == "dm"
        return MessageEventContext(
            event=event,
            source="discord",
            sender_id=entry.sender_id,
            channel_id=entry.channel_id,
            message_id=entry.message_id,
            content=entry.content,
            timestamp=entry.received_at,
            conversation_type=entry.sender_type,
            metadata={},
            **kwargs,
        )

    async def run(self) -> None:
        """Main buffer consumption loop."""
        self._running = True
        self._log("info", "Buffer consumer started")

        while self._running:
            try:
                pending = self.buffer.get_pending()
                if not pending:
                    await asyncio.sleep(self._poll_interval)
                    continue

                entry = pending[0]  # FIFO: take first
                await self._process_entry(entry)

            except asyncio.CancelledError:
                self._log("info", "Buffer consumer cancelled")
                break
            except Exception as e:
                self._log("error", f"Buffer consumer error: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _process_entry(self, entry: BufferEntry) -> None:
        """Process a single buffer entry."""
        # Check rate limit
        allowed, reason = self.executor.check_rate_limit(entry.sender_id)
        if not allowed:
            self._log("info", f"Rate limited ({reason}), waiting...")
            await asyncio.sleep(self._poll_interval)
            return  # Don't mark as failed, just skip this cycle

        # Mark as processing
        self.buffer.update_status(entry.id, STATUS_PROCESSING)

        # Security sanitization (before template rendering)
        message_text = entry.content
        if self.sanitizer is not None:
            result = self.sanitizer.sanitize(message_text)
            if result.blocked:
                # MED #2: Security block -> discard immediately, no retry
                self.buffer.update_status(
                    entry.id, STATUS_DISCARDED,
                    result=f"security_blocked: {result.block_reason}",
                )
                self.security_blocked_count += 1
                self._log("warning",
                          f"Message {entry.id} blocked by SecuritySanitizer: {result.block_reason}")
                self._update_log(entry, f"security_blocked: {result.block_reason}")
                return
            message_text = result.text
            if result.metadata.get("injection", {}).get("detected"):
                self._log("info",
                          f"Injection flagged in message {entry.id} (flag mode, continuing)")

            # Dispatch message:sanitized hook
            if self.hook_dispatcher and _HOOKS_AVAILABLE:
                sanitize_findings = []
                if result.metadata.get("injection", {}).get("detected"):
                    sanitize_findings.append("injection_flagged")
                if result.metadata.get("system_tags", {}).get("sanitized_count", 0) > 0:
                    sanitize_findings.append("system_tags_sanitized")
                ctx = self._build_hook_context(entry, EVENT_SANITIZED,
                                                sanitize_findings=sanitize_findings)
                await self.hook_dispatcher.dispatch(ctx)

        # Personality context injection (G1 Phase 1)
        if self.config.personality_enabled and self._personality_collector is not None:
            try:
                from bot_personality import build_enhanced_prompt as _build_enhanced
                context = await self._personality_collector.collect_context(message_text)
                prompt = _build_enhanced(
                    context=context,
                    message=message_text,
                    sender_id=entry.sender_id,
                )
                self._log("info", f"Personality context injected for message {entry.id}")
            except Exception as e:
                self._log("warning", f"Personality injection failed (fail-open): {e}")
                # Fall back to default template
                prompt = self.template.render(
                    message=message_text,
                    sender_id=entry.sender_id,
                )
        else:
            # Render prompt (default, no personality)
            prompt = self.template.render(
                message=message_text,
                sender_id=entry.sender_id,
            )

        # Execute CLI
        self._log("info", f"Executing CLI for message from {entry.sender_id}")
        success, output, error = await self.executor.execute(prompt)

        if success:
            # Record execution for rate limiting (only on success)
            self.executor.record_execution(entry.sender_id)
            # Send response
            try:
                await self.sender.send_response(
                    text=output,
                    sender_id=entry.sender_id,
                    sender_type=entry.sender_type,
                    channel_id=entry.channel_id,
                )
                self.buffer.update_status(
                    entry.id, STATUS_COMPLETED,
                    result=f"OK ({len(output)} chars)"
                )
                self.processed_count += 1
                self._log("info",
                          f"Processed message {entry.id}: success ({len(output)} chars)")

                # Update receive log
                self._update_log(entry, "completed")

                # Dispatch message:sent hook (success)
                if self.hook_dispatcher and _HOOKS_AVAILABLE:
                    ctx = self._build_hook_context(entry, EVENT_SENT,
                                                    send_success=True, send_error="")
                    await self.hook_dispatcher.dispatch(ctx)

            except Exception as send_err:
                self._log("error", f"Failed to send response: {send_err}")
                self._handle_failure(entry, f"send_error: {send_err}")

                # Dispatch message:sent hook (failure)
                if self.hook_dispatcher and _HOOKS_AVAILABLE:
                    ctx = self._build_hook_context(entry, EVENT_SENT,
                                                    send_success=False,
                                                    send_error=str(send_err))
                    await self.hook_dispatcher.dispatch(ctx)
        else:
            self._log("warning", f"CLI execution failed for {entry.id}: {error}")
            self._handle_failure(entry, error)

    def _handle_failure(self, entry: BufferEntry, error: str) -> None:
        """Handle a failed processing attempt."""
        # Reload entry to get current retry_count
        entries = self.buffer._load_all()
        current = None
        for e in entries:
            if e.id == entry.id:
                current = e
                break

        if current is None:
            return

        new_retry = current.retry_count + 1

        if new_retry >= self.config.max_retries:
            # Discard
            self.buffer.update_status(
                entry.id, STATUS_DISCARDED,
                result=f"discarded after {new_retry} retries: {error}"
            )
            self.discarded_count += 1
            self._log("warning",
                      f"Discarded message {entry.id} after {new_retry} retries")
            self._update_log(entry, f"discarded: {error}")
        else:
            # Mark failed, increment retry count
            all_entries = self.buffer._load_all()
            for e in all_entries:
                if e.id == entry.id:
                    e.status = STATUS_FAILED
                    e.retry_count = new_retry
                    e.result = f"attempt {new_retry} failed: {error}"
                    break
            self.buffer._save_all(all_entries)
            self.failed_count += 1
            self._log("info",
                      f"Message {entry.id} failed (attempt {new_retry}/{self.config.max_retries})")
            self._update_log(entry, f"failed (attempt {new_retry}): {error}")

    def _update_log(self, entry: BufferEntry, result: str) -> None:
        """Update the receive log with processing result."""
        log_entry = ReceiveLogEntry(
            timestamp=_now_iso(),
            sender_id=entry.sender_id,
            channel_id=entry.channel_id,
            message_id=entry.message_id,
            body_preview=entry.content[:self.config.log_body_truncate],
            filter_result="processed",
            processing_result=result,
        )
        self.receive_log.append(log_entry)

    def stop(self) -> None:
        """Stop the buffer consumer."""
        self._running = False

    def get_stats(self) -> Dict[str, int]:
        """Get consumer statistics."""
        return {
            "processed": self.processed_count,
            "failed": self.failed_count,
            "discarded": self.discarded_count,
            "security_blocked": self.security_blocked_count,
        }
