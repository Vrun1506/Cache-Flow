import pytest

from tutor.tools.chem import (
    ELEMENTS, ChemError, balance, check_reaction, is_balanced, looks_like_reaction, parse_reaction, parse_species,
)


def test_element_table_has_118_symbols():
    assert len(ELEMENTS) == 118 and {"H", "O", "Fe", "Og"} <= ELEMENTS


@pytest.mark.parametrize("formula,counts", [
    ("H2O", {"H": 2, "O": 1}),
    ("Ca(OH)2", {"Ca": 1, "O": 2, "H": 2}),
    ("Fe2(SO4)3", {"Fe": 2, "S": 3, "O": 12}),
    ("CuSO4.5H2O", {"Cu": 1, "S": 1, "O": 9, "H": 10}),
    ("CuSO4·5H2O", {"Cu": 1, "S": 1, "O": 9, "H": 10}),
    ("H₂SO₄", {"H": 2, "S": 1, "O": 4}),
    ("CO", {"C": 1, "O": 1}),
    ("Co", {"Co": 1}),
    ("NaCl(aq)", {"Na": 1, "Cl": 1}),
    ("Al2(SO4)3", {"Al": 2, "S": 3, "O": 12}),
    ("K4[Fe(CN)6]".replace("[", "(").replace("]", ")"), {"K": 4, "Fe": 1, "C": 6, "N": 6}),
    ("Mg3(PO4)2", {"Mg": 3, "P": 2, "O": 8}),
    ("(NH4)2SO4", {"N": 2, "H": 8, "S": 1, "O": 4}),
])
def test_parse_species(formula, counts):
    assert parse_species(formula) == counts


@pytest.mark.parametrize("bad", [
    "", "Xx2", "Hx", "h2o", "Na+", "Fe^3+", "SO4^2-", "H2O)", "(H2O", "H2O2(", "__import__('os')", "open('x')", "H" * 61,
    "((((H))))2", "H100", "C0", "5H2O", "H2O..", ".H2O", "H-2", "H2 O3 $", "\n", "1", "()",
])
def test_parse_species_rejects_garbage(bad):
    with pytest.raises(ChemError):
        parse_species(bad)


def test_nesting_limit():
    assert parse_species("(((H)2)2)2") == {"H": 8}
    with pytest.raises(ChemError):
        parse_species("((((H)2)2)2)2")


def test_parse_reaction_shapes():
    left, right = parse_reaction("2 H₂ + O₂ → 2 H₂O")
    assert left == [(2, "H2"), (1, "O2")] and right == [(2, "H2O")]
    for arrow in ("->", "→", "=", "=>", "-->", "⟶"):
        parse_reaction(f"H2 + Cl2 {arrow} 2HCl")


@pytest.mark.parametrize("bad", ["H2 + O2", "H2 -> O2 -> H2O", "H2 <=> 2H", "H2 ⇌ 2H", "Fe3+ + e- -> Fe2+",
                                 "H2 + -> H2O", "A + B + C + D + E + F + G + H + I -> J", "x" * 300])
def test_parse_reaction_rejects(bad):
    with pytest.raises(ChemError):
        parse_reaction(bad)


@pytest.mark.parametrize("text,status", [
    ("H2 + O2 -> H2O", "unbalanced"),
    ("2H2 + O2 -> 2H2O", "balanced"),
    ("Fe + O2 -> Fe2O3", "unbalanced"),
    ("4Fe + 3O2 -> 2Fe2O3", "balanced"),
    ("Ca(OH)2 + HCl -> CaCl2 + H2O", "unbalanced"),
    ("Ca(OH)2 + 2HCl -> CaCl2 + 2H2O", "balanced"),
    ("CuSO4.5H2O -> CuSO4 + 5H2O", "balanced"),
    ("CuSO4.5H2O -> CuSO4 + 4H2O", "unbalanced"),
    ("2H₂ + O₂ → 2H₂O", "balanced"),
    ("CH4 + 2O2 → CO2 + 2H2O", "balanced"),
    ("C3H8 + 5O2 -> 3CO2 + 4H2O(g)", "balanced"),
    ("H2 <=> 2H", "unsupported"),
    ("Xx2 -> Xx", "unsupported"),
])
def test_is_balanced(text, status):
    assert is_balanced(text).status == status


def test_unbalanced_note_names_the_element_and_does_not_give_coefficients():
    r = is_balanced("H2 + O2 -> H2O")
    assert r.note == "not balanced: O differs 2 vs 1" and r.counts == {"H": (2, 2), "O": (2, 1)}


def test_unbalanced_note_lists_at_most_three_elements():
    r = is_balanced("C2H5OH + O2 -> CO2 + H2O")
    assert r.note.count("differs") <= 3


@pytest.mark.parametrize("text,coeffs", [
    ("H2 + O2 -> H2O", [2, 1, 2]),
    ("Fe + O2 -> Fe2O3", [4, 3, 2]),
    ("Ca(OH)2 + HCl -> CaCl2 + H2O", [1, 2, 1, 2]),
    ("C3H8 + O2 -> CO2 + H2O", [1, 5, 3, 4]),
    ("Al + O2 -> Al2O3", [4, 3, 2]),
    ("KMnO4 + HCl -> KCl + MnCl2 + H2O + Cl2", [2, 16, 2, 2, 8, 5]),
    ("N2 + H2 -> NH3", [1, 3, 2]),
    ("Fe2(SO4)3 + NaOH -> Fe(OH)3 + Na2SO4", [1, 6, 2, 3]),
    ("C6H12O6 + O2 -> CO2 + H2O", [1, 6, 6, 6]),
    ("Na + Cl2 -> NaCl", [2, 1, 2]),
])
def test_balance_returns_the_smallest_positive_integers(text, coeffs):
    assert balance(text) == coeffs
    left, right = parse_reaction(text)
    balanced = " + ".join(f"{c}{s}" for c, s in zip(coeffs, [s for _, s in left])) + " -> " + \
        " + ".join(f"{c}{s}" for c, s in zip(coeffs[len(left):], [s for _, s in right]))
    assert is_balanced(balanced).status == "balanced"


def test_balance_ignores_given_coefficients_and_rejects_impossible_or_ambiguous_cases():
    assert balance("7H2 + 9O2 -> 3H2O") == [2, 1, 2]
    assert balance("H2 -> O2") is None            # elements do not match
    assert balance("H2O -> H2O") is None or balance("H2O -> H2O") == [1, 1]
    assert balance("H2 + O2 -> H2O + H2O2") is None  # two independent solutions
    assert balance("not a reaction") is None


def test_check_reaction_leaves_algebra_alone():
    for text in ("x = 5", "n = m/M", "F = ma", "2x + 3 = 11", "x**2+5*x+6=0", "C = 5", "H = 2", "", "   "):
        assert check_reaction(text) is None
    assert check_reaction("2H2 + O2 = 2H2O").status == "balanced"
    assert check_reaction("H2 + O2 -> ").status == "unsupported"  # an arrow means it was meant as a reaction
    assert looks_like_reaction("a -> b") and not looks_like_reaction("a = b")


def test_hostile_strings_never_execute_and_are_unsupported():
    for text in ("__import__('os').system('x') -> y", "H2 + O2 -> " + "H" * 500, "(" * 100 + "H" + ")" * 100 + " -> H",
                 "H2O\x00 -> H2O", "H2 + O2 -> H2O; rm -rf /"):
        assert is_balanced(text).status == "unsupported"
    assert check_reaction(None) is None
    assert is_balanced(12345).status == "unsupported"
