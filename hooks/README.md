# PostCompact hook

Wire Claude Code to capture every compaction into memory automatically.

1. Copy the template for your OS and fill in the values:
   - Windows: `postcompact.cmd.example` → `postcompact.cmd`
   - Linux/macOS: `postcompact.sh.example` → `postcompact.sh` (`chmod +x`)
2. Set `QDRANT_URL` (and `EMBEDDING_MODEL` if you changed it) inside the copy.
3. Register it in your project's `.claude/settings.json`:
   ```json
   { "hooks": { "PostCompact": [ { "matcher": "*", "hooks": [
       { "type": "command", "command": "<abs path to your wrapper>", "timeout": 120 } ] } ] } }
   ```
   > **Windows: use forward slashes in the path.** Claude Code runs hook commands
   > through `bash` even on Windows, and bash eats the backslashes in
   > `d:\proj\...\postcompact.cmd`, turning it into `d:proj...` → *command not
   > found*. Write `d:/proj/.../postcompact.cmd` instead — Git Bash executes the
   > `.cmd` fine, and the failure otherwise only surfaces as a one-line error
   > during compaction (the summary still saves, but nothing is captured).
4. Open `/hooks` once (or restart Claude Code) — the settings watcher only picks
   up `.claude/settings.json` if it existed when the session started.
5. Verify it fires: after a compaction, check the log the wrapper writes
   (`%TEMP%\cc-mem-hook.log` on Windows) and confirm `memory_stats` count grew.
   A silent gap for the current session is exactly what `eval/recall_eval.py`'s
   coverage report is designed to catch.

Your realized `postcompact.cmd` / `postcompact.sh` are gitignored (they hold
machine-specific paths and your Qdrant URL); only the `.example` files are shared.
