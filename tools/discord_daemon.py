#!/usr/bin/env python3
"""Discord receiver daemon for Claude Code.

Runs as a background process (via pythonw.exe or --foreground),
maintaining a persistent Gateway WebSocket connection to receive
Discord messages and buffer them for later processing.

Usage:
    pythonw discord_daemon.py              # Background (no console window)
    python  discord_daemon.py --foreground # Foreground with console output
    python  discord_daemon.py --stop       # Stop a running daemon
    python  discord_daemon.py --status     # Check daemon status

This is a generic Claude Code utility, not part of any specific project.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import time
from typing import Optional

# Add the tools directory to sys.path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from discord_receiver import (
    DISCORD_DATA_DIR,
    DiscordReceiver,
    load_receive_state,
    resolve_bot_token,
)

# Daemon files
PID_FILE = os.path.join(DISCORD_DATA_DIR, "discord_daemon.pid")
DAEMON_LOG_FILE = os.path.join(DISCORD_DATA_DIR, "discord_daemon.log")


def setup_logging(foreground: bool = False) -> logging.Logger:
    """Configure daemon logging."""
    os.makedirs(DISCORD_DATA_DIR, exist_ok=True)
    logger = logging.getLogger("discord_daemon")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with rotation (5MB max, 2 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        DAEMON_LOG_FILE, encoding="utf-8", mode="a",
        maxBytes=5_000_000, backupCount=2,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler (foreground only)
    if foreground:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


# ═══════════════════════════════════════════════════════════════
# PID management (pattern from cron_daemon.py)
# ═══════════════════════════════════════════════════════════════


def write_pid_file() -> None:
    """Write current PID to pid file."""
    os.makedirs(DISCORD_DATA_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    """Remove PID file."""
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def read_pid_file() -> Optional[int]:
    """Read PID from pid file."""
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID is running."""
    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# ═══════════════════════════════════════════════════════════════
# Daemon lifecycle
# ═══════════════════════════════════════════════════════════════


def stop_daemon() -> int:
    """Stop a running daemon by PID. Returns exit code."""
    pid = read_pid_file()
    if pid is None:
        print("No daemon PID file found.", file=sys.stderr)
        return 1

    if not is_process_alive(pid):
        print(f"Daemon (pid {pid}) is not running. Cleaning up PID file.",
              file=sys.stderr)
        remove_pid_file()
        return 0

    print(f"Stopping daemon (pid {pid})...", file=sys.stderr)
    try:
        if sys.platform == "win32":
            import ctypes
            import subprocess as _sp

            # Verify PID belongs to discord_daemon process
            is_daemon = False
            try:
                result = _sp.run(
                    ["wmic", "process", "where", f"ProcessId={pid}",  # noqa: S607
                     "get", "CommandLine", "/VALUE"],
                    capture_output=True, text=True, timeout=5,
                )
                cmdline = result.stdout if result.returncode == 0 else ""
                if "discord_daemon" in cmdline:
                    is_daemon = True
            except Exception as e:
                print(f"PID check warning: {e}", file=sys.stderr)

            if not is_daemon:
                # Fallback: check PID file freshness
                try:
                    pid_mtime = os.path.getmtime(PID_FILE)
                    age = time.time() - pid_mtime
                    if age < 120:  # 2 minutes
                        is_daemon = True
                    else:
                        print(
                            f"WARNING: Cannot verify PID {pid} is a discord_daemon process. "
                            f"PID file age: {age:.0f}s. Refusing to kill -- "
                            f"remove {PID_FILE} manually if stale.",
                            file=sys.stderr,
                        )
                        return 1
                except OSError:
                    print(
                        f"WARNING: Cannot verify PID {pid} is a discord_daemon process. "
                        f"Refusing to kill. Remove {PID_FILE} manually if stale.",
                        file=sys.stderr,
                    )
                    return 1

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, pid)  # PROCESS_TERMINATE
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)

        # Wait for process to die
        for _ in range(10):
            time.sleep(0.5)
            if not is_process_alive(pid):
                break

        if is_process_alive(pid):
            print("Daemon did not stop. Forcing kill.", file=sys.stderr)
            try:
                if sys.platform == "win32":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x0001, False, pid)
                    if handle:
                        kernel32.TerminateProcess(handle, 1)
                        kernel32.CloseHandle(handle)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, AttributeError):
                pass

        remove_pid_file()
        print("Daemon stopped.", file=sys.stderr)
        return 0

    except Exception as e:
        print(f"Error stopping daemon: {e}", file=sys.stderr)
        return 1


