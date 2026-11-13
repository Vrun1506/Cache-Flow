"""Evidence-backed skills (Interface.md "skills"): helped work never raises a level."""

from __future__ import annotations

from dataclasses import dataclass, field

from .wire import skill_level

MAX_LEVEL = 5


@dataclass
class Skill:
    skill: str
    label: str
    level: int = 0
    independent_evidence: int = 0
    assisted: bool = False


@dataclass
class SkillBook:
    skills: dict[str, Skill] = field(default_factory=dict)

    @staticmethod
    def slug(name: str) -> str:
        out = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
        return "_".join(p for p in out.split("_") if p)[:30] or "general"

    def get(self, name: str) -> Skill:
        name = name[:30]  # the label is cut to 30 characters, so the key must come from the same text
        key = self.slug(name)
        if key not in self.skills:
            self.skills[key] = Skill(key, name or "General")
        return self.skills[key]

    def seed(self, name: str) -> None:
        self.get(name)

    def record(self, name: str, *, correct: bool, independent: bool) -> Skill:
        """Only a correct answer with no help raises the level; help marks the skill assisted."""
        s = self.get(name)
        if correct and independent:
            s.level = min(MAX_LEVEL, s.level + 1)
            s.independent_evidence += 1
        elif not independent:
            s.assisted = True
        return s

    def mark_assisted(self, name: str) -> None:
        self.get(name).assisted = True

    def wire(self) -> list[dict]:
        return [skill_level(s.skill, s.label, s.level, s.independent_evidence, s.assisted) for s in self.skills.values()]
