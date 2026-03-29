# Claude Code Instructions

## Project

ZTP-Forge — Zero-touch provisioning for bare-metal infrastructure (Cisco network devices + HPE servers).

## Conversation History

**IMPORTANT**: After making any meaningful code changes (new features, bug fixes, refactors, config changes, dependency updates), update `docs/CONVERSATION_HISTORY.md` before committing:

1. Add a new session entry under `## Session Log` with:
   - Date, branch name, PR number (if applicable), and commit hashes
   - Summary of what was done
   - Key decisions made
2. Update the `## Current State of the Project` section to reflect:
   - Any newly implemented features (move from "needs to be built" to "exists")
   - Any new known issues or open items
3. Keep entries concise but informative enough for a future session to understand context without re-exploring the codebase.

## Development

- Python 3.11+, build with Hatchling
- Lint: `ruff check src/`
- Type check: `mypy src/`
- Tests: `pytest tests/`
- Source layout: `src/ztp_forge/`
- Dashboard: Django app at `src/ztp_forge/dashboard/`
