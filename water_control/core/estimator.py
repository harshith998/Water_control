"""
Estimates the unknown river input rate (Q_in) by back-calculating from
the measured water level change and known output flow.

Conservation of mass:
    dh/dt = (Q_in - Q_out) / A_surface
    => Q_in = dh/dt * A_surface + Q_out
"""

from collections import deque


class FlowEstimator:
    def __init__(self, window: int, surface_area_m2: float):
        """
        window          : number of samples to use for smoothing dh/dt
        surface_area_m2 : horizontal reservoir surface area (m²)
        """
        self.window = window
        self.surface_area = surface_area_m2

        self._levels: deque = deque(maxlen=window)
        self._times: deque = deque(maxlen=window)

        self.dhdt: float = 0.0       # m/s — last smoothed rate of change
        self.Q_in_est: float = 0.0   # m³/s — estimated inflow

    def update(self, t: float, h: float, Q_out: float) -> tuple:
        """
        Feed a new (time, level, outflow) sample.

        Returns (dhdt, Q_in_est).
        """
        self._levels.append(h)
        self._times.append(t)

        if len(self._levels) >= 2:
            dh = self._levels[-1] - self._levels[0]
            dt = self._times[-1] - self._times[0]
            self.dhdt = dh / dt if dt > 0.0 else 0.0
        else:
            self.dhdt = 0.0

        self.Q_in_est = self.dhdt * self.surface_area + Q_out
        return self.dhdt, self.Q_in_est

    def reset(self):
        self._levels.clear()
        self._times.clear()
        self.dhdt = 0.0
        self.Q_in_est = 0.0
