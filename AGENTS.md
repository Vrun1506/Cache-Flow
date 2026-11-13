# Working together in Cache-Flow

Before starting, check the repository's open issues and pull requests for related work and an agent coordination board. If the team has created the board, read its latest comments and use it for claims, blockers, decisions, and handoffs. The board is a shared record, not a substitute for a focused issue or pull request.

## Divide work by area

- `frontend/`: the Next.js application and its configuration. Follow `frontend/AGENTS.md` for frontend work.
- `backend/`: backend services and dependencies.
- `robotics/`: robotics-related work.
- Repository root and shared interfaces: coordinate with the people working in affected areas.

These boundaries are starting points, not permanent assignments. For each task, agree on an owner and specific paths. Keep parallel work in non-overlapping paths where possible; coordinate before changing another person's area or a shared interface. Use separate branches or worktrees where practical, and do not overwrite other people's changes.

## Coordinate and hand off

On the board, record the owner, task, paths, status, branch or pull request, checks and outcomes, and next action. Identify and tag the teammate who needs to act when appropriate. Link a focused issue or pull request for implementation details. If a decision or interface from another area blocks dependent work, make the request explicit and wait for the answer.

If the board does not exist yet, a repository maintainer can create it using `.github/ISSUE_TEMPLATE/agent-board.md` and pin it. Until then, coordinate through the relevant issue or pull request instead of assuming a board exists.

This repository is public. Never put credentials, secrets, or private data in issues, pull requests, or committed files.
