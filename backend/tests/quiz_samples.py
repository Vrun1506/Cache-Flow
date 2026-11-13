"""Canned quiz data for the quiz, judge, hint and guard tests."""

import json

from tutor.schemas.quiz import Expected, QuizItem


def item(type="numeric", value="6", **kw):
    data = dict(
        id="q1",
        type=type,
        prompt="What is it?",
        expected=Expected(value=value),
        hints=["Which two numbers?", "Think about factor pairs.", "The numbers are 2 and 3."],
        skill="factors",
    )
    data.update(kw)
    return QuizItem(**data)


def mcq(index=1, options=("red", "green", "blue")):
    return item("mcq", None, options=list(options), expected=Expected(option_index=index))


def explain(points=("a product is zero only if a factor is zero",)):
    return item("explain", None, expected=Expected(key_points=list(points)))


def quiz_json(**overrides):
    data = {
        "items": [
            {
                "id": "q1", "type": "numeric", "prompt": "What is 2 times 3?", "options": None,
                "expected": {"value": "6", "key_points": None, "option_index": None},
                "hints": ["What do you get when you multiply?", "Use the times table.", "2 times 3 is 6."],
                "skill": "multiplication",
            },
            {
                "id": "q2", "type": "explain", "prompt": "Why does a zero factor give zero?", "options": None,
                "expected": {"value": None, "key_points": ["anything times zero is zero"], "option_index": None},
                "hints": ["What is 5 times 0?", "Think of the zero property.", "Anything multiplied by zero is zero."],
                "skill": "zero property",
            },
        ]
    }
    data.update(overrides)
    return json.dumps(data)
