"""Run the scorer over three synthetic windows and print the breakdown.

    python examples/synthetic_window.py

No installation, no data files, no network. Every number below is invented to
illustrate the three cases the scorer has to separate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scorer import Level, Window, score  # noqa: E402


def profile(pairs, wind_dir, wind_speed=5.5):
    return tuple(
        Level(height_m=h, backscatter=b, wind_dir_deg=wind_dir, wind_speed_ms=wind_speed)
        for h, b in pairs
    )


CASES = {
    "transport event, afternoon": Window(
        site="NTU Smart Campus",
        start_hour_sgt=14.0,
        # elevated layer near 1 km, smooth edges
        profile=profile([(200, 1.0), (600, 1.5), (1000, 3.2), (1400, 1.8), (1800, 1.0)], 200.0),
        pm25_ugm3=42.0,
        upwind_fire_count=12,
        upwind_fire_bearing_deg=205.0,
        boundary_layer_height_m=1100.0,
    ),
    "same layer, but overnight": Window(
        site="NTU Smart Campus",
        start_hour_sgt=3.0,
        profile=profile([(200, 1.0), (600, 1.5), (1000, 3.2), (1400, 1.8), (1800, 1.0)], 200.0),
        pm25_ugm3=18.0,
        upwind_fire_count=12,
        upwind_fire_bearing_deg=205.0,
        boundary_layer_height_m=300.0,
    ),
    "cloud masquerading as a layer": Window(
        site="Woodlands Health",
        start_hour_sgt=14.0,
        # hard, saturating return: this is a cloud base, not aerosol
        profile=profile([(200, 0.9), (700, 14.0), (1100, 0.8)], 200.0),
        pm25_ugm3=40.0,
        upwind_fire_count=12,
        upwind_fire_bearing_deg=205.0,
        boundary_layer_height_m=1100.0,
    ),
}


def main() -> None:
    for title, w in CASES.items():
        s = score(w)
        print(f"\n{title}")
        print(f"  site {w.site}   {w.start_hour_sgt:04.1f} SGT")
        print(f"  cloud: {s.cloud.label.value}", end="")
        if s.cloud.stage_failed:
            print(f" (rejected at stage {s.cloud.stage_failed})")
        else:
            print()
        for name, value in s.components.items():
            bar = "#" * round(value * 24)
            print(f"    {name:<22} {value:5.2f}  {bar}")
        verdict = "EVENT" if s.is_event else "no event"
        print(f"  total {s.total:.2f}  ->  {verdict}")

    print(
        "\nCase 2 carries the identical aerosol layer through a nocturnal"
        "\nboundary layer, and the breakthrough term collapses from 1.00 to"
        "\n0.11: aloft is not the same as at the surface."
        "\n"
        "\nCase 3 scores 0.80 and is still rejected. The cloud classifier is a"
        "\ngate, not a term, which is why a backscatter-only test is not enough."
    )


if __name__ == "__main__":
    main()
