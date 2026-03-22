"""
Hydraulic flow equations for gate control.
All flows in m³/s, areas in m², levels/heads in m.
"""

G = 9.81  # gravitational acceleration (m/s²)


def gate_flow(cd: float, area_m2: float, opening: float, delta_h: float) -> float:
    """
    Orifice equation: Q = C_d * A * p * sqrt(2 * g * dH)

    cd       : discharge coefficient (empirical, ~0.6–0.8)
    area_m2  : full gate cross-sectional area (m²)
    opening  : fractional opening [0.0, 1.0]
    delta_h  : hydraulic head difference (upstream - downstream, m)

    Returns flow rate Q (m³/s). Returns 0 if delta_h <= 0 or gate closed.
    """
    if delta_h <= 0.0 or opening <= 0.0:
        return 0.0
    return cd * area_m2 * opening * (2.0 * G * delta_h) ** 0.5


def opening_for_flow(Q_target: float, cd: float, area_m2: float, delta_h: float) -> float:
    """
    Inverse orifice equation — find the opening fraction that produces Q_target.

    Returns the required opening (may be outside [0, 1]; caller must clamp).
    Returns 0.0 if target is zero or head is non-positive.
    """
    if delta_h <= 0.0 or Q_target <= 0.0:
        return 0.0
    denom = cd * area_m2 * (2.0 * G * delta_h) ** 0.5
    if denom == 0.0:
        return float("inf")
    return Q_target / denom
