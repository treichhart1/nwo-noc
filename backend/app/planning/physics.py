"""Versioned analytical predictions; FSPL and single-dominant knife-edge approximation."""

import math
from pyproj import Geod

G = Geod(ellps="WGS84")
ENGINE = "noc105-fspl-ke-v1"


def fspl(distance_m, frequency_mhz):
    if distance_m <= 0 or frequency_mhz <= 0:
        raise ValueError("Positive distance and frequency required")
    return (
        32.447783 + 20 * math.log10(distance_m / 1000) + 20 * math.log10(frequency_mhz)
    )


def fresnel(frequency_mhz, d1, d2):
    return (
        math.sqrt((299792458 / (frequency_mhz * 1e6)) * d1 * d2 / (d1 + d2))
        if d1 > 0 and d2 > 0
        else 0
    )


def knife_edge(v):
    return (
        0
        if v <= -0.78
        else 6.9 + 20 * math.log10(math.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)
    )


def delta_angle(a, b):
    return abs((a - b + 180) % 360 - 180)


def ray(emitter, target, parameters, options, sampler, include_profile=False):
    az, _, distance = G.inv(
        emitter["longitude"],
        emitter["latitude"],
        target["longitude"],
        target["latitude"],
    )
    out = {
        "predicted": True,
        "engine": ENGINE,
        "distance_m": round(distance, 2),
        "predicted_dbm": None,
        "status": "insufficient_data",
        "uncertainty": [],
        "model": options["model"],
    }
    if not 1 <= distance <= 100000:
        out["uncertainty"].append("Distance outside1m–100km planning bounds")
        return out
    if (
        emitter.get("azimuth_deg") is None
        or emitter.get("horizontal_beamwidth_deg") is None
    ):
        out["uncertainty"].append("Missing documented azimuth/beamwidth")
        return out
    if (
        delta_angle(az % 360, emitter["azimuth_deg"])
        > emitter["horizontal_beamwidth_deg"] / 2
    ):
        out["status"] = "outside_sector"
        return out
    req = ["frequency_mhz", "tx_power_dbm", "tx_gain_dbi", "tx_loss_db"]
    if (
        any(parameters.get(k) is None for k in req)
        or options["rx_gain_dbi"] is None
        or options["rx_loss_db"] is None
    ):
        out["uncertainty"].append("Missing RF link-budget inputs")
        return out
    frequency = parameters["frequency_mhz"]
    n = options["profile_samples"]
    positions = (
        [(emitter["longitude"], emitter["latitude"])]
        + G.npts(
            emitter["longitude"],
            emitter["latitude"],
            target["longitude"],
            target["latitude"],
            n - 2,
        )
        + [(target["longitude"], target["latitude"])]
    )
    grounds = []
    surfaces = []
    ids = set()
    for lon, lat in positions:
        ground, gid = sampler.value("ground", lon, lat)
        surface, sid = sampler.value("surface", lon, lat)
        grounds.append(ground)
        surfaces.append(surface)
        if gid:
            ids.add(gid)
        if sid:
            ids.add(sid)
    for i, p in [(0, emitter), (-1, target)]:
        if grounds[i] is None and p.get("ground_m") is not None:
            grounds[i] = p["ground_m"]
    out["terrain_dataset_ids"] = sorted(ids)
    if any(v is None for v in grounds):
        out["uncertainty"].append(
            "Missing ground terrain along path; no LOS or coverage claim"
        )
        return out
    surface_complete = all(v is not None for v in surfaces)
    out["terrain_basis"] = (
        "ground_and_surface" if surface_complete else "ground_only_or_partial_surface"
    )
    if not surface_complete:
        out["uncertainty"].append(
            "Buildings/vegetation not fully represented; ground clearance is not verified LOS"
        )
    obstacles = [max(g, s) if s is not None else g for g, s in zip(grounds, surfaces)]
    h0 = grounds[0] + emitter["height_m"]
    h1 = grounds[-1] + target["height_m"]
    elevation = math.degrees(math.atan2(h1 - h0, distance))
    tilt = emitter.get("mechanical_downtilt_deg")
    et = emitter.get("electrical_downtilt_deg")
    vb = emitter.get("vertical_beamwidth_deg")
    if tilt is not None and et is not None and vb is not None:
        if abs(elevation + tilt + et) > vb / 2:
            out["status"] = "outside_vertical_beam"
            return out
    else:
        out["uncertainty"].append(
            "Vertical pattern/downtilt incomplete; boresight upper-bound gain used"
        )
    profile = []
    min_clear = float("inf")
    min_ratio = float("inf")
    vmax = -float("inf")
    obstructed = 0
    for i in range(1, n - 1):
        d1 = distance * i / (n - 1)
        d2 = distance - d1
        bulge = d1 * d2 / (2 * options["refraction_k"] * 6371008.8)
        line = h0 + (h1 - h0) * i / (n - 1)
        clear = line - obstacles[i] - bulge
        r = fresnel(frequency, d1, d2)
        min_clear = min(min_clear, clear)
        min_ratio = min(min_ratio, clear / r)
        v = -clear * math.sqrt(2) / r
        vmax = max(vmax, v)
        if clear < 0:
            obstructed += 1
        profile.append(
            {
                "distance_m": round(d1, 2),
                "ground_m": round(grounds[i], 3),
                "surface_m": surfaces[i],
                "ray_m": round(line, 3),
                "curvature_m": round(bulge, 3),
                "clearance_m": round(clear, 3),
                "fresnel_m": round(r, 3),
            }
        )
    loss = fspl(distance, frequency)
    diffraction = knife_edge(vmax) if options["model"] == "dominant_knife_edge" else 0
    atmospheric = options.get("atmospheric_loss_db_per_km")
    if atmospheric is None:
        out["uncertainty"].append(
            "Atmospheric/rain/gas loss unmodeled; especially material at60GHz"
        )
        atmospheric = 0
    signal = (
        parameters["tx_power_dbm"]
        + parameters["tx_gain_dbi"]
        + options["rx_gain_dbi"]
        - parameters["tx_loss_db"]
        - options["rx_loss_db"]
        - loss
        - diffraction
        - atmospheric * distance / 1000
    )
    sens = parameters.get("receiver_sensitivity_dbm")
    margin = signal - sens if sens is not None else None
    if parameters.get("polarization") is None or options.get(
        "rx_polarization"
    ) != parameters.get("polarization"):
        out["uncertainty"].append("Polarization compatibility/loss unverified")
    out.update(
        predicted_dbm=round(signal, 3),
        fspl_db=round(loss, 3),
        diffraction_db=round(diffraction, 3),
        fade_margin_db=None if margin is None else round(margin, 3),
        minimum_clearance_m=round(min_clear, 3),
        minimum_fresnel_fraction=round(min_ratio, 3),
        obstructed_samples=obstructed,
        profile_spacing_m=round(distance / (n - 1), 2),
        status=(
            "obstruction_risk"
            if min_clear < 0 or min_ratio < options["fresnel_fraction"]
            else (
                "no_predicted_coverage"
                if margin is not None and margin < options["required_fade_margin_db"]
                else (
                    "potentially_serviceable"
                    if margin is not None
                    else "insufficient_data"
                )
            )
        ),
    )
    if min_clear < 0 and options["model"] == "fspl":
        out["uncertainty"].append(
            "Obstructed FSPL is an upper bound; blockage attenuation not modeled"
        )
    if options["model"] == "dominant_knife_edge":
        out["uncertainty"].append(
            "Single dominant-edge approximation; multiple diffraction, reflection and clutter not validated"
        )
    out["uncertainty"].append(
        "Discrete nearest-pixel samples can miss narrow obstructions; site survey required"
    )
    if include_profile:
        out["profile"] = profile
    return out
