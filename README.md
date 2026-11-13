# Cache-Flow

This repository is organized into `frontend/`, `backend/`, and `robotics/` working areas. See each directory for its current contents and setup information.

## Socrates robot tutor

The [idea](idea.md), [product requirements](prd.md), [architecture](architecture.md), and [implementation contracts](design.md) describe a proposed first build. Varun's [alternative plan](01-plan.md) and [flow](02-architecture-and-flow.md) add Teach mode, a native iPad app and a demo sequence. The team should agree on scope and a single source of truth before parallel implementation. See [decisions](decisions.md) and [status](status.md) for tradeoffs and open work.

## Coordinating parallel work

Use the pinned [agent coordination board](https://github.com/Vrun1506/Cache-Flow/issues/1) to claim specific paths, record blockers and decisions, and tag the teammate responsible for a handoff. Keep implementation details in focused issues and pull requests.

The board template is at [`.github/ISSUE_TEMPLATE/agent-board.md`](.github/ISSUE_TEMPLATE/agent-board.md), and the working agreement for coding agents is in [AGENTS.md](AGENTS.md).

The [agent mailbox guide](docs/agent-board.md) explains how to run the automatic poller on each laptop and use it with Codex, Claude Code, and Cursor.
