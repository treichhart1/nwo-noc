import math
from .terrain import Sampler
from .physics import ray, ENGINE, G


def calculate(inputs, progress=lambda p: None):
    if inputs["kind"] == "link":
        targets = [inputs["target"]]
    else:
        a, b, c, d = inputs["bbox"]
        n = inputs["grid_size"]
        targets = [
            {
                "longitude": a + (c - a) * x / (n - 1),
                "latitude": b + (d - b) * y / (n - 1),
                "height_m": inputs["receiver_height_m"],
                "ground_m": None,
            }
            for y in range(n)
            for x in range(n)
        ]
    sampler = Sampler(inputs["terrain"])
    points = []
    try:
        for index, t in enumerate(targets):
            rays = [
                ray(e, t, p["parameters"], inputs, sampler, inputs["kind"] == "link")
                for e, p in zip(inputs["emitters"], inputs["profiles"])
            ]
            supported = [r for r in rays if r["predicted_dbm"] is not None]
            usable = [r for r in supported if r["status"] == "potentially_serviceable"]
            best = max(
                usable or supported, key=lambda r: r["predicted_dbm"], default=None
            )
            status = (
                best["status"]
                if best
                else (
                    "no_predicted_coverage"
                    if all(r["status"].startswith("outside_") for r in rays)
                    else "insufficient_data"
                )
            )
            points.append(
                {
                    "longitude": t["longitude"],
                    "latitude": t["latitude"],
                    "status": status,
                    "best_predicted_dbm": best["predicted_dbm"] if best else None,
                    "coverage_overlap": len(usable),
                    "rays": rays,
                }
            )
            if index % 8 == 0:
                progress(int(95 * (index + 1) / len(targets)))
    finally:
        sampler.close()
    findings = []
    counts = {
        s: sum(p["status"] == s for p in points)
        for s in sorted({p["status"] for p in points})
    }
    for status, action in [
        (
            "obstruction_risk",
            "Review surface data, physical LOS and proposed antenna-height scenario",
        ),
        (
            "no_predicted_coverage",
            "Review documented sector alignment and compare a proposed location scenario",
        ),
        (
            "insufficient_data",
            "Acquire missing local terrain, antenna specifications and receiver evidence",
        ),
    ]:
        if counts.get(status):
            findings.append(
                {
                    "kind": status,
                    "evidence": {
                        "grid_points": counts[status],
                        "scenario_engine": ENGINE,
                        "dataset_ids": inputs["dataset_ids"],
                    },
                    "recommendation": action,
                    "confidence": "limited by model and source evidence; not a field guarantee",
                }
            )
    overlap = sum(p["coverage_overlap"] > 1 for p in points)
    if overlap:
        findings.append(
            {
                "kind": "predicted_overlap",
                "evidence": {"grid_points": overlap},
                "recommendation": "Survey overlap and interference; coverage overlap alone does not establish interference",
            }
        )
    return {
        "engine": ENGINE,
        "predicted": True,
        "points": points,
        "counts": counts,
        "recommendations": findings,
        "assumptions": {
            k: inputs[k]
            for k in [
                "model",
                "refraction_k",
                "fresnel_fraction",
                "profile_samples",
                "atmospheric_loss_db_per_km",
            ]
        },
        "limitations": [
            "No field-verified coverage, throughput, rain availability or installation approval",
            "Beam membership uses documented widths, not an invented antenna radiation pattern",
            "No automatic radio changes or parameter calibration",
        ],
    }
