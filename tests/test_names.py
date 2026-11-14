import pytest

from intro_paths.names import from_sec_name, is_same_person, match_names


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("John Smith", "John Smith", 1.0),
        ("John A. Smith", "John Smith", 1.0),
        ("Smith, John", "John Smith", 1.0),
        ("J. Smith", "John Smith", 0.85),
        ("Иван Петров", "иван петров", 1.0),
        ("Алёна Иванова", "Алена Иванова", 1.0),
        ("John Smith", "Jane Smith", 0.0),
        ("John Smith", "John Smyth", 0.0),
        ("Smith", "John Smith", 0.0),
        ("", "John Smith", 0.0),
    ],
)
def test_match_names(a, b, expected):
    assert match_names(a, b) == expected


def test_apostrophe_names():
    assert from_sec_name("O'Brien Deirdre") == "Deirdre Obrien"
    assert is_same_person("Deirdre O'Brien", from_sec_name("O'BRIEN DEIRDRE"))


def test_from_sec_name_reverses_last_first():
    assert from_sec_name("COOK TIMOTHY D") == "Timothy D Cook"
    assert is_same_person("Tim Cook", from_sec_name("COOK TIMOTHY D")) is False
    assert is_same_person("Timothy Cook", from_sec_name("COOK TIMOTHY D")) is True
