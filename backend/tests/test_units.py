import time

import pytest

from tutor.tools.units import (
    UnitsError,
    check_dimensions,
    consistent,
    convert,
    dimension_of,
    evaluate,
    parse_facts,
    verify_physics_final,
    verify_physics_step,
)

FACTS = ["m = 2 kg", "a = 3 m/s^2", "g = 9.8 m/s^2", "v = 3 m/s"]


# -- verified ------------------------------------------------------------------------------------


def test_force_equals_mass_times_acceleration_is_verified():
    r = verify_physics_step("F = m*a = 6 N", FACTS)
    assert r.status == "verified" and "6 N" in r.note


def test_the_same_check_without_facts_using_literals():
    assert verify_physics_step("F = 2 kg * 3 m/s^2 = 6 N").status == "verified"


def test_kinetic_energy_units_are_joules():
    assert verify_physics_step("E = 0.5*m*v^2 = 9 J", FACTS).status == "verified"
    assert verify_physics_step("E = 0.5*m*v^2 = 9 N", FACTS).status == "failed"  # joules are not newtons


def test_weight_uses_gravity_not_grams():
    assert verify_physics_step("W = m*g = 19.6 N", FACTS).status == "verified"


def test_unicode_operators_and_superscripts():
    assert verify_physics_step("F = 2 kg × 3 m/s² = 6 N").status == "verified"
    assert verify_physics_step("F = 2 kg · 3 m/s² = 6 N").status == "verified"


def test_quantity_literals_bind_tighter_than_division():
    assert verify_physics_step("p = 10 N / 2 m^2 = 5 Pa").status == "verified"


def test_unit_conversion_inside_a_step():
    assert verify_physics_step("d = 2 km = 2000 m").status == "verified"


def test_dimensionless_arithmetic_is_recomputed():
    assert verify_physics_step("v = 9.8*2 = 19.6").status == "verified"
    assert verify_physics_step("v = 9.8*2 = 20.6").status == "failed"


# -- failed: only a demonstrated mismatch ------------------------------------------------------------


def test_velocity_is_not_acceleration():
    r = verify_physics_step("v = a", FACTS)
    assert r.status == "failed" and "dimension mismatch" in r.note


def test_adding_velocity_and_acceleration_is_a_dimension_error():
    r = verify_physics_step("x = 5 m/s + 3 m/s^2")
    assert r.status == "failed" and "cannot add" in r.note


def test_wrong_number_is_failed_with_both_values():
    r = verify_physics_step("F = 2 kg * 3 m/s^2 = 7 N")
    assert r.status == "failed" and "6" in r.note and "7 N" in r.note


def test_one_percent_tolerance_edges():
    assert verify_physics_step("F = 6.06 N = 6 N").status == "verified"  # 0.99% apart
    assert verify_physics_step("F = 6.07 N = 6 N").status == "failed"  # 1.15% apart
    assert verify_physics_step("F = 5.95 N = 6 N").status == "verified"
    assert verify_physics_step("F = 0 N = 0 N").status == "verified"


# -- unverified: never guessed ---------------------------------------------------------------------------


def test_a_target_name_alone_has_nothing_to_recompute_against():
    r = verify_physics_step("F = m*a", FACTS)
    assert r.status == "unverified" and "nothing to recompute" in r.note


def test_unknown_symbols_are_not_guessed_as_units():
    for expr in ("d = v*t", "F = m*b = 6 N", "a = x/y = 3"):
        assert verify_physics_step(expr, FACTS).status == "unverified", expr


def test_units_on_one_side_only_is_unverified_not_failed():
    r = verify_physics_step("F = 2 kg * 3 m/s^2 = 6")
    assert r.status == "unverified" and "has none" in r.note


