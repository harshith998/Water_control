"""
ReservoirWidget — custom QPainter widget showing a top-down schematic:

  Input gates  │         RESERVOIR          │  Output gates
  (left edge)  │   (blue = water level)     │  (right edge)
               │                            │

Each gate is drawn as a vertical rectangle whose fill height reflects
its current opening percentage.  Input gates are blue, output gates green.
The reservoir background interpolates from light blue (low) to dark blue (high).
"""

from PyQt5.QtWidgets import QWidget, QToolTip
from PyQt5.QtCore import Qt, QRect, QPoint
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QBrush, QLinearGradient


# Colour palette
COL_BG           = QColor(30, 35, 45)
COL_RESERVOIR_BG = QColor(20, 30, 50)
COL_GATE_BG      = QColor(55, 60, 70)
COL_GATE_BORDER  = QColor(130, 140, 155)
COL_INPUT_FILL   = QColor(60, 140, 240)   # blue
COL_OUTPUT_FILL  = QColor(60, 210, 120)   # green
COL_TEXT         = QColor(210, 220, 230)
COL_SETPOINT     = QColor(255, 200, 50, 180)   # amber dashed line
COL_ALARM_LOW    = QColor(230, 80, 80, 180)
COL_ALARM_HIGH   = QColor(230, 80, 80, 180)

GATE_W = 22   # gate rectangle width (px)
GATE_GAP = 8  # gap between gates (px)
MARGIN_SIDE = 60   # horizontal margin for labels
MARGIN_TOP = 40
MARGIN_BOT = 30


