"""
Step-based simulation engine.

State:
    h                : current water level (m)
    t                : elapsed sim time (s)
    openings         : dict {gate_id: actual opening [0,1]} — moves at rate limit
    target_openings  : dict {gate_id: commanded opening [0,1]} — set instantly by controller
    rate_limits      : dict {gate_id: max change per second [0,1]/s}

Gates do not change instantly. Each gate has a travel_time_s (seconds for 0→100%).
Every physics step the actual opening moves toward the target at the rate limit.
The dashboard feeds it gate commands and Q_in scenarios.
"""

import numpy as np
from core.hydraulics import gate_flow


# ---------------------------------------------------------------------------
# Q_in scenarios  (river level as a function of sim time)
# These represent the *river level* which, combined with reservoir level,
# drives input gate flow via the orifice equation.
# ---------------------------------------------------------------------------

class RiverScenario:
    """Base class for river level scenarios."""
    def __init__(self, base_level: float):
        self.base_level = base_level

    def river_level(self, t: float) -> float:
        raise NotImplementedError


class ConstantScenario(RiverScenario):
    """Flat river level — balanced steady state."""
    def river_level(self, t: float) -> float:
        return self.base_level


class StepUpScenario(RiverScenario):
    """Sudden river level rise at t=30s (flood surge)."""
    def __init__(self, base_level: float, delta: float = 1.5, t_step: float = 30.0):
        super().__init__(base_level)
        self.delta = delta
        self.t_step = t_step

    def river_level(self, t: float) -> float:
        return self.base_level + (self.delta if t >= self.t_step else 0.0)


class StepDownScenario(RiverScenario):
    """Sudden river level drop at t=30s (drought)."""
    def __init__(self, base_level: float, delta: float = 1.5, t_step: float = 30.0):
        super().__init__(base_level)
        self.delta = delta
        self.t_step = t_step

    def river_level(self, t: float) -> float:
        return self.base_level - (self.delta if t >= self.t_step else 0.0)


class SineScenario(RiverScenario):
    """Oscillating river level (tidal / seasonal variation)."""
    def __init__(self, base_level: float, amplitude: float = 1.0, period: float = 120.0):
        super().__init__(base_level)
        self.amplitude = amplitude
        self.period = period

    def river_level(self, t: float) -> float:
        return self.base_level + self.amplitude * np.sin(2 * np.pi * t / self.period)


class RampUpScenario(RiverScenario):
    """Gradual river level rise."""
    def __init__(self, base_level: float, rate: float = 0.01, max_delta: float = 2.0):
        super().__init__(base_level)
        self.rate = rate       # m/s
        self.max_delta = max_delta

    def river_level(self, t: float) -> float:
        return self.base_level + min(self.rate * t, self.max_delta)


class RandomScenario(RiverScenario):
    """
    Pseudo-random river level — sum of incommensurable sinusoids.
    Looks random but is fully deterministic (no state required).
    """
    _COMPONENTS = [
        (0.60,  47.3,  0.00),   # (amplitude_m, period_s, phase_offset)
        (0.40,  83.7,  1.23),
        (0.30, 127.1,  2.57),
        (0.20,  31.4,  0.87),
        (0.15, 211.3,  4.10),
        (0.10,  19.7,  3.33),
    ]

    def river_level(self, t: float) -> float:
        level = self.base_level
        for amp, period, phase in self._COMPONENTS:
            level += amp * np.sin(2 * np.pi * t / period + phase)
        # Clamp to ±2 m of baseline
        return float(np.clip(level, self.base_level - 2.0, self.base_level + 2.0))


