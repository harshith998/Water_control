# Water Dam Gate Control System — Architecture & Usage Guide

---

## FOR AKIRA — What to Measure at the Facility

This is the complete list of real-world values you need to collect on-site. Everything here maps directly into `config.yaml` and the simulation/control system.

---

### 1. Gates — Count & Identity

- How many **input gates** are there? (gates that let river water INTO the reservoir)
- How many **output gates** are there? (gates that release water FROM the reservoir downstream)
- Give each gate a label (e.g. IG1, IG2 / OG1, OG2) so we can track them individually.

---

### 2. Gate Size — Area (m²)

For each gate, measure:

- **Width** (m) × **Height** (m) = gate area in m²
- This is the area when the gate is **fully open** (100% opening)
- Every gate can have a different size — measure each one separately

> This goes into `area_m2` in config.yaml for each gate.

---

### 3. Discharge Coefficient — Cd (experimentally measured)

Cd accounts for the fact that real water flow is never perfectly efficient (turbulence, friction, gate geometry). It is always between 0 and 1, typically around 0.6–0.7.

**How to find it:**

1. Set a gate to a known fixed opening (e.g. 50%)
2. Measure the actual flow rate through it — Q_measured (m³/s)
   *(use a flow meter, bucket + stopwatch, or ultrasonic sensor)*
3. Measure the head difference across the gate — ΔH (m)
   *(water level upstream minus water level downstream)*
4. Back-calculate Cd from the orifice equation:

```
Cd = Q_measured / ( A × opening × √(2 × 9.81 × ΔH) )
```

Repeat this for a few different openings and average the results. Each gate may have a slightly different Cd due to shape and wear.

> This goes into `cd` in config.yaml for each gate.

---

### 4. Water Levels — Fixed Reference Points (m)

You need three elevation measurements, all on the **same datum** (e.g. sea level or a local reference point):

| Value | What to measure | config.yaml key |
|-------|----------------|-----------------|
| River/upstream level | Water surface height on the inlet side of input gates | `river_level_m` |
| Tailwater/downstream level | Water surface height on the outlet side of output gates | `tailwater_level_m` |
| Reservoir surface area | Horizontal area of the reservoir (m²) — from engineering drawings or satellite measurement | `surface_area_m2` |

Also define your operational limits:

| Value | What it means | config.yaml key |
|-------|--------------|-----------------|
| Target level | The water level you want to maintain | `setpoint_m` |
| Low alarm level | Minimum safe level before intervention | `min_level_m` |
| High alarm level | Maximum safe level before flood risk | `max_level_m` |

---

### 5. Gate Actuation Speed — How Fast Can a Gate Move?

**This is critical.** A gate does not instantly jump from 30% to 80% open — it moves gradually driven by a motor or hydraulic actuator. If the controller ignores this, it will issue commands that the physical gate cannot execute in time.

For each gate (or gate type, if they all use the same actuator), measure:

- **Full travel time** — how many seconds does it take to go from fully closed (0%) to fully open (100%)?
- From this, derive the **rate limit** in units of **% opening per second**:

```
rate_limit (%/s) = 100% / full_travel_time_seconds

Example: gate takes 40 seconds to go 0→100%
→ rate_limit = 100 / 40 = 2.5 %/s
```

- Also note if open and close speeds differ (some actuators are faster in one direction)

> **Impact on the control system:** The controller must never command a gate to change faster than this rate. We will add a **rate limiter** constraint per gate so commanded openings are clamped to physically achievable movements each timestep. This means the simulation's `control_dt` and `dt` parameters need to be set relative to actual gate speeds.

---

### Summary Checklist

| # | What to measure | Unit | Notes |
|---|----------------|------|-------|
| 1 | Number of input gates | count | Label each one |
| 2 | Number of output gates | count | Label each one |
| 3 | Width × height of each gate | m² | Fully-open area |
| 4 | Discharge coefficient Cd for each gate | dimensionless (0–1) | Measure experimentally |
| 5 | Upstream river water level | m | On a consistent datum |
| 6 | Downstream tailwater level | m | On same datum |
| 7 | Reservoir horizontal surface area | m² | From drawings or survey |
| 8 | Target operating water level (setpoint) | m | Operational requirement |
| 9 | Low and high alarm thresholds | m | Safety limits |
| 10 | Gate full-travel time (0% → 100%) | seconds | Per gate or actuator type |
| 11 | Gate close speed if different from open | seconds | Some actuators differ |

