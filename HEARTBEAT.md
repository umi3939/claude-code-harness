# HEARTBEAT

## Concerns

| Concern | Check | Action | Safety |
|---------|-------|--------|--------|
| Discord pending | `discord_receive_pending` | `discord_send` to respond | Autonomous (SecuritySanitizer applied) |
| Cron errors | `persistent_cron_list` + `persistent_cron_logs` | Report only | Report only (repair requires human) |
| Memory system | `memory_status` | Report only | Report only |
| Cron notifications | `persistent_cron_notifications` | Report only | Report only |
| STM capacity | `stm_read` (check total) | If total > 45: `memory_consolidate` mode=check, then save if needed | Conditional autonomous |
| Daemon health | Run `python tools/daemon_monitor.py` or import `check_and_restart_all` from `daemon_monitor` | Restart dead daemons + log + Discord notify (if possible) | Conditional autonomous (skip restart after 3 consecutive failures) |
| Emotion check | `emotion_get` | If flat (all ≈0): `emotion_react` with contextual label | Autonomous |
| Self observation | `self_snapshot` | Record observations to STM via `stm_write` | Report only |
| Activation surface | `activation_surface` with context="heartbeat periodic check" | Act on surfaced concerns | Report only |
| Behavior analysis | `behavior_analyze` | Review behavioral patterns from observation log | Report only |

## Rules
- HEARTBEAT.md is READ-ONLY. Do NOT modify it.
- Do NOT register new cron jobs.
- Do NOT use emojis.
- If a previous action failed (see action history below), skip that action this round.
