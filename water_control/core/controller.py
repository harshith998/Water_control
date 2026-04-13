"""
Feedforward + PI controller for water level.

Control law:
    error        = h - h_setpoint
    Q_correction = -(Kp * error + Ki * integral(error))   [m³/s]
    Q_net_target = Q_correction

The gate selector will find gate configurations that achieve this Q_net_target
where Q_net = Q_in - Q_out.

A positive Q_net raises the level; negative lowers it.
When h > setpoint, Q_correction is negative (reduce inflow or increase outflow).
"""


class PIController:
    def __init__(self, kp: float, ki: float, setpoint: float, surface_area: float):
        """
        kp           : proportional gain (m³/s per m error)
        ki           : integral gain     (m³/s per m·s accumulated error)
        setpoint     : target water level (m)
        surface_area : reservoir surface area (m²) — used to scale if needed
        """
        self.kp = kp
        self.ki = ki
        self.setpoint = setpoint
        self.surface_area = surface_area

        self._integral: float = 0.0

    # Physical limit: clamp Q_net_target to this range (m³/s).
    # Prevents integral windup from driving the controller beyond what gates can achieve.
    Q_LIMIT = 30.0

    def compute(self, h: float, dt: float, update_integral: bool = True) -> float:
        """
        Compute Q_net_target (m³/s) given current level h and timestep dt.

        update_integral : set False when auto-control is off so the integral
                          does not drift while gates are not being commanded.

        Positive result → need more inflow than outflow (level too low).
        Negative result → need more outflow than inflow (level too high).
        """
        error = h - self.setpoint  # positive when level is too high

        if update_integral:
            self._integral += error * dt
            # Anti-windup: clamp integral so its contribution never exceeds Q_LIMIT
            max_i = self.Q_LIMIT / max(self.ki, 1e-9)
            if self._integral > max_i:
                self._integral = max_i
            elif self._integral < -max_i:
                self._integral = -max_i

        Q_net_target = -(self.kp * error + self.ki * self._integral)

        # Hard clamp output to physical range
        if Q_net_target > self.Q_LIMIT:
            Q_net_target = self.Q_LIMIT
        elif Q_net_target < -self.Q_LIMIT:
            Q_net_target = -self.Q_LIMIT

        return Q_net_target

    def reset(self):
        self._integral = 0.0