---

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
  - [Layer Diagram](#layer-diagram)
  - [UI Layer](#ui-layer)
  - [Core Control Layer](#core-control-layer)
  - [Simulation Layer](#simulation-layer)
  - [Data Flow](#data-flow)
- [Key Physics & Algorithms](#key-physics--algorithms)
- [Configuration](#configuration)
- [How to Run](#how-to-run)
- [UI Walkthrough](#ui-walkthrough)
- [Simulation Scenarios](#simulation-scenarios)

---

## Overview

This is a **real-time water reservoir gate control simulation** built as a desktop application. It models a dam with multiple input and output gates, uses a PI feedback controller to maintain a target water level, and presents an interactive visualization with suggested gate configurations.

**Technology stack:** Python 3 · PyQt5 · Matplotlib · NumPy · SciPy · PyYAML

---

## Project Structure

```
water_control/
├── main.py                   # Entry point — loads config, starts Qt application
├── config.yaml               # All physical parameters, gains, initial conditions
├── requirements.txt          # Python dependencies
│
├── core/                     # Control logic (no UI dependencies)
│   ├── controller.py         # PI feedback controller
│   ├── estimator.py          # Real-time inflow estimator (mass balance)
│   ├── gate_selector.py      # Optimization: finds top-5 gate configurations
│   └── hydraulics.py         # Orifice-flow hydraulic equations
│
├── simulation/               # Physics simulation engine
│   └── simulator.py          # Euler integration + river-level scenarios
│
└── ui/                       # Desktop GUI (PyQt5)
    ├── dashboard.py           # Main window — orchestrates all components
    └── reservoir_widget.py    # Custom 2D schematic painter widget
```

---

## Architecture

### Layer Diagram

```
┌─────────────────────────────────────────────────────────┐
│                        UI Layer                          │
│   Dashboard (main window)                                │
│   ├── ReservoirWidget  (custom QPainter schematic)       │
│   ├── TimeSeriesCanvas (matplotlib time-series chart)    │
│   ├── Status Panel     (real-time metrics readout)       │
│   ├── Top-5 Options    (suggested gate configurations)   │
│   └── Controls         (play/stop/reset/speed/scenario)  │
└────────────────────┬────────────────────────────────────┘
                     │  QTimer tick every 16 ms
┌────────────────────▼────────────────────────────────────┐
│               Core Control Layer                         │
│   PIController   → computes target net flow              │
│   FlowEstimator  → estimates inflow from mass balance    │
│   GateSelector   → scipy optimization → top-5 options   │
└────────────────────┬────────────────────────────────────┘
                     │  gate openings / sensor readings
┌────────────────────▼────────────────────────────────────┐
│               Simulation Layer                           │
│   Simulator  → Euler integration of reservoir state      │
│   Scenarios  → river-level time profiles                 │
│   Hydraulics → orifice flow equations                    │
└─────────────────────────────────────────────────────────┘
```

### UI Layer

| Component | File | Description |
|-----------|------|-------------|
| `Dashboard` | [ui/dashboard.py](ui/dashboard.py) | Main `QMainWindow`. Owns the timer, wires all components together, handles button signals. |
| `ReservoirWidget` | [ui/reservoir_widget.py](ui/reservoir_widget.py) | Custom `QWidget` that uses `QPainter` to draw a top-down schematic of the reservoir, input gates (blue), output gates (green), setpoint line (amber), and alarm thresholds (red). |
| `TimeSeriesCanvas` | [ui/dashboard.py](ui/dashboard.py) | Embedded `matplotlib` figure showing a rolling 5-minute history of water level, setpoint, alarm lines, and river level. |
| Status Panel | [ui/dashboard.py](ui/dashboard.py) | Live readout: water level, setpoint, estimated inflow, outflow, level derivative, controller output. |
| Top-5 Options | [ui/dashboard.py](ui/dashboard.py) | Displays the five best gate configurations returned by the optimizer. Each can be applied manually or set to auto-apply. |

### Core Control Layer

| Module | Class / Function | Description |
|--------|-----------------|-------------|
| [core/controller.py](core/controller.py) | `PIController` | Discrete PI controller. Receives level error, returns a target net-flow correction. Integrator anti-windup is applied at configurable limits. |
| [core/estimator.py](core/estimator.py) | `FlowEstimator` | Sliding-window smoothing of `dh/dt`. Uses the mass-balance equation to back-calculate the unknown river inflow `Q_in`. |
| [core/gate_selector.py](core/gate_selector.py) | `find_top5_options()` | Iterates subsets of gates, calls `scipy.optimize.minimize` for each subset to find opening values that hit the target net flow. Returns the 5 options with fewest gate movements and smallest deviation. |
| [core/hydraulics.py](core/hydraulics.py) | `orifice_flow()` | Implements the standard orifice equation. Used by the optimizer and the simulator. |

### Simulation Layer

| Module | Class | Description |
|--------|-------|-------------|
| [simulation/simulator.py](simulation/simulator.py) | `Simulator` | Advances reservoir state by `dt` seconds using forward Euler integration. Computes all gate flows each step. Maintains a history buffer for the time-series chart. |
| [simulation/simulator.py](simulation/simulator.py) | Scenario classes | `ConstantScenario`, `StepUpScenario`, `StepDownScenario`, `SineScenario`, `RampUpScenario` — generate the upstream river level as a function of simulation time. |

### Data Flow

```
User selects scenario + presses Play
          │
          ▼
Scenario.river_level(t)  ──────────────────────────────────┐
          │                                                 │
          ▼                                                 │
Simulator._step()                                           │
  • computes Q_in for each input gate (orifice eq.)         │
  • computes Q_out for each output gate (orifice eq.)       │
  • integrates: h += (Q_in_total - Q_out_total) / A * dt   │
  • appends snapshot to history buffer                      │
          │                                                 │
          ▼                                                 │
FlowEstimator.update(h)                                     │
  • smooths dh/dt over window                              │
  • estimates Q_in_unknown = dh/dt * A + Q_out             │
          │                                                 │
          ▼ (every control_dt seconds)                      │
PIController.update(h, setpoint)                            │
  • error = h - setpoint                                   │
  • Q_net_target = -(Kp*error + Ki*∫error dt)              │
          │                                                 │
          ▼                                                 │
find_top5_options(Q_net_target, gates, h)                   │
  • scipy.optimize per gate subset                          │
  • returns [(openings, deviation, n_changes), ...]         │
          │                                                 │
          ▼                                                 │
Dashboard._tick()                                           │
  • auto-applies best option (if enabled)                  │
  • updates ReservoirWidget paint                           │
  • updates TimeSeriesCanvas                                │
  • updates status panel labels                             │
  • updates Top-5 options panel                             │
          │                                                 │
          └─────────── new gate openings ──────────────────┘
                        fed back into simulator
```

---

## Key Physics & Algorithms

### Orifice Flow Equation

All gate flow rates are computed with the standard orifice formula:

```
Q = C_d × A × opening × √(2 × g × ΔH)
```

| Symbol | Meaning |
|--------|---------|
| `C_d` | Discharge coefficient (≈ 0.65), note to akira this will have to be experimentally found basically how much does the actual water flow cuz its never perfect flow.|
| `A` | Maximum gate area (m²) again you have to measure each gate |
| `opening` | Gate opening fraction [0, 1] this is what gets controlled|
| `g` | Gravitational acceleration (9.81 m/s²) |
| `ΔH` | Head difference across gate (m), basically overall water level |

### Reservoir Mass Balance (Euler Integration)

```
dh/dt = (Q_in_total − Q_out_total) / A_surface
h(t+dt) = h(t) + dh/dt × dt
```

### PI Controller

```
error(t)   = h(t) − h_setpoint
Q_target   = −( Kp × error(t) + Ki × Σ error(t) × dt )
```

Negative sign: if level is above setpoint, we want net outflow to increase (Q_target < 0 means "remove more water").

### Gate Optimization (scipy)

For each subset of 0–3 gates to adjust:

- **Objective:** minimize `(Q_net(openings) − Q_target)²`
- **Bounds:** opening ∈ [0, 1] per gate
- **Ranking:** sort candidates by `(number of gate changes, |deviation from target|)`
- **Output:** top-5 options presented to the operator

### Inflow Estimation

```
Q_in_estimated = (dh/dt × A_surface) + Q_out_total
```

Allows the controller to infer the unmeasured river inflow from observed level dynamics.

---

## Configuration

All parameters live in [config.yaml](config.yaml). Edit this file to change physical properties or tune the controller without touching code.

```yaml
reservoir:
  surface_area_m2: 50000      # Reservoir horizontal area
  setpoint_m: 5.0             # Target water level
  initial_level_m: 5.0        # Starting level
  min_level_m: 2.0            # Low alarm threshold
  max_level_m: 8.0            # High alarm threshold

hydraulics:
  gravity: 9.81
  river_level_m: 6.5          # Upstream head (baseline)
  tailwater_level_m: 0.5      # Downstream head

input_gates:                  # Gates feeding water INTO the reservoir
  - id: IG1
    area_m2: 2.0
    cd: 0.65
    initial_opening: 0.30
  - id: IG2
    area_m2: 3.5
    cd: 0.65
    initial_opening: 0.30
  - id: IG3
    area_m2: 1.5
    cd: 0.65
    initial_opening: 0.20

output_gates:                 # Gates releasing water FROM the reservoir
  - id: OG1
    area_m2: 1.5
    cd: 0.65
    initial_opening: 0.40
  - id: OG2
    area_m2: 4.0
    cd: 0.65
    initial_opening: 0.30
  - id: OG3
    area_m2: 2.5
    cd: 0.65
    initial_opening: 0.35

control:
  kp: 0.0002                  # Proportional gain
  ki: 0.00002                 # Integral gain
  estimator_window: 20        # Samples for dh/dt smoothing
  control_dt: 2.0             # Seconds between control decisions

simulation:
  dt: 0.5                     # Physics timestep (seconds)
  speed_multiplier: 20        # Sim-seconds per real second
```

---

## How to Run

### Prerequisites

- Python 3.9 or later
- (Recommended) a virtual environment

### 1. Create and activate a virtual environment

```bash
cd /path/to/water_control

python3 -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` installs:

| Package | Purpose |
|---------|---------|
| `numpy >= 1.24` | Array math, numerical integration |
| `scipy >= 1.11` | Gate configuration optimization |
| `PyQt5 >= 5.15` | Desktop GUI framework |
| `matplotlib >= 3.7` | Time-series chart |
| `pyyaml >= 6.0` | Config file parsing |

### 3. Run the application

```bash
# Default config
python main.py

# Custom config file
python main.py /path/to/my_config.yaml
```

The main window opens immediately. The simulation starts paused — press **Play** to begin.

---

## UI Walkthrough

```
┌─────────────────────────────────────────────────────────────┐
│  Water Dam Gate Control System                               │
├──────────────────────────┬──────────────────────────────────┤
│                          │  Status Panel                    │
│   Reservoir Schematic    │  ─────────────────────────────   │
│   (ReservoirWidget)      │  Level:         5.23 m           │
│                          │  Setpoint:      5.00 m           │
│   [IG1][IG2][IG3] →□← [OG1][OG2][OG3]   │  Inflow est.:   12.4 m³/s        │
│          water level     │  Outflow:       14.1 m³/s        │
│   ───── setpoint ─────   │  dh/dt:        -0.003 m/s        │
│                          │                                  │
│                          │  Top-5 Gate Options              │
│                          │  ─────────────────────────────   │
│                          │  1. IG2=0.28 OG1=0.45  ✓ apply  │
│                          │  2. IG1=0.25 OG3=0.38    apply  │
│                          │  3. OG2=0.32             apply  │
│                          │  4. IG3=0.22 OG2=0.30    apply  │
│                          │  5. IG1=0.20 OG1=0.50    apply  │
├──────────────────────────┴──────────────────────────────────┤
│   Time-Series Chart (matplotlib)                            │
│   ─── level   ─ ─ setpoint   ··· alarms   ── river level   │
├─────────────────────────────────────────────────────────────┤
│  [ ▶ Play ] [ ■ Stop ] [ ↺ Reset ]   Speed: ──●── 20×      │
│  Scenario: [ Constant ▼ ]   [ ☑ Auto-apply best option ]   │
└─────────────────────────────────────────────────────────────┘
```

| Control | Description |
|---------|-------------|
| **Play / Stop** | Start or pause the simulation timer |
| **Reset** | Reset simulation state to initial conditions |
| **Speed slider** | Adjusts simulation speed from 1× to 100× real-time |
| **Scenario selector** | Selects the upstream river-level profile |
| **Auto-apply** | Automatically applies the #1 gate option each control cycle |
| **Apply buttons** | Manually apply any of the top-5 suggested configurations |

**Reservoir schematic colors:**

| Color | Meaning |
|-------|---------|
| Blue fill (reservoir) | Current water level (height proportional to fill) |
| Blue fill (input gates) | Gate opening percentage |
| Green fill (output gates) | Gate opening percentage |
| Amber dashed line | Setpoint |
| Red dashed lines | High / Low alarm thresholds |

---

## Simulation Scenarios

| Scenario | River Level Behavior |
|----------|---------------------|
| **Constant** | Fixed at `river_level_m` from config |
| **Step Up** | Rises by 1 m after 60 simulation-seconds |
| **Step Down** | Drops by 1 m after 60 simulation-seconds |
| **Sine Wave** | Oscillates ±0.5 m around baseline with ~120 s period |
| **Ramp Up** | Linearly increases from baseline over 300 simulation-seconds |

These scenarios stress-test the PI controller's ability to maintain the setpoint under varying upstream conditions.
