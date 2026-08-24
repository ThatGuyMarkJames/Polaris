"""
POLARIS Load Management
Defines station loads, calculates demand, and handles load shedding.
"""

import math
import numpy as np

_rng = np.random.RandomState(45)


def reset_rng(seed: int = 45):
    global _rng
    _rng = np.random.RandomState(seed)


def get_default_loads() -> list[dict]:
    """Return default station loads."""
    return [
        {"name": "Heating", "power_kw": 50.0, "category": "CRITICAL", "is_active": True, "is_deferred": False},
        {"name": "Research Equipment", "power_kw": 30.0, "category": "CRITICAL", "is_active": True, "is_deferred": False},
        {"name": "Communication", "power_kw": 10.0, "category": "CRITICAL", "is_active": True, "is_deferred": False},
        {"name": "Emergency Systems", "power_kw": 8.0, "category": "CRITICAL", "is_active": True, "is_deferred": False},
        {"name": "Navigation & Safety", "power_kw": 5.0, "category": "CRITICAL", "is_active": True, "is_deferred": False},
        {"name": "Lighting", "power_kw": 10.0, "category": "IMPORTANT", "is_active": True, "is_deferred": False},
        {"name": "Laboratory Equipment", "power_kw": 12.0, "category": "IMPORTANT", "is_active": True, "is_deferred": False},
        {"name": "General Infrastructure", "power_kw": 8.0, "category": "IMPORTANT", "is_active": True, "is_deferred": False},
        {"name": "Computing", "power_kw": 20.0, "category": "DEFERRABLE", "is_active": True, "is_deferred": False},
        {"name": "Water Heating", "power_kw": 15.0, "category": "DEFERRABLE", "is_active": True, "is_deferred": False},
        {"name": "Charging Equipment", "power_kw": 8.0, "category": "DEFERRABLE", "is_active": True, "is_deferred": False},
        {"name": "Non-Essential Lab", "power_kw": 6.0, "category": "DEFERRABLE", "is_active": True, "is_deferred": False},
    ]


def calculate_demand(
    loads: list[dict],
    temperature: float = -18.0,
    hour: float = 0.0,
    demand_increase_pct: float = 0.0,
) -> dict:
    """
    Calculate total station demand based on loads, temperature, and time.

    - Heating demand increases with colder temperatures
    - Lighting varies with time of day
    - General demand has a daily pattern
    """
    adjusted_loads = []
    total_demand = 0.0
    critical_demand = 0.0
    important_demand = 0.0
    deferrable_demand = 0.0

    hour_of_day = hour % 24

    for load in loads:
        adjusted = dict(load)
        base_power = load.get("original_power_kw", load["power_kw"])

        if load["is_deferred"] or not load["is_active"]:
            adjusted["power_kw"] = 0.0
            adjusted_loads.append(adjusted)
            continue

        # Temperature-dependent heating
        if load["name"] == "Heating":
            # Heating increases as temperature drops below -15°C
            temp_factor = 1.0 + max(0, (-15 - temperature) * 0.02)
            adjusted["power_kw"] = round(base_power * temp_factor, 2)

        # Time-dependent lighting
        elif load["name"] == "Lighting":
            # Less lighting needed during "day" (polar day still has brightness variation)
            if 6 <= hour_of_day <= 20:
                adjusted["power_kw"] = round(base_power * 0.6, 2)
            else:
                adjusted["power_kw"] = round(base_power * 1.0, 2)

        # Research varies by shift
        elif load["name"] == "Research Equipment":
            if 8 <= hour_of_day <= 22:
                adjusted["power_kw"] = round(base_power * 1.1, 2)
            else:
                adjusted["power_kw"] = round(base_power * 0.7, 2)

        # Computing varies
        elif load["name"] == "Computing":
            if 10 <= hour_of_day <= 18:
                adjusted["power_kw"] = round(base_power * 1.2, 2)
            else:
                adjusted["power_kw"] = round(base_power * 0.8, 2)

        else:
            adjusted["power_kw"] = base_power

        # Add small noise
        noise = _rng.normal(1.0, 0.02)
        adjusted["power_kw"] = round(max(0, adjusted["power_kw"] * noise), 2)

        # Apply demand increase scenario
        if demand_increase_pct > 0:
            adjusted["power_kw"] = round(adjusted["power_kw"] * (1 + demand_increase_pct / 100.0), 2)

        # Tally by category
        if adjusted["category"] == "CRITICAL":
            critical_demand += adjusted["power_kw"]
        elif adjusted["category"] == "IMPORTANT":
            important_demand += adjusted["power_kw"]
        elif adjusted["category"] == "DEFERRABLE":
            deferrable_demand += adjusted["power_kw"]

        total_demand += adjusted["power_kw"]
        adjusted_loads.append(adjusted)

    return {
        "loads": adjusted_loads,
        "total_demand_kw": round(total_demand, 2),
        "critical_demand_kw": round(critical_demand, 2),
        "important_demand_kw": round(important_demand, 2),
        "deferrable_demand_kw": round(deferrable_demand, 2),
    }