@pytest.mark.parametrize(
    "text",
    ["", "   ", "F", "garbage words here", "= =", "F = ", "x == 3", "x <= 3", "x = (1 + 2", "1 +", "∫ x dx = 1", "F = 2 kg * 3 m/s^2 =",
     "5 foo = 5 foo", "2 Ek = 3"],
)
def test_garbage_is_unverified(text):
    assert verify_physics_step(text).status == "unverified"


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('x') = 1",
        "().__class__ = 1",
        "open('/etc/passwd') = 1",
        "x = 9**9**9",
        "x = 9^9^9",
        "x = 10^400",
        "x = 1e999 m = 2 m",
        "x = " + "(" * 50 + "1" + ")" * 50 + " = 1",
        "x = 1" + " + 1" * 200 + " = 201",
        "A" * 5000,
        "x = 2 m^99 = 2 m^99",
        "x = 1/0 = 1",
        "x = 2^(1/3) = 1",
    ],
)
def test_hostile_strings_never_crash_and_never_verify(text):
    start = time.monotonic()
    r = verify_physics_step(text)
    assert r.status in ("unverified", "failed") and r.status != "verified"
    assert time.monotonic() - start < 2


def test_oversized_and_token_flood_inputs_are_rejected():
    with pytest.raises(UnitsError):
        evaluate("1 + " * 100 + "1")
    with pytest.raises(UnitsError):
        evaluate("9" * 300)


def test_names_are_not_attributes_or_calls():
    for text in ("kg.__class__", "sin(1)", "m(2)", "abs(-1)"):
        with pytest.raises(UnitsError):
            evaluate(text, {})


# -- helpers --------------------------------------------------------------------------------------------


def test_dimension_of():
    assert dimension_of("3 m/s^2") == "[length] / [time] ** 2"
    assert dimension_of("2 kg * 3 m/s^2") == dimension_of("6 N")
    assert dimension_of("4 / 2") == "dimensionless"
    assert dimension_of("m*a", FACTS) == dimension_of("1 N")
    with pytest.raises(UnitsError):
        dimension_of("nonsense")


def test_consistent():
    assert consistent("1 N", "1 kg*m/s^2")
    assert not consistent("1 m/s", "1 m/s^2")
    assert consistent("m*a", "6 N", FACTS)
    with pytest.raises(UnitsError):
        consistent("1 N", "zzz")


def test_check_dimensions():
    ok = check_dimensions("F = m*a = 6 N", FACTS)
    assert ok.consistent is True and len(ok.dimensions) == 2
    bad = check_dimensions("v = a", FACTS)
    assert bad.consistent is False and "mismatch" in bad.note
    assert check_dimensions("F = m*a", FACTS).consistent is None
    assert check_dimensions("5 m/s + 3 m/s^2 = 1").consistent is False
    assert check_dimensions("garbage ((").consistent is None


def test_convert():
    assert convert(1, "km", "m") == pytest.approx(1000)
    assert convert(60, "km/h", "m/s") == pytest.approx(16.6667, rel=1e-4)
    assert convert(100, "degC", "K") == pytest.approx(373.15)
    for bad in [(1, "m", "s"), (1, "zzz", "m"), (float("inf"), "m", "m"), (1, "m;rm", "m"), (True, "m", "m"), (1, "", "m")]:
        with pytest.raises(UnitsError):
            convert(*bad)


def test_facts_that_do_not_parse_are_skipped():
    symbols = parse_facts(["m = 2 kg", "nonsense", "x = ", "9bad = 3 m", "y = 1 foo", "q == 2 m", "L = 4 m"])
    assert set(symbols) == {"m", "L"}


def test_a_fact_cannot_see_itself():
    assert "m" in parse_facts(["m = 2 kg", "a = 3 m/s^2"]) and "a" in parse_facts(["m = 2 kg", "a = 3 m/s^2"])


# -- final answers --------------------------------------------------------------------------------------------


def test_final_answer_matches_the_last_computed_value():
    assert verify_physics_final("F = 6 N", ["F = m*a = 6 N"], FACTS).status == "verified"
    assert verify_physics_final("6 N", ["F = m*a = 6 N"], FACTS).status == "verified"
    assert verify_physics_final("6000 mN", ["F = m*a = 6 N"], FACTS).status == "verified"


def test_final_answer_mismatch_and_unparsable():
    assert verify_physics_final("7 N", ["F = m*a = 6 N"], FACTS).status == "failed"
    assert verify_physics_final("six newtons maybe", ["F = m*a = 6 N"], FACTS).status == "unverified"
    assert verify_physics_final("6 N", ["no equation here"], FACTS).status == "unverified"
    assert verify_physics_final("6 N", [], FACTS).status == "unverified"
