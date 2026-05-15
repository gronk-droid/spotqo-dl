"""
Shared utility helpers used across multiple modules.
"""

import re


def titles_match(title1: str, title2: str) -> bool:
    """
    Check whether two track/album titles refer to the same thing.

    Comparison is case-insensitive, ignores punctuation, and treats hyphens /
    underscores as word separators so that "Pro-Active" and "Pro Active" match.
    One title being a substring of the other also counts as a match (handles
    edit-additions like "feat." suffixes).
    """
    if not title1 or not title2:
        return False

    def _norm(t: str) -> str:
        t = re.sub(r"[-_]", " ", t.lower().strip())
        t = re.sub(r"[^\w\s]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    n1, n2 = _norm(title1), _norm(title2)
    return n1 == n2 or n1 in n2 or n2 in n1
