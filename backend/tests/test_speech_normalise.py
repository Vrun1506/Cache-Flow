import pytest

from tutor.speech.normalise import phrase_key, split_sentences, to_speech


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("x^2", "x squared"),
        ("x**2", "x squared"),
        ("x^3", "x cubed"),
        ("x^n", "x to the power n"),
        ("y^10", "y to the power 10"),
        ("x^-1", "x to the power minus 1"),
        ("x²", "x squared"),
        ("(x+1)^2 = 9", "the quantity x plus 1 squared equals 9"),
        ("3*4", "3 times 4"),
        ("3×4", "3 times 4"),
        ("3.5*2", "3.5 times 2"),
        ("1/2", "1 over 2"),
        ("x/y", "x over y"),
        ("x = 5", "x equals 5"),
        ("x = -2 or x = -3", "x equals minus 2 or x equals minus 3"),
        ("-5", "minus 5"),
        ("x-3", "x minus 3"),
        ("2 + 3 - 1", "2 plus 3 minus 1"),
        ("x − 3", "x minus 3"),
        ("sqrt(16)", "the square root of 16"),
        ("sqrt(x+1)", "the square root of x plus 1"),
        ("√9", "the square root of 9"),
        ("pi*r^2", "pi times r squared"),
        ("π", "pi"),
        ("x <= 3", "x is less than or equal to 3"),
        ("x ≥ 3", "x is greater than or equal to 3"),
        ("x < 3", "x is less than 3"),
        ("x > 3", "x is greater than 3"),
        ("a != b", "a is not equal to b"),
        ("a ≠ b", "a is not equal to b"),
        ("a -> b", "a gives b"),
        ("50%", "50 percent"),
        ("30°", "30 degrees"),
        ("v = 5 m/s", "v equals 5 metres per second"),
        ("a = 9.8 m/s^2", "a equals 9.8 metres per second squared"),
        ("speed 60 km/h", "speed 60 kilometres per hour"),
        ("well-known result", "well-known result"),
        ("x ≈ 3.14", "x is approximately 3.14"),
        ("The answer is x = 2. Done.", "The answer is x equals 2. Done."),
        ("", ""),
        ("Plain words only.", "Plain words only."),
    ],
)
def test_to_speech(text, spoken):
    assert to_speech(text) == spoken


def test_output_has_no_leftover_operator_symbols():
    out = to_speech("(a+b)^2 = a^2 + 2*a*b + b^2")
    assert not any(ch in out for ch in "^*+=")


def test_split_sentences_basic():
    assert split_sentences("One. Two! Three?", 6) == ["One.", "Two!", "Three?"]


def test_split_sentences_merges_short_neighbours():
    assert split_sentences("One. Two. Three.", 200) == ["One. Two. Three."]


def test_split_sentences_never_exceeds_limit():
    text = "word " * 200
    chunks = split_sentences(text, 80)
    assert len(chunks) > 1 and all(len(c) <= 80 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_split_sentences_cuts_a_long_unbroken_token():
    chunks = split_sentences("a" * 450, 200)
    assert [len(c) for c in chunks] == [200, 200, 50]


def test_split_sentences_keeps_decimals_together():
    assert split_sentences("It is 3.5 metres long.", 200) == ["It is 3.5 metres long."]


def test_split_sentences_empty():
    assert split_sentences("  ", 200) == []


def test_phrase_key_is_stable_and_normalised():
    assert phrase_key("Hello  world") == phrase_key("hello world")
    assert phrase_key("a") != phrase_key("b")
    assert phrase_key("a", "v1") != phrase_key("a", "v2")
    assert len(phrase_key("a")) == 24
