from sample_data import ORCHESTRATOR_OK, SOLUTION_OK
from tutor.llm.budget import estimate_tokens
from tutor.schemas import Plan, Solution
from tutor.state.memory import BoardRef, Memory


def loaded() -> Memory:
    m = Memory(session_id="s", page_id="p")
    plan = Plan.model_validate_json(ORCHESTRATOR_OK)
    sol = Solution.model_validate_json(SOLUTION_OK)
    m.set_solution(plan, sol, {"s1": "unverified", "s2": "verified", "s3": "verified", "final": "verified"})
    return m


def test_digest_has_ids_titles_exprs_and_status_but_no_rationale():
    d = loaded().solution_digest()
    assert "s2: Factorise | (x+2)*(x+3)=0 [verified]" in d
    assert "final:" in d and "Brackets use the pair" not in d


def test_qa_slice_includes_selection_and_recent_turns_and_stays_small():
    m = loaded()
    m.add_board(BoardRef(id="b-2-1", step_id="s2", kind="write", label="(x+2)(x+3)=0"))
    m.selected_object = "b-2-1"
    m.current_step = 3
    for i in range(10):
        m.add_turn("student", f"question {i}")
    s = m.qa_slice()
    assert "student selected: (x+2)(x+3)=0 (step 2)" in s
    assert "question 9" in s and "question 1\n" not in s
    assert estimate_tokens(s) <= 2000


def test_last_turns_are_capped():
    m = loaded()
    for i in range(20):
        m.add_turn("tutor", f"t{i}")
    assert len(m.last_turns) <= 6


def test_slice_drops_trailing_parts_before_leading_ones():
    m = loaded()
    m.turns_summary = "word " * 3000
    s = m.qa_slice()
    assert estimate_tokens(s) <= 2000
    assert s.startswith("question:") and "solution:" in s


def test_lesson_slice_only_contains_the_requested_steps():
    m = loaded()
    s = m.lesson_slice(["s2"])
    assert "s2 [verified]" in s and "s1 [" not in s and "s3 [" not in s
    assert "level: secondary" in s


def test_lesson_slice_marks_unverified_steps():
    s = loaded().lesson_slice(["s1"])
    assert "s1 [unverified]" in s


def test_board_index_replaces_same_id_and_limits_recent():
    m = loaded()
    for i in range(10):
        m.add_board(BoardRef(id=f"b-1-{i}", step_id="s1"))
    m.add_board(BoardRef(id="b-1-0", step_id="s1", label="again"))
    assert len(m.board_index) == 10
    assert [r.id for r in m.board_recent(3)][-1] == "b-1-0"


def test_attempts_update_skills_within_bounds():
    m = loaded()
    m.record_attempt("q1", True, 0, 0, skill="factoring")
    assert m.skills["factoring"] == 3
    m.record_attempt("q2", False, 3, 3, skill="factoring")
    m.record_attempt("q3", False, 3, 3, skill="factoring")
    m.record_attempt("q4", False, 3, 3, skill="factoring")
    m.record_attempt("q5", False, 3, 3, skill="factoring")
    assert m.skills["factoring"] == 0 and "factoring" in m.skill_gaps()


def test_summary_triggers_and_fallback():
    m = loaded()
    assert not m.needs_summary()
    for i in range(6):
        m.add_turn("student", "hi")
    assert m.needs_summary()
    assert "Question:" in m.fallback_summary()
    m.apply_summary("A short summary.")
    assert not m.needs_summary() and m.turns_summary == "A short summary."


def test_object_lookups():
    m = loaded()
    m.add_board(BoardRef(id="b-3-1", step_id="s3", label="roots"))
    assert m.step_for_object("b-3-1") == 3
    assert m.step_for_object("nope") is None
    assert m.label_for("nope") == "nope"
