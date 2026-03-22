"""
Gate selector: given a target net flow (Q_net_target = Q_in - Q_out),
find up to 5 gate configurations that achieve it while minimizing
the number of gates changed.

Algorithm:
  For k = 0, 1, 2, 3 gates changed:
    Enumerate all C(n_gates, k) combinations of gates to adjust.
    For each combination, use scipy.optimize to find new openings that:
      1. Hit Q_net_target (primary objective, heavily weighted)
      2. Minimize total opening change (secondary objective)
    Collect feasible solutions, sort by (num_changes, deviation).
    Stop once we have 5 candidates.
"""

import itertools
import numpy as np
from scipy.optimize import minimize

from .hydraulics import gate_flow

# Maximum gates changed before we stop searching
MAX_K = 3

# Tolerance: within this fraction of total gate capacity we call it "solved"
FLOW_TOLERANCE_FRACTION = 0.02


def _build_gate_list(input_gates: list, output_gates: list, dh_in: float, dh_out: float) -> list:
    """
    Flatten input+output gates into a unified list with metadata.
    sign = +1 for input gates (opening them increases Q_net)
    sign = -1 for output gates (opening them decreases Q_net)
    """
    gates = []
    for g in input_gates:
        gates.append({
            "id": g["id"],
            "name": g["name"],
            "cd": g["cd"],
            "area_m2": g["area_m2"],
            "opening": g["opening"],
            "type": "input",
            "dh": dh_in,
            "sign": +1,
        })
    for g in output_gates:
        gates.append({
            "id": g["id"],
            "name": g["name"],
            "cd": g["cd"],
            "area_m2": g["area_m2"],
            "opening": g["opening"],
            "type": "output",
            "dh": dh_out,
            "sign": -1,
        })
    return gates


def _compute_Q_net(openings: np.ndarray, gates: list) -> float:
    """Compute Q_net = Q_in - Q_out given a full array of openings."""
    Q_net = 0.0
    for i, g in enumerate(gates):
        Q = gate_flow(g["cd"], g["area_m2"], openings[i], g["dh"])
        Q_net += g["sign"] * Q
    return Q_net


def _max_Q_net_range(gates: list, current_openings: np.ndarray) -> float:
    """Estimate total flow capacity for setting tolerance."""
    total = 0.0
    for i, g in enumerate(gates):
        total += gate_flow(g["cd"], g["area_m2"], 1.0, g["dh"])
    return total


def find_top5_options(
    input_gates: list,
    output_gates: list,
    h: float,
    river_level: float,
    tailwater_level: float,
    Q_net_target: float,
) -> list:
    """
    Find up to 5 gate configurations achieving Q_net_target.

    input_gates / output_gates: list of gate dicts (from config) each with
        an 'opening' key reflecting current state.

    Returns list of dicts sorted by (num_changes, deviation):
        {
            'num_changes': int,
            'changes':     {gate_id: new_opening, ...},
            'Q_net':       float,
            'deviation':   float,
            'openings':    np.ndarray  (full opening array, internal use)
        }
    """
    dh_in = max(river_level - h, 0.0)
    dh_out = max(h - tailwater_level, 0.0)

    gates = _build_gate_list(input_gates, output_gates, dh_in, dh_out)
    n = len(gates)
    current_openings = np.array([g["opening"] for g in gates])

    Q_net_current = _compute_Q_net(current_openings, gates)
    capacity = _max_Q_net_range(gates, current_openings)
    tolerance = FLOW_TOLERANCE_FRACTION * max(capacity, 1.0)

    candidates = []

    # --- k = 0: no changes ---
    candidates.append({
        "num_changes": 0,
        "changes": {},
        "Q_net": Q_net_current,
        "deviation": abs(Q_net_target - Q_net_current),
        "openings": current_openings.copy(),
    })

    # --- k = 1, 2, 3 ---
    for k in range(1, min(MAX_K + 1, n + 1)):
        for combo in itertools.combinations(range(n), k):

            def objective(x, combo=combo):
                openings = current_openings.copy()
                for i, idx in enumerate(combo):
                    openings[idx] = x[i]
                Q_net = _compute_Q_net(openings, gates)
                flow_err = (Q_net - Q_net_target) ** 2
                # Secondary: minimize total absolute opening change
                movement = sum(abs(x[i] - current_openings[combo[i]]) for i in range(k))
                # Weight flow error very heavily so hitting target dominates
                return flow_err * 1e8 + movement

            x0 = current_openings[list(combo)]
            bounds = [(0.0, 1.0)] * k

            result = minimize(
                objective,
                x0,
                bounds=bounds,
                method="L-BFGS-B",
                options={"ftol": 1e-14, "gtol": 1e-10, "maxiter": 200},
            )

            new_openings = current_openings.copy()
            for i, idx in enumerate(combo):
                new_openings[idx] = float(np.clip(result.x[i], 0.0, 1.0))

            Q_net_achieved = _compute_Q_net(new_openings, gates)
            deviation = abs(Q_net_target - Q_net_achieved)

            # Build changes dict (only gates that actually moved)
            changes = {}
            for idx in combo:
                delta = abs(new_openings[idx] - current_openings[idx])
                if delta > 1e-4:
                    changes[gates[idx]["id"]] = round(float(new_openings[idx]), 4)

            if not changes:
                continue  # optimizer found no meaningful change needed

            candidates.append({
                "num_changes": len(changes),
                "changes": changes,
                "Q_net": Q_net_achieved,
                "deviation": deviation,
                "openings": new_openings,
            })

        # Re-sort and prune after each k level
        candidates.sort(key=lambda c: (c["num_changes"], c["deviation"]))
        candidates = candidates[:5]

        # If we already have 5 good solutions, stop early
        if len(candidates) >= 5 and candidates[-1]["deviation"] < tolerance:
            break

    return candidates[:5]