SCENARIOS = {
    "Constant":  ConstantScenario,
    "Step Up":   StepUpScenario,
    "Step Down": StepDownScenario,
    "Sine Wave": SineScenario,
    "Ramp Up":   RampUpScenario,
    "Random":    RandomScenario,
}


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class Simulator:
    def __init__(self, config: dict):
        self.cfg = config
        res = config["reservoir"]
        hyd = config["hydraulics"]

        self.surface_area = res["surface_area_m2"]
        self.min_level = res["min_level_m"]
        self.max_level = res["max_level_m"]
        self.setpoint = res["setpoint_m"]
        self.tailwater = hyd["tailwater_level_m"]
        self.g = hyd["gravity"]

        self.dt = config["simulation"]["dt"]

        # Gate definitions (list of dicts)
        self.input_gates = [dict(g) for g in config["input_gates"]]
        self.output_gates = [dict(g) for g in config["output_gates"]]

        # Rate limits: fraction per second (derived from travel_time_s)
        # travel_time_s is time to go 0.0 → 1.0, so rate = 1.0 / travel_time_s
        self.rate_limits: dict = {}
        for g in self.input_gates + self.output_gates:
            travel = g.get("travel_time_s", 60.0)
            self.rate_limits[g["id"]] = 1.0 / travel

        # Actual openings (move gradually toward target) and commanded targets
        self.openings: dict = {}
        self.target_openings: dict = {}
        self.reset()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def reset(self):
        self.t = 0.0
        self.h = self.cfg["reservoir"]["initial_level_m"]
        self.openings = {}
        self.target_openings = {}
        for g in self.input_gates + self.output_gates:
            self.openings[g["id"]] = g["initial_opening"]
            self.target_openings[g["id"]] = g["initial_opening"]

        # History buffers
        self.history_t: list = [0.0]
        self.history_h: list = [self.h]
        self.history_Q_in: list = [0.0]
        self.history_Q_out: list = [0.0]
        self.history_river: list = [self.cfg["hydraulics"]["river_level_m"]]
        self.history_error: list = [0.0]   # |h - setpoint| at each step

    def apply_openings(self, changes: dict):
        """Set target openings. Actual openings move toward these at the gate's rate limit."""
        for gate_id, opening in changes.items():
            if gate_id in self.target_openings:
                self.target_openings[gate_id] = float(np.clip(opening, 0.0, 1.0))

    def _advance_gate_positions(self):
        """Move each gate's actual opening one dt step toward its target."""
        for gate_id in self.openings:
            target = self.target_openings[gate_id]
            current = self.openings[gate_id]
            max_step = self.rate_limits[gate_id] * self.dt
            diff = target - current
            if abs(diff) <= max_step:
                self.openings[gate_id] = target
            else:
                self.openings[gate_id] = current + max_step * np.sign(diff)

    # ------------------------------------------------------------------
    # Per-gate flows (snapshot)
    # ------------------------------------------------------------------

    def gate_flows(self, river_level: float) -> dict:
        """Return {gate_id: Q (m³/s)} for all gates at current state."""
        flows = {}
        dh_in = max(river_level - self.h, 0.0)
        dh_out = max(self.h - self.tailwater, 0.0)

        for g in self.input_gates:
            flows[g["id"]] = gate_flow(g["cd"], g["area_m2"], self.openings[g["id"]], dh_in)
        for g in self.output_gates:
            flows[g["id"]] = gate_flow(g["cd"], g["area_m2"], self.openings[g["id"]], dh_out)
        return flows

    def Q_in_total(self, river_level: float) -> float:
        dh_in = max(river_level - self.h, 0.0)
        return sum(
            gate_flow(g["cd"], g["area_m2"], self.openings[g["id"]], dh_in)
            for g in self.input_gates
        )

    def Q_out_total(self) -> float:
        dh_out = max(self.h - self.tailwater, 0.0)
        return sum(
            gate_flow(g["cd"], g["area_m2"], self.openings[g["id"]], dh_out)
            for g in self.output_gates
        )

    # ------------------------------------------------------------------
    # Physics step
    # ------------------------------------------------------------------

    def step(self, river_level: float) -> dict:
        """
        Advance simulation by one dt step.
        Returns a snapshot dict of current state.
        """
        self._advance_gate_positions()
        Q_in = self.Q_in_total(river_level)
        Q_out = self.Q_out_total()

        dh_dt = (Q_in - Q_out) / self.surface_area
        self.h = float(np.clip(self.h + dh_dt * self.dt, self.min_level, self.max_level))
        self.t += self.dt

        # Record history
        self.history_t.append(self.t)
        self.history_h.append(self.h)
        self.history_Q_in.append(Q_in)
        self.history_Q_out.append(Q_out)
        self.history_river.append(river_level)
        self.history_error.append(abs(self.h - self.setpoint))

        # Keep history bounded (last 5 minutes of sim time)
        max_samples = int(300 / self.dt)
        if len(self.history_t) > max_samples:
            self.history_t = self.history_t[-max_samples:]
            self.history_h = self.history_h[-max_samples:]
            self.history_Q_in = self.history_Q_in[-max_samples:]
            self.history_Q_out = self.history_Q_out[-max_samples:]
            self.history_river = self.history_river[-max_samples:]
            self.history_error = self.history_error[-max_samples:]

        return self.snapshot(river_level)

    def snapshot(self, river_level: float) -> dict:
        """Current state as a plain dict for the UI."""
        return {
            "t": self.t,
            "h": self.h,
            "setpoint": self.setpoint,
            "min_level": self.min_level,
            "max_level": self.max_level,
            "river_level": river_level,
            "tailwater": self.tailwater,
            "openings": dict(self.openings),
            "target_openings": dict(self.target_openings),
            "Q_in": self.Q_in_total(river_level),
            "Q_out": self.Q_out_total(),
        }

    # ------------------------------------------------------------------
    # Gate dicts with current openings (for gate_selector)
    # ------------------------------------------------------------------

    def input_gates_current(self) -> list:
        """Actual physical positions (mid-travel). Use for flow calculations."""
        return [{**g, "opening": self.openings[g["id"]]} for g in self.input_gates]

    def output_gates_current(self) -> list:
        return [{**g, "opening": self.openings[g["id"]]} for g in self.output_gates]

    def input_gates_target(self) -> list:
        """Commanded target positions. Use for optimization so we plan from where gates are heading."""
        return [{**g, "opening": self.target_openings[g["id"]]} for g in self.input_gates]

    def output_gates_target(self) -> list:
        return [{**g, "opening": self.target_openings[g["id"]]} for g in self.output_gates]
