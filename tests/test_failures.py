from rd.failures import label

LOOP = "<think>\n" + "Wait, let me check the value of x again from the start.\n" * 40
OK = "<think>\nx = 2 so 2x = 4.\n</think>\n\nThe answer is $\\boxed{4}$."


def test_categories():
    assert label(OK, True, "stop")["category"] == "correct"
    assert label(OK, False, "stop")["category"] == "wrong_answer"
    assert label(LOOP, False, "length")["category"] == "loop"
    assert label("<think>\n" + "step %d of a long derivation\n" * 1, False, "length")["category"] == "too_long"


def test_format():
    assert label("<think>\nstill thinking", False, "stop")["format_issue"] == "unclosed_think"
    assert label("<think>\nok\n</think>\nanswer is 4", False, "stop")["format_issue"] == "no_boxed"
    assert label("\\boxed{4}", False, "stop")["format_issue"] == ""  # E1 style: no think, boxed is fine
    assert label(LOOP, False, "length")["wrong_format"] is False  # truncated: format not judged


def test_repetition():
    assert label(LOOP, False, "length")["repetition"]
    assert label("<think>\n" + "0" * 9000, False, "length")["repetition"]  # newline-free loop via the tail check
    assert not label(OK, True, "stop")["repetition"]
