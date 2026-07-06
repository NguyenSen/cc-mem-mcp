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
4. Open `/hooks` once (or restart Claude Code) — the settings watcher only picks
   up `.claude/settings.json` if it existed when the session started.

Your realized `postcompact.cmd` / `postcompact.sh` are gitignored (they hold
machine-specific paths and your Qdrant URL); only the `.example` files are shared.
