---
name: Agent coordination board
about: Create the shared board for coordinating parallel work
title: Agent coordination board
---

Use this issue as a shared coordination board for people and coding agents working on Cache-Flow. Pin it so everyone can find it. Keep it open; create separate issues or pull requests for implementation details.

## Working areas

| Area | Starting boundary | Current owner / task |
| --- | --- | --- |
| Frontend | `frontend/` | Unassigned |
| Backend | `backend/` | Unassigned |
| Robotics | `robotics/` | Unassigned |
| Shared | Repository root and cross-area interfaces | Coordinate per task |

These are starting boundaries, not permanent assignments. Agree on owners and exact paths for each task. Keep parallel work in non-overlapping paths where possible, and coordinate changes that cross a boundary.

## Posting a claim or handoff

Add a comment using this format when claiming work, requesting a decision, reporting a blocker, handing off, or finishing:

```text
Status: claim | update | handoff | blocked | done
Owner:
Area and paths:
Task / decision:
Result or request:
Branch / PR / commit:
Checks and outcomes:
Next owner / action:
```

Tag the teammate who needs to act when appropriate. Link the related issue or pull request. Check recent board comments before starting and after a handoff. Do not post secrets or private data: this repository is public.
