"""Waiver fixtures. One properly waived line, one with a bare marker and no reason -- which waives nothing
and is itself reported. Values are constructed and fake."""

# A genuine waiver: reason written down, on the line, where the next reader sees it.
sample_api_key = "Kq7ZmVt3LwXn8BdYcRf2GsHj9PtQ"  # secret-scan-ok: constructed fixture value, never a real credential

# A bare marker. This must NOT waive anything.
other_api_key = "Zv8Nq2Rf5Tj7Wm3Kx9Bd4Lp6Hc1Gy0Su"  # secret-scan-ok:
