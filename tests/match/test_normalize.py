from bbb_scraper.match.normalize import (
    letter_grade_to_num,
    name_similarity,
    name_tokens,
    phone_key,
    zip5,
)


def test_phone_key_normalizes_formats():
    assert phone_key("(305) 642-4409") == "3056424409"
    assert phone_key("+1 305-642-4409") == "3056424409"
    assert phone_key("305.642.4409") == "3056424409"
    assert phone_key("13056424409") == "3056424409"
    assert phone_key("642-4409") is None  # not 10 digits
    assert phone_key(None) is None


def test_zip5():
    assert zip5("33125") == "33125"
    assert zip5("33125-1318") == "33125"
    assert zip5("FL 33125") == "33125"
    assert zip5(None) is None


def test_name_tokens_drops_legal_and_filler():
    assert name_tokens("Machado Auto Sales, LLC") == frozenset({"machado", "auto", "sales"})
    assert name_tokens("The Walt Grace, Inc.") == frozenset({"walt", "grace"})
    assert "and" not in name_tokens("Barnes & Dennig and Company")


def test_name_similarity_is_order_and_suffix_insensitive():
    assert name_similarity("Miami Auto Mall Inc", "Miami Automall") > 0.8
    assert name_similarity("Coggin Honda", "Coggin Honda Jacksonville") > 0.75
    assert name_similarity("Machado Auto Sales, LLC", "Machado Auto Sales") == 1.0
    assert name_similarity("Green Light Auto Sales", "Bird Road Auto Sales") < 0.6
    assert name_similarity("Toyota of Miami", None) == 0.0


def test_letter_grade_to_num():
    assert letter_grade_to_num("A+") == 4.33
    assert letter_grade_to_num("B-") == 2.67
    assert letter_grade_to_num("F") == 0.0
    assert letter_grade_to_num("NR") is None
    assert letter_grade_to_num(None) is None