def show_status() -> int:
    """Show daemon status. Returns exit code."""
    pid = read_pid_file()
    state = load_receive_state()

    lines = ["Discord Receiver Daemon Status:"]

    if pid is not None:
        alive = is_process_alive(pid)
        lines.append(f"  PID: {pid} ({'running' if alive else 'not running'})")
        if not alive:
            lines.append("  (PID file is stale)")
    else:
        lines.append("  PID: not found (daemon not running)")

    if state:
        lines.append(f"  Last updated: {state.get('last_updated', 'unknown')}")
        gw = state.get("gateway", {})
        lines.append(f"  Connected: {gw.get('connected', False)}")
        if gw.get("bot_name"):
            lines.append(f"  Bot: {gw.get('bot_name')} (ID: {gw.get('bot_id', 'unknown')})")
        if gw.get("connected_since"):
            lines.append(f"  Connected since: {gw.get('connected_since')}")
        lines.append(f"  Messages received: {gw.get('messages_received', 0)}")
        lines.append(f"  Messages filtered: {gw.get('messages_filtered', 0)}")
        lines.append(f"  Messages buffered: {gw.get('messages_buffered', 0)}")
        lines.append(f"  Reconnects: {gw.get('reconnect_count', 0)}")

        buf = state.get("buffer", {})
        if buf:
            lines.append(f"  Buffer: {buf.get('pending', 0)} pending, "
                         f"{buf.get('total', 0)} total")

        consumer = state.get("consumer", {})
        if consumer:
            lines.append(f"  Consumer: {consumer.get('processed', 0)} processed, "
                         f"{consumer.get('failed', 0)} failed, "
                         f"{consumer.get('discarded', 0)} discarded")
    else:
        lines.append("  No state file found")

    print("\n".join(lines), file=sys.stderr)
    return 0


async def async_main(foreground: bool = False) -> None:
    """Async entry point for the daemon."""
    logger = setup_logging(foreground=foreground)

    # Check for already running instance
    existing_pid = read_pid_file()
    if existing_pid and is_process_alive(existing_pid):
        logger.error(f"Daemon already running (pid {existing_pid}). Exiting.")
        sys.exit(1)

    # Resolve token
    token = resolve_bot_token()
    if not token:
        logger.error(
            "No Discord bot token found. "
            "Set DISCORD_BOT_TOKEN environment variable or "
            "save token via discord_connect MCP tool."
        )
        sys.exit(1)

    # Write PID file
    write_pid_file()
    logger.info(f"Daemon starting (pid {os.getpid()})")

    receiver = DiscordReceiver(token=token, logger=logger)

    # Signal handling for graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Start receiver in a task
        receiver_task = asyncio.create_task(receiver.start())

        # Also wait for shutdown signal
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        # Wait for either completion
        done, pending = await asyncio.wait(
            [receiver_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Graceful shutdown
        await receiver.stop()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, shutting down...")
        try:
            await receiver.stop()
        except Exception as stop_err:
            logger.warning(f"Error during shutdown (keyboard interrupt): {stop_err}")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        try:
            await receiver.stop()
        except Exception as stop_err:
            logger.warning(f"Error during shutdown (original error: {e}): {stop_err}")
    finally:
        remove_pid_file()
        logger.info("Daemon stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discord receiver daemon for Claude Code"
    )
    parser.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground with console output"
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop a running daemon"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show daemon status"
    )
    args = parser.parse_args()

    if args.stop:
        sys.exit(stop_daemon())

    if args.status:
        sys.exit(show_status())

    asyncio.run(async_main(foreground=args.foreground))


if __name__ == "__main__":
    main()