def shed_loads(
    loads: list[dict],
    deficit_kw: float,
) -> tuple[list[dict], list[str], float]:
    """
    Shed loads to reduce demand by deficit_kw.
    Priority: DEFERRABLE first, then IMPORTANT, never CRITICAL.

    Returns: (updated_loads, deferred_load_names, remaining_deficit)
    """
    remaining = deficit_kw
    deferred_names = []
    updated = [dict(l) for l in loads]

    # First: shed deferrable loads
    for load in updated:
        if remaining <= 0:
            break
        if load["category"] == "DEFERRABLE" and load["is_active"] and not load["is_deferred"]:
            deferred_names.append(load["name"])
            remaining -= load["power_kw"]
            load["is_deferred"] = True
            load["power_kw"] = 0.0

    # Second: reduce important loads if still deficit
    if remaining > 0:
        for load in updated:
            if remaining <= 0:
                break
            if load["category"] == "IMPORTANT" and load["is_active"] and not load["is_deferred"]:
                # Reduce by up to 50%
                reduction = min(load["power_kw"] * 0.5, remaining)
                load["power_kw"] = round(load["power_kw"] - reduction, 2)
                remaining -= reduction
                if load["power_kw"] < 1.0:
                    load["is_deferred"] = True
                    remaining -= load["power_kw"]
                    load["power_kw"] = 0.0
                    deferred_names.append(load["name"])

    return updated, deferred_names, max(0, remaining)


def restore_loads(loads: list[dict], default_loads: list[dict]) -> list[dict]:
    """Restore all deferred loads to their original values."""
    defaults_map = {l["name"]: l for l in default_loads}
    restored = []
    for load in loads:
        if load["is_deferred"]:
            default = defaults_map.get(load["name"], load)
            restored.append({
                **load,
                "power_kw": default["power_kw"],
                "is_deferred": False,
                "is_active": True,
            })
        else:
            restored.append(load)
    return restored


def predict_demand(
    loads: list[dict],
    current_hour: float,
    hours_ahead: int = 72,
    temperature_forecast: list[float] | None = None,
) -> list[dict]:
    """Predict demand for the next N hours."""
    predictions = []
    saved_state = _rng.get_state()
    pred_rng = np.random.RandomState(46)

    for h in range(hours_ahead):
        future_hour = current_hour + h
        temp = temperature_forecast[h] if temperature_forecast and h < len(temperature_forecast) else -18.0

        result = calculate_demand(
            loads=loads,
            temperature=temp,
            hour=future_hour,
        )

        # Add prediction uncertainty growing with time
        uncertainty = 1.0 + pred_rng.normal(0, 0.03 * (1 + h * 0.01))
        predicted = result["total_demand_kw"] * uncertainty

        predictions.append({
            "hour": future_hour,
            "predicted_demand_kw": round(max(0, predicted), 2),
            "confidence": max(0.5, 1.0 - 0.005 * h),
        })

    _rng.set_state(saved_state)
    return predictions
