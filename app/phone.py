import re


def normalize(raw: str) -> str:
    """Telefon -> doar cifre, cu prefix de țară (implicit Moldova 373)."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("00") and len(d) > 10:  # prefix internațional 00373..., nu un număr local
        d = d[2:]
    if len(d) == 9 and d.startswith("0"):
        d = "373" + d[1:]
    elif len(d) == 8:
        d = "373" + d
    return d