class ReservoirWidget(QWidget):

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.cfg = config
        self.setMinimumSize(520, 350)

        # Current display state (updated by dashboard)
        self.h = config["reservoir"]["initial_level_m"]
        self.min_level = config["reservoir"]["min_level_m"]
        self.max_level = config["reservoir"]["max_level_m"]
        self.setpoint = config["reservoir"]["setpoint_m"]

        self.openings: dict = {}
        for g in config["input_gates"] + config["output_gates"]:
            self.openings[g["id"]] = g["initial_opening"]

        self.input_gates = config["input_gates"]
        self.output_gates = config["output_gates"]

        # For tooltip tracking
        self._gate_rects: dict = {}  # gate_id -> QRect
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # Public update method
    # ------------------------------------------------------------------

    def update_state(self, snapshot: dict):
        self.h = snapshot["h"]
        self.openings.update(snapshot["openings"])
        self.update()  # trigger repaint

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _reservoir_rect(self) -> QRect:
        w, h = self.width(), self.height()
        x = MARGIN_SIDE + GATE_W + 12
        y = MARGIN_TOP
        rw = w - 2 * (MARGIN_SIDE + GATE_W + 12)
        rh = h - MARGIN_TOP - MARGIN_BOT
        return QRect(x, y, rw, rh)

    def _gate_height(self, n: int, res_rect: QRect) -> int:
        available = res_rect.height() - 2 * GATE_GAP
        return max(20, min(80, (available - (n - 1) * GATE_GAP) // n))

    def _gate_y_positions(self, n: int, res_rect: QRect) -> list:
        gh = self._gate_height(n, res_rect)
        total_h = n * gh + (n - 1) * GATE_GAP
        start_y = res_rect.top() + (res_rect.height() - total_h) // 2
        return [start_y + i * (gh + GATE_GAP) for i in range(n)]

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        res = self._reservoir_rect()

        # Background
        painter.fillRect(0, 0, w, h, COL_BG)

        # ── Reservoir body (water level as blue intensity) ──────────────
        level_frac = (self.h - self.min_level) / max(self.max_level - self.min_level, 1e-6)
        level_frac = max(0.0, min(1.0, level_frac))

        # Light blue (low) → dark blue (high)
        r = int(100 - 80 * level_frac)
        g_c = int(160 - 100 * level_frac)
        b = int(220 - 50 * level_frac)
        water_color = QColor(r, g_c, b)

        painter.fillRect(res, COL_RESERVOIR_BG)
        # Only fill up to water level fraction (bottom-aligned)
        water_h = int(res.height() * level_frac)
        water_rect = QRect(res.left(), res.bottom() - water_h + 1, res.width(), water_h)
        painter.fillRect(water_rect, water_color)

        # Reservoir border
        painter.setPen(QPen(COL_GATE_BORDER, 2))
        painter.drawRect(res)

        # ── Setpoint line (amber dashed) ─────────────────────────────────
        sp_frac = (self.setpoint - self.min_level) / max(self.max_level - self.min_level, 1e-6)
        sp_frac = max(0.0, min(1.0, sp_frac))
        sp_y = res.bottom() - int(res.height() * sp_frac)

        pen = QPen(COL_SETPOINT, 1, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(res.left(), sp_y, res.right(), sp_y)
        painter.setPen(COL_SETPOINT)
        painter.setFont(QFont("Monospace", 8))
        painter.drawText(res.right() - 60, sp_y - 3, f"SP {self.setpoint:.1f}m")

        # ── Level text inside reservoir ───────────────────────────────────
        painter.setPen(QColor(255, 255, 255, 200))
        painter.setFont(QFont("Monospace", 11, QFont.Bold))
        painter.drawText(res.left() + 8, res.top() + 20, f"h = {self.h:.3f} m")

        # ── Input gates (left edge) ────────────────────────────────────
        n_in = len(self.input_gates)
        gh_in = self._gate_height(n_in, res)
        y_positions_in = self._gate_y_positions(n_in, res)
        gate_x_in = MARGIN_SIDE

        for i, gate in enumerate(self.input_gates):
            gid = gate["id"]
            opening = self.openings.get(gid, 0.0)
            gy = y_positions_in[i]
            rect = QRect(gate_x_in, gy, GATE_W, gh_in)
            self._gate_rects[gid] = rect
            self._draw_gate(painter, rect, opening, COL_INPUT_FILL, gid)

            # Arrow pointing right into reservoir
            painter.setPen(QPen(QColor(150, 180, 220), 1))
            mid_y = gy + gh_in // 2
            painter.drawLine(gate_x_in + GATE_W, mid_y, res.left(), mid_y)
            # Arrowhead
            painter.drawLine(res.left(), mid_y, res.left() - 6, mid_y - 4)
            painter.drawLine(res.left(), mid_y, res.left() - 6, mid_y + 4)

        # ── Output gates (right edge) ──────────────────────────────────
        n_out = len(self.output_gates)
        gh_out = self._gate_height(n_out, res)
        y_positions_out = self._gate_y_positions(n_out, res)
        gate_x_out = res.right() + 12

        for i, gate in enumerate(self.output_gates):
            gid = gate["id"]
            opening = self.openings.get(gid, 0.0)
            gy = y_positions_out[i]
            rect = QRect(gate_x_out, gy, GATE_W, gh_out)
            self._gate_rects[gid] = rect
            self._draw_gate(painter, rect, opening, COL_OUTPUT_FILL, gid)

            # Arrow pointing right out of reservoir
            painter.setPen(QPen(QColor(120, 200, 140), 1))
            mid_y = gy + gh_out // 2
            painter.drawLine(res.right(), mid_y, gate_x_out, mid_y)
            # Arrowhead
            painter.drawLine(gate_x_out + GATE_W, mid_y, gate_x_out + GATE_W + 6, mid_y - 4)
            painter.drawLine(gate_x_out + GATE_W, mid_y, gate_x_out + GATE_W + 6, mid_y + 4)

        # ── Labels ────────────────────────────────────────────────────
        painter.setFont(QFont("Monospace", 8))
        painter.setPen(QColor(160, 170, 190))
        painter.drawText(gate_x_in - 5, res.top() - 6, "INPUTS")
        painter.drawText(gate_x_out, res.top() - 6, "OUTPUTS")

        painter.end()

    def _draw_gate(self, painter: QPainter, rect: QRect, opening: float, fill_color: QColor, label: str):
        """Draw a single gate rectangle with fill proportional to opening."""
        # Background
        painter.fillRect(rect, COL_GATE_BG)

        # Fill (bottom up, like a rising water gate)
        fill_h = int(rect.height() * opening)
        if fill_h > 0:
            fill_rect = QRect(rect.left(), rect.bottom() - fill_h + 1, rect.width(), fill_h)
            painter.fillRect(fill_rect, fill_color)

        # Border
        painter.setPen(QPen(COL_GATE_BORDER, 1))
        painter.drawRect(rect)

        # Label below
        painter.setPen(COL_TEXT)
        painter.setFont(QFont("Monospace", 7))
        painter.drawText(rect.left() - 2, rect.bottom() + 14, label)

        # Opening % above
        pct = f"{int(opening * 100)}%"
        painter.setFont(QFont("Monospace", 7))
        painter.drawText(rect.left() - 2, rect.top() - 3, pct)

    # ------------------------------------------------------------------
    # Tooltip on hover
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event):
        pos = event.pos()
        for gid, rect in self._gate_rects.items():
            if rect.contains(pos):
                opening = self.openings.get(gid, 0.0)
                QToolTip.showText(
                    self.mapToGlobal(pos),
                    f"{gid}\nOpening: {opening*100:.1f}%",
                    self,
                )
                return
        QToolTip.hideText()
