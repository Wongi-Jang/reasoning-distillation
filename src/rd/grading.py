"""Answer extraction + correctness. Used to (a) verify teacher traces and (b) score every eval."""
import re
from math_verify import parse, verify


def extract_boxed(text: str) -> str | None:
    """Content of the LAST \\boxed{...} / \\fbox{...}, nested braces handled. None if absent or unbalanced."""
    idx = max(text.rfind("\\boxed"), text.rfind("\\fbox"))
    if idx < 0:
        return None
    i = text.find("{", idx)
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 : j]
    return None  # unbalanced -> generation was cut off mid-answer

def extract_answer(response: str) -> str | None:
    """Final answer from a model completion: last \\boxed{} after </think>, else the last number."""
    if "</think>" in response:
        response = response.split("</think>")[-1]
    elif "<think>" in response:
        return None  # thinking never finished (hit max_tokens)
    boxed = extract_boxed(response)
    if boxed is not None:
        return boxed
    nums = re.findall(r"-?\d[\d,]*\.?\d*", response)
    return nums[-1].replace(",", "").rstrip(".") if nums else None

def is_correct(pred: str | None, gold: str) -> bool:
    """Symbolic equivalence via math-verify (sympy). Gold must be the first argument to verify()."""
    if pred is None:
        return False
    return verify(parse("\\boxed{" + gold + "}"), parse("\\boxed{" + pred + "}"))