"""
Step-based simulation engine.

State:
    h         : current water level (m)
    t         : elapsed sim time (s)
    openings  : dict {gate_id: opening [0,1]} for all gates

The simulator knows nothing about control — it just applies physics.
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


SCENARIOS = {
    "Constant":  ConstantScenario,
    "Step Up":   StepUpScenario,
    "Step Down": StepDownScenario,
    "Sine Wave": SineScenario,
    "Ramp Up":   RampUpScenario,
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

        # Current openings (mutable)
        self.openings: dict = {}
        self.reset()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def reset(self):
        self.t = 0.0
        self.h = self.cfg["reservoir"]["initial_level_m"]
        self.openings = {}
        for g in self.input_gates + self.output_gates:
            self.openings[g["id"]] = g["initial_opening"]

        # History buffers
        self.history_t: list = [0.0]
        self.history_h: list = [self.h]
        self.history_Q_in: list = [0.0]
        self.history_Q_out: list = [0.0]
        self.history_river: list = [self.cfg["hydraulics"]["river_level_m"]]

    def apply_openings(self, changes: dict):
        """Apply a partial or full gate opening update."""
        for gate_id, opening in changes.items():
            if gate_id in self.openings:
                self.openings[gate_id] = float(np.clip(opening, 0.0, 1.0))

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

        # Keep history bounded (last 5 minutes of sim time)
        max_samples = int(300 / self.dt)
        if len(self.history_t) > max_samples:
            self.history_t = self.history_t[-max_samples:]
            self.history_h = self.history_h[-max_samples:]
            self.history_Q_in = self.history_Q_in[-max_samples:]
            self.history_Q_out = self.history_Q_out[-max_samples:]
            self.history_river = self.history_river[-max_samples:]

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
            "Q_in": self.Q_in_total(river_level),
            "Q_out": self.Q_out_total(),
        }

    # ------------------------------------------------------------------
    # Gate dicts with current openings (for gate_selector)
    # ------------------------------------------------------------------

    def input_gates_current(self) -> list:
        return [{**g, "opening": self.openings[g["id"]]} for g in self.input_gates]

    def output_gates_current(self) -> list:
        return [{**g, "opening": self.openings[g["id"]]} for g in self.output_gates]
