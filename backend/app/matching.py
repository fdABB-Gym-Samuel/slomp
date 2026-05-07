import re

_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_FEAT = re.compile(r"\s*[-–—]?\s*\b(feat|featuring|ft|with)\b\.?\s.*$", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """Lowercase, strip parenthetical / bracket sections, strip 'feat.'-style
    artist credits, and collapse to alnum-only tokens."""
    s = title.lower()
    s = _PARENS.sub(" ", s)
    s = _FEAT.sub("", s)
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


def normalize_artist(artist: str) -> str:
    """Looser than title: just lowercase + strip non-alnum. Artists rarely
    have parenthetical content but often have punctuation/diacritics."""
    s = artist.lower()
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


def title_matches_guess(title: str, guess: str) -> bool:
    return normalize_title(title) == normalize_title(guess)


def is_correct_guess(
    round_title: str,
    round_artist: str,
    guess_title: str,
    guess_artist: str,
) -> bool:
    """Both title and artist must match (after normalization). This blocks
    same-titled-different-songs (e.g. 'Hello' by Adele vs Lionel Richie)
    while still allowing different versions/remixes of the same song."""
    if normalize_title(round_title) != normalize_title(guess_title):
        return False
    return normalize_artist(round_artist) == normalize_artist(guess_artist)


def is_hint_fulfilled(
    field: str,
    round_artist: str,
    round_album: str | None,
    guess_artist: str,
    guess_album: str | None,
) -> bool:
    """Whether a wrong guess matches the configured hint field. `field` is
    one of {"none", "artist", "album"}. Same normalization as `is_correct_guess`
    so capitalisation/punctuation differences don't matter."""
    if field == "artist":
        if not (round_artist and guess_artist):
            return False
        return normalize_artist(round_artist) == normalize_artist(guess_artist)
    if field == "album":
        if not (round_album and guess_album):
            return False
        return normalize_artist(round_album) == normalize_artist(guess_album)
    return False
