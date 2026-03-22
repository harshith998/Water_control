"""
Main dashboard window.

Layout:
  ┌─────────────────────────────┬─────────────────────┐
  │   ReservoirWidget           │  Status panel        │
  │   (top-down schematic)      │  Top-5 options       │
  ├─────────────────────────────│  Simulation controls │
  │   Time-series chart         │                      │
  └─────────────────────────────┴─────────────────────┘
"""

import sys
import yaml
import numpy as np

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox,
    QGroupBox, QScrollArea, QFrame, QSplitter, QSlider,
    QCheckBox,
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont, QColor, QPalette

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from ui.reservoir_widget import ReservoirWidget
from simulation.simulator import Simulator, SCENARIOS
from core.estimator import FlowEstimator
from core.controller import PIController
from core.gate_selector import find_top5_options


# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------

DARK_BG  = "#1e2330"
PANEL_BG = "#252b3b"
ACCENT   = "#3a8fd4"
TEXT     = "#d2dce8"
GREEN    = "#3cd47a"
AMBER    = "#f0c040"
RED      = "#e05050"


def _label(text: str, bold=False, color=TEXT, size=9) -> QLabel:
    lbl = QLabel(text)
    font = QFont("Monospace", size)
    font.setBold(bold)
    lbl.setFont(font)
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    return lbl


def _btn(text: str, color=ACCENT) -> QPushButton:
    b = QPushButton(text)
    b.setFont(QFont("Monospace", 9))
    b.setStyleSheet(
        f"QPushButton {{ background: {color}; color: #fff; border-radius: 4px; padding: 4px 10px; }}"
        f"QPushButton:hover {{ background: #5ab0f0; }}"
        f"QPushButton:pressed {{ background: #1a6090; }}"
    )
    return b


# ---------------------------------------------------------------------------
# Matplotlib canvas for time series
# ---------------------------------------------------------------------------

class TimeSeriesCanvas(FigureCanvas):
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(6, 2.2), facecolor="#1e2330")
        super().__init__(self.fig)
        self.setParent(parent)

        self.ax = self.fig.add_subplot(111)
        self._style_axes()

        self.fig.tight_layout(pad=1.2)

    def _style_axes(self):
        ax = self.ax
        ax.set_facecolor("#161c28")
        ax.tick_params(colors="#8090a8", labelsize=8)
        ax.spines["bottom"].set_color("#3a4560")
        ax.spines["left"].set_color("#3a4560")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_xlabel("Sim time (s)", color="#8090a8", fontsize=8)
        ax.set_ylabel("Level (m)", color="#8090a8", fontsize=8)
        ax.grid(color="#2a3450", linewidth=0.5, linestyle="--")

    def update_plot(self, times, levels, setpoint, min_l, max_l, river_levels=None):
        self.ax.cla()
        self._style_axes()

        t = np.array(times)
        h = np.array(levels)

        self.ax.plot(t, h, color="#5ab0f0", linewidth=1.4, label="Water level")

        if river_levels is not None:
            self.ax.plot(t, river_levels, color="#80d8a0", linewidth=0.8,
                         linestyle=":", label="River level", alpha=0.7)

        self.ax.axhline(setpoint, color="#f0c040", linewidth=0.9, linestyle="--", label="Setpoint")
        self.ax.axhline(min_l, color="#e05050", linewidth=0.6, linestyle=":")
        self.ax.axhline(max_l, color="#e05050", linewidth=0.6, linestyle=":")

        if len(t) > 0:
            self.ax.set_xlim(max(0, t[-1] - 300), t[-1] + 5)

        y_margin = (max_l - min_l) * 0.1
        self.ax.set_ylim(min_l - y_margin, max_l + y_margin)
        self.ax.legend(loc="upper left", fontsize=7, framealpha=0.3, labelcolor="white")

        self.fig.tight_layout(pad=1.2)
        self.draw()


# ---------------------------------------------------------------------------
# Option card for top-5 list
# ---------------------------------------------------------------------------

