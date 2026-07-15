import re

def to_subscript(formula: str) -> str:
    """Return *formula* with any digits rendered as Unicode subscripts.

    Examples
    --------
    >>> to_subscript("Al2O3")
    'Al₂O₃'
    >>> to_subscript("Cr2O3")
    'Cr₂O₃'
    """

    return re.sub(r'(\d+)', r'$_\1$', formula)