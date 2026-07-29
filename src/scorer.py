"""Transport-event scorer for fused atmospheric observations.

This is an illustrative reimplementation. The structure mirrors the scorer used
in 3DREAMS@SG, where a single scoring function is shared by the live pipeline,
the replay engine, and the dashboard, so that all three agree by construction.
Every coefficient here is a placeholder chosen to make the synthetic example
readable. None of them are operational values.

Standard library only, on purpose: a reviewer can clone the repo and run the
example without installing anything.

The scorer is a pure function of a window. No I/O, no clock, no globals. That
is what makes the same code safe to run over live data and over replayed
history, and what makes the tests meaningful.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Level:
    """One range gate of a Doppler-LiDAR profile."""

    height_m: float
    backscatter: float  # arbitrary units, higher means more aerosol
    wind_dir_deg: float  # meteorological convention, direction wind comes from
    wind_speed_ms: float


@dataclass(frozen=True)
class Window:
    """A two-hour fused observation window at one site.

    Fuses the four sources: LiDAR profile, surface air quality, satellite fire
    detections, and the cloud mask (represented here by the profile itself,
    which the classifier reads).
    """

    site: str
    start_hour_sgt: float  # local hour, 0 to 24
    profile: tuple[Level, ...]
    pm25_ugm3: float
    upwind_fire_count: int
    upwind_fire_bearing_deg: float
    boundary_layer_height_m: float


class CloudLabel(str, Enum):
    CLEAR = "clear"
    THIN = "thin"
    OPAQUE = "opaque"


@dataclass(frozen=True)
class CloudVerdict:
    label: CloudLabel
    confidence: float
    stage_failed: int | None  # which stage rejected the profile, if any


@dataclass(frozen=True)
class Score:
    """Result of scoring one window."""

    components: dict[str, float]
    cloud: CloudVerdict
    total: float

    @property
    def is_event(self) -> bool:
        """Placeholder decision threshold, not the operational one."""
        return self.total >= 0.6 and self.cloud.label is not CloudLabel.OPAQUE


# --------------------------------------------------------------------------
# Three-stage cloud classifier
# --------------------------------------------------------------------------

# Placeholder gates. Real values are tuned per site and season.
_OPACITY_GATE = 8.0
_STRUCTURE_GATE = 3.5
_PERSISTENCE_GATE = 0.45


def classify_cloud(profile: tuple[Level, ...]) -> CloudVerdict:
    """Label a profile CLEAR, THIN, or OPAQUE.

    Cloud is the dominant false-positive source: a cloud base looks like a
    dense aerosol layer to a backscatter-only test. Three cheap stages run in
    order, and the first one that rejects wins, so the expensive test only sees
    profiles that survived the cheap ones.

    Stage 1  gross opacity   a hard return that saturates the gate
    Stage 2  vertical structure  cloud edges are sharper than aerosol layers
    Stage 3  persistence      real cloud holds its height across the profile
    """
    if not profile:
        return CloudVerdict(CloudLabel.CLEAR, 0.0, stage_failed=None)

    betas = [lv.backscatter for lv in profile]
    peak = max(betas)

    # Stage 1: gross opacity.
    if peak >= _OPACITY_GATE:
        return CloudVerdict(CloudLabel.OPAQUE, _sat(peak / _OPACITY_GATE), 1)

    # Stage 2: vertical structure. Aerosol layers taper, cloud edges do not.
    gradients = [abs(b - a) for a, b in zip(betas, betas[1:])]
    sharpest = max(gradients) if gradients else 0.0
    if sharpest >= _STRUCTURE_GATE:
        return CloudVerdict(CloudLabel.THIN, _sat(sharpest / _STRUCTURE_GATE), 2)

    # Stage 3: persistence. How much of the column sits near the peak.
    near_peak = sum(1 for b in betas if b >= 0.8 * peak) / len(betas)
    if near_peak >= _PERSISTENCE_GATE:
        return CloudVerdict(CloudLabel.THIN, _sat(near_peak), 3)

    return CloudVerdict(CloudLabel.CLEAR, 1.0 - _sat(near_peak), None)


# --------------------------------------------------------------------------
# Diurnal boundary-layer breakthrough
# --------------------------------------------------------------------------


def breakthrough_probability(
    hour_sgt: float, layer_height_m: float, boundary_layer_height_m: float
) -> float:
    """Probability that an elevated layer mixes down to the surface.

    Transported aerosol often arrives aloft and only reaches people when the
    convective boundary layer grows past it, which is a function of time of
    day. A layer sitting well above the boundary layer at 03:00 is not a
    surface problem yet; the same layer at 13:00 usually is.

    Returns 0 when the layer is far above the boundary layer, rising toward 1
    as the boundary layer approaches and passes it, weighted by how much
    convective growth the remaining daylight allows.
    """
    if layer_height_m <= 0 or boundary_layer_height_m <= 0:
        return 0.0

    # How close the boundary layer already is, in units of layer height.
    proximity = _sat(boundary_layer_height_m / layer_height_m)

    # Convective growth potential: peaks early afternoon, near zero overnight.
    # Placeholder curve, a smooth bump centred on 14:00 local.
    growth = math.exp(-(((hour_sgt - 14.0) / 4.5) ** 2))

    # Already broken through: proximity alone carries it.
    if boundary_layer_height_m >= layer_height_m:
        return _sat(0.75 + 0.25 * growth)

    return _sat(proximity * (0.35 + 0.65 * growth))


# --------------------------------------------------------------------------
# Six-component probability model
# --------------------------------------------------------------------------

# Placeholder weights. They sum to 1 so the total reads as a probability.
WEIGHTS: dict[str, float] = {
    "aloft_enhancement": 0.22,
    "wind_alignment": 0.18,
    "fire_upwind": 0.20,
    "surface_corroboration": 0.15,
    "cloud_clearance": 0.10,
    "breakthrough": 0.15,
}


def score(window: Window) -> Score:
    """Score one window. Pure function, safe to run live or in replay."""
    cloud = classify_cloud(window.profile)
    peak_level = _peak_level(window.profile)

    components = {
        "aloft_enhancement": _aloft_enhancement(window, peak_level),
        "wind_alignment": _wind_alignment(window, peak_level),
        "fire_upwind": _fire_upwind(window),
        "surface_corroboration": _sat(window.pm25_ugm3 / 55.0),
        "cloud_clearance": _cloud_clearance(cloud),
        "breakthrough": breakthrough_probability(
            window.start_hour_sgt,
            peak_level.height_m if peak_level else 0.0,
            window.boundary_layer_height_m,
        ),
    }

    total = sum(WEIGHTS[k] * v for k, v in components.items())
    return Score(components=components, cloud=cloud, total=_sat(total))


def _aloft_enhancement(window: Window, peak: Level | None) -> float:
    """Backscatter at the peak, relative to the column median."""
    if peak is None or not window.profile:
        return 0.0
    betas = sorted(lv.backscatter for lv in window.profile)
    median = betas[len(betas) // 2]
    if median <= 0:
        return 0.0
    return _sat((peak.backscatter / median - 1.0) / 2.0)


def _wind_alignment(window: Window, peak: Level | None) -> float:
    """Does the wind at the layer point back at the fires?"""
    if peak is None or window.upwind_fire_count == 0:
        return 0.0
    delta = _angular_delta(peak.wind_dir_deg, window.upwind_fire_bearing_deg)
    directional = 1.0 - _sat(delta / 90.0)
    # Slow wind carries less, and does not sustain a plume over the distance.
    speed = _sat(peak.wind_speed_ms / 6.0)
    return directional * speed


def _fire_upwind(window: Window) -> float:
    """Saturating count: one fire matters far more than the tenth."""
    if window.upwind_fire_count <= 0:
        return 0.0
    return _sat(math.log1p(window.upwind_fire_count) / math.log1p(20))


def _cloud_clearance(cloud: CloudVerdict) -> float:
    if cloud.label is CloudLabel.OPAQUE:
        return 0.0
    if cloud.label is CloudLabel.THIN:
        return 0.4 * (1.0 - _sat(cloud.confidence))
    return cloud.confidence


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _sat(x: float) -> float:
    """Clamp to the unit interval. Every component is a probability."""
    return max(0.0, min(1.0, x))


def _angular_delta(a_deg: float, b_deg: float) -> float:
    """Smallest absolute angle between two bearings, in degrees."""
    d = abs(a_deg - b_deg) % 360.0
    return min(d, 360.0 - d)


def _peak_level(profile: tuple[Level, ...]) -> Level | None:
    return max(profile, key=lambda lv: lv.backscatter) if profile else None