class OptionCard(QFrame):
    def __init__(self, rank: int, option: dict, apply_callback, parent=None):
        super().__init__(parent)
        self.option = option
        self.setFrameStyle(QFrame.Box)
        self.setStyleSheet(
            f"QFrame {{ background: #2a3248; border: 1px solid #3a4560; border-radius: 5px; }}"
            f"QFrame:hover {{ border: 1px solid {ACCENT}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # Header: rank + num gates changed
        num_changes = option["num_changes"]
        gate_word = "gates" if num_changes != 1 else "gate"
        dev = option["deviation"]

        header_color = GREEN if num_changes == 0 else (AMBER if num_changes == 1 else TEXT)
        header = _label(
            f"#{rank}  {num_changes} {gate_word} changed  (Δ={dev:.3f} m³/s)",
            bold=True, color=header_color, size=8
        )
        layout.addWidget(header)

        # Gate changes
        if option["changes"]:
            for gid, new_opening in option["changes"].items():
                layout.addWidget(_label(f"  {gid}: → {new_opening*100:.1f}%", size=8))
        else:
            layout.addWidget(_label("  No changes needed", color=GREEN, size=8))

        # Q_net achieved
        layout.addWidget(_label(f"  Q_net = {option['Q_net']:.3f} m³/s", color="#8090a8", size=7))

        # Apply button
        if option["changes"]:
            apply_btn = _btn("Apply", color="#2a6090")
            apply_btn.setFixedHeight(22)
            apply_btn.clicked.connect(lambda: apply_callback(option["changes"]))
            layout.addWidget(apply_btn)


# ---------------------------------------------------------------------------
# Main Dashboard
# ---------------------------------------------------------------------------

class Dashboard(QMainWindow):

    def __init__(self, config: dict):
        super().__init__()
        self.cfg = config
        self.setWindowTitle("Water Gate Control System")
        self.setMinimumSize(1100, 680)
        self._apply_dark_theme()

        # --- Core objects ---
        self.sim = Simulator(config)

        ctrl_cfg = config["control"]
        res_cfg  = config["reservoir"]

        self.estimator = FlowEstimator(
            window=ctrl_cfg["estimator_window"],
            surface_area_m2=res_cfg["surface_area_m2"],
        )
        self.controller = PIController(
            kp=ctrl_cfg["kp"],
            ki=ctrl_cfg["ki"],
            setpoint=res_cfg["setpoint_m"],
            surface_area=res_cfg["surface_area_m2"],
        )

        self.control_dt = ctrl_cfg["control_dt"]
        self._time_since_control = 0.0

        # Scenario
        self.scenario = list(SCENARIOS.values())[0](config["hydraulics"]["river_level_m"])

        # Auto-control flag
        self.auto_control = True

        # Top-5 options cache
        self._options: list = []

        # Running flag
        self._running = False

        # UI
        self._build_ui()

        # Timers
        sim_interval_ms = max(16, int(1000 * config["simulation"]["dt"]
                                      / config["simulation"]["speed_multiplier"]))
        self._sim_timer = QTimer(self)
        self._sim_timer.setInterval(sim_interval_ms)
        self._sim_timer.timeout.connect(self._tick)

        # Initial render
        self._update_ui(self.sim.snapshot(self.scenario.river_level(0.0)))
        self._refresh_options()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Left column (reservoir + chart) ──────────────────────────
        left_col = QVBoxLayout()
        left_col.setSpacing(6)

        self.reservoir_widget = ReservoirWidget(self.cfg)
        self.reservoir_widget.setMinimumHeight(320)
        left_col.addWidget(self.reservoir_widget, stretch=3)

        self.chart = TimeSeriesCanvas()
        self.chart.setMinimumHeight(180)
        left_col.addWidget(self.chart, stretch=2)

        left_widget = QWidget()
        left_widget.setLayout(left_col)

        # ── Right column (status + options + sim controls) ────────────
        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_col.setContentsMargins(0, 0, 0, 0)

        right_col.addWidget(self._build_status_panel())
        right_col.addWidget(self._build_options_panel(), stretch=1)
        right_col.addWidget(self._build_sim_panel())

        right_widget = QWidget()
        right_widget.setLayout(right_col)
        right_widget.setFixedWidth(300)
        right_widget.setStyleSheet(f"background: {PANEL_BG};")

        root.addWidget(left_widget, stretch=1)
        root.addWidget(right_widget)

    def _build_status_panel(self) -> QGroupBox:
        box = QGroupBox("System Status")
        box.setStyleSheet(self._group_style())
        layout = QVBoxLayout(box)
        layout.setSpacing(4)

        self.lbl_level    = _label("Level: --", bold=True, size=10)
        self.lbl_setpoint = _label("Setpoint: --", size=9)
        self.lbl_Q_in     = _label("Q_in (est): --", size=9)
        self.lbl_Q_out    = _label("Q_out: --", size=9)
        self.lbl_Q_net    = _label("Q_net: --", size=9)
        self.lbl_dhdt     = _label("dh/dt: --", size=9)
        self.lbl_river    = _label("River lvl: --", size=9)
        self.lbl_time     = _label("Sim time: 0 s", size=8, color="#8090a8")

        for w in [self.lbl_level, self.lbl_setpoint, self.lbl_Q_in,
                  self.lbl_Q_out, self.lbl_Q_net, self.lbl_dhdt,
                  self.lbl_river, self.lbl_time]:
            layout.addWidget(w)

        return box

    def _build_options_panel(self) -> QGroupBox:
        box = QGroupBox("Top-5 Gate Options")
        box.setStyleSheet(self._group_style())
        outer = QVBoxLayout(box)
        outer.setSpacing(4)
        outer.setContentsMargins(4, 4, 4, 4)

        # Auto-control toggle
        row = QHBoxLayout()
        self.chk_auto = QCheckBox("Auto-apply best option")
        self.chk_auto.setChecked(True)
        self.chk_auto.setStyleSheet(f"color: {TEXT}; font-family: Monospace; font-size: 8pt;")
        self.chk_auto.stateChanged.connect(self._on_auto_toggle)
        row.addWidget(self.chk_auto)

        refresh_btn = _btn("↻ Refresh", color="#2a5060")
        refresh_btn.setFixedHeight(24)
        refresh_btn.clicked.connect(self._refresh_options)
        row.addWidget(refresh_btn)
        outer.addLayout(row)

        # Scrollable option cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._options_container = QWidget()
        self._options_container.setStyleSheet("background: transparent;")
        self._options_layout = QVBoxLayout(self._options_container)
        self._options_layout.setSpacing(4)
        self._options_layout.addStretch()

        scroll.setWidget(self._options_container)
        outer.addWidget(scroll)

        return box

    def _build_sim_panel(self) -> QGroupBox:
        box = QGroupBox("Simulation")
        box.setStyleSheet(self._group_style())
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        # Scenario selector
        row1 = QHBoxLayout()
        row1.addWidget(_label("Scenario:", size=8))
        self.combo_scenario = QComboBox()
        self.combo_scenario.addItems(list(SCENARIOS.keys()))
        self.combo_scenario.setStyleSheet(
            f"QComboBox {{ background: #2a3248; color: {TEXT}; border: 1px solid #3a4560;"
            f" border-radius: 3px; font-family: Monospace; font-size: 8pt; }}"
        )
        self.combo_scenario.currentTextChanged.connect(self._on_scenario_change)
        row1.addWidget(self.combo_scenario)
        layout.addLayout(row1)

        # River level manual override
        row2 = QHBoxLayout()
        row2.addWidget(_label("River lvl (m):", size=8))
        self.spin_river = QDoubleSpinBox()
        self.spin_river.setRange(0.0, 20.0)
        self.spin_river.setSingleStep(0.1)
        self.spin_river.setValue(self.cfg["hydraulics"]["river_level_m"])
        self.spin_river.setStyleSheet(
            f"QDoubleSpinBox {{ background: #2a3248; color: {TEXT}; border: 1px solid #3a4560;"
            f" border-radius: 3px; font-family: Monospace; font-size: 8pt; }}"
        )
        self.spin_river.valueChanged.connect(self._on_river_level_change)
        row2.addWidget(self.spin_river)
        layout.addLayout(row2)

        # Speed
        row3 = QHBoxLayout()
        row3.addWidget(_label("Speed:", size=8))
        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setRange(1, 100)
        self.slider_speed.setValue(self.cfg["simulation"]["speed_multiplier"])
        self.slider_speed.setStyleSheet("QSlider::handle:horizontal { background: #3a8fd4; }")
        self.lbl_speed = _label(f"{self.cfg['simulation']['speed_multiplier']}×", size=8)
        self.slider_speed.valueChanged.connect(self._on_speed_change)
        row3.addWidget(self.slider_speed)
        row3.addWidget(self.lbl_speed)
        layout.addLayout(row3)

        # Start / Stop / Reset
        btn_row = QHBoxLayout()
        self.btn_start = _btn("▶ Start", color="#2a7040")
        self.btn_stop  = _btn("■ Stop",  color="#703030")
        self.btn_reset = _btn("↺ Reset", color="#404060")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_reset)
        layout.addLayout(btn_row)

        return box

    # ------------------------------------------------------------------
    # Simulation tick
    # ------------------------------------------------------------------

    def _tick(self):
        river_level = self.scenario.river_level(self.sim.t)

        # Override river level if scenario is Constant (use spin box)
        if self.combo_scenario.currentText() == "Constant":
            river_level = self.spin_river.value()

        # Step simulation
        snap = self.sim.step(river_level)

        # Estimator update
        dhdt, Q_in_est = self.estimator.update(snap["t"], snap["h"], snap["Q_out"])

        # Control decision every control_dt seconds
        self._time_since_control += self.sim.dt
        if self._time_since_control >= self.control_dt:
            self._time_since_control = 0.0

            Q_net_target = self.controller.compute(snap["h"], self.control_dt)

            # Compute options
            self._options = find_top5_options(
                self.sim.input_gates_current(),
                self.sim.output_gates_current(),
                snap["h"],
                river_level,
                snap["tailwater"],
                Q_net_target,
            )

            if self.auto_control and self._options:
                # Apply best option (fewest changes, smallest deviation)
                best = self._options[0]
                if best["changes"]:
                    self.sim.apply_openings(best["changes"])

            # Refresh options panel
            self._rebuild_option_cards()

        self._update_ui(snap, dhdt=dhdt, Q_in_est=Q_in_est)

    # ------------------------------------------------------------------
    # UI updates
    # ------------------------------------------------------------------

    def _update_ui(self, snap: dict, dhdt: float = 0.0, Q_in_est: float = 0.0):
        h = snap["h"]
        sp = snap["setpoint"]

        # Colour the level label
        if h > snap["max_level"] * 0.95 or h < snap["min_level"] * 1.05:
            lvl_color = RED
        elif abs(h - sp) > 0.3:
            lvl_color = AMBER
        else:
            lvl_color = GREEN

        self.lbl_level.setText(f"Level: {h:.3f} m")
        self.lbl_level.setStyleSheet(f"color: {lvl_color}; background: transparent;")
        self.lbl_setpoint.setText(f"Setpoint: {sp:.1f} m")
        self.lbl_Q_in.setText(f"Q_in (est): {Q_in_est:.3f} m³/s")
        self.lbl_Q_out.setText(f"Q_out: {snap['Q_out']:.3f} m³/s")
        q_net = snap["Q_in"] - snap["Q_out"]
        net_color = AMBER if abs(q_net) > 1.0 else GREEN
        self.lbl_Q_net.setText(f"Q_net: {q_net:+.3f} m³/s")
        self.lbl_Q_net.setStyleSheet(f"color: {net_color}; background: transparent;")
        dhdt_color = AMBER if abs(dhdt) > 0.0001 else GREEN
        self.lbl_dhdt.setText(f"dh/dt: {dhdt*1000:+.4f} mm/s")
        self.lbl_dhdt.setStyleSheet(f"color: {dhdt_color}; background: transparent;")
        self.lbl_river.setText(f"River lvl: {snap['river_level']:.2f} m")
        self.lbl_time.setText(f"Sim time: {snap['t']:.0f} s")

        # Reservoir widget
        self.reservoir_widget.update_state(snap)

        # Time series (every 5 ticks to reduce load)
        if int(snap["t"] / self.sim.dt) % 5 == 0:
            self.chart.update_plot(
                self.sim.history_t,
                self.sim.history_h,
                sp,
                snap["min_level"],
                snap["max_level"],
                river_levels=self.sim.history_river,
            )

    def _refresh_options(self):
        river_level = self.scenario.river_level(self.sim.t)
        if self.combo_scenario.currentText() == "Constant":
            river_level = self.spin_river.value()

        snap = self.sim.snapshot(river_level)
        Q_net_target = self.controller.compute(snap["h"], self.control_dt)
        # Reset integral so we don't double-count
        self.controller._integral -= Q_net_target / max(self.controller.ki * self.control_dt, 1e-9)

        self._options = find_top5_options(
            self.sim.input_gates_current(),
            self.sim.output_gates_current(),
            snap["h"],
            river_level,
            snap["tailwater"],
            Q_net_target,
        )
        self._rebuild_option_cards()

    def _rebuild_option_cards(self):
        # Clear existing cards (leave stretch at end)
        layout = self._options_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for rank, opt in enumerate(self._options, start=1):
            card = OptionCard(rank, opt, self._apply_option)
            layout.insertWidget(rank - 1, card)

    def _apply_option(self, changes: dict):
        self.sim.apply_openings(changes)
        snap = self.sim.snapshot(self.scenario.river_level(self.sim.t))
        self._update_ui(snap)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def _start(self):
        if not self._running:
            self._running = True
            self._sim_timer.start()

    def _stop(self):
        self._running = False
        self._sim_timer.stop()

    def _reset(self):
        self._stop()
        self.sim.reset()
        self.estimator.reset()
        self.controller.reset()
        self._time_since_control = 0.0
        snap = self.sim.snapshot(self.scenario.river_level(0.0))
        self._update_ui(snap)
        self._refresh_options()

    def _on_scenario_change(self, name: str):
        base = self.cfg["hydraulics"]["river_level_m"]
        self.scenario = SCENARIOS[name](base)
        self.spin_river.setEnabled(name == "Constant")

    def _on_river_level_change(self, val: float):
        pass  # spin box value is read directly in _tick

    def _on_speed_change(self, val: int):
        self.lbl_speed.setText(f"{val}×")
        interval_ms = max(16, int(1000 * self.sim.dt / val))
        self._sim_timer.setInterval(interval_ms)

    def _on_auto_toggle(self, state):
        self.auto_control = bool(state)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_dark_theme(self):
        self.setStyleSheet(f"""
            QMainWindow {{ background: {DARK_BG}; }}
            QWidget {{ background: {DARK_BG}; color: {TEXT}; }}
            QGroupBox {{ color: {TEXT}; border: 1px solid #3a4560;
                         border-radius: 5px; margin-top: 10px;
                         font-family: Monospace; font-size: 9pt; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px;
                                padding: 0 4px; color: {ACCENT}; }}
            QScrollBar:vertical {{ background: #1e2330; width: 8px; }}
            QScrollBar::handle:vertical {{ background: #3a4560; border-radius: 4px; }}
            QToolTip {{ background: #2a3248; color: {TEXT}; border: 1px solid {ACCENT}; }}
        """)

    def _group_style(self) -> str:
        return (
            f"QGroupBox {{ background: {PANEL_BG}; border: 1px solid #3a4560;"
            f" border-radius: 5px; margin-top: 10px; }}"
            f"QGroupBox::title {{ color: {ACCENT}; }}"
        )
