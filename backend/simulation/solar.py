"""
POLARIS Solar Generation Model
Calculates solar power output based on weather conditions,
panel capacity, and efficiency factors.
"""

import math
import numpy as np

_rng = np.random.RandomState(43)


def reset_rng(seed: int = 43):
    global _rng
    _rng = np.random.RandomState(seed)


def calculate_solar_output(
    solar_irradiance: float,   # W/m²
    cloud_cover: float,        # 0-100%
    temperature: float,        # °C
    capacity_kw: float = 200.0,
    panel_efficiency: float = 0.85,
    scenario_reduction: float = 0.0,  # 0-100%
) -> dict:
    """
    Calculate solar panel output based on current conditions.

    Solar output model:
    - Base output proportional to irradiance
    - Temperature coefficient: panels are more efficient in cold
    - Cloud cover already factored into irradiance
    - Scenario reduction for failure simulations
    - Realistic noise ±3-6%
    """
    if solar_irradiance <= 0:
        return {
            "current_output_kw": 0.0,
            "efficiency": panel_efficiency,
            "panel_temperature": temperature + 5,  # slight warming from equipment
            "capacity_factor": 0.0,
        }

    # Reference irradiance for panel rating
    reference_irradiance = 1000.0  # W/m² (STC)

    # Base output from irradiance
    irradiance_factor = solar_irradiance / reference_irradiance

    # Temperature coefficient: panels gain ~0.3% per °C below 25°C
    # In polar conditions (-20°C), this gives a nice boost
    temp_coeff = 0.003  # per °C
    reference_temp = 25.0
    temp_factor = 1.0 + temp_coeff * (reference_temp - temperature)
    temp_factor = max(0.8, min(1.3, temp_factor))  # clamp

    # Panel temperature (lower than ambient due to wind cooling in polar)
    panel_temp = temperature + 10 + solar_irradiance * 0.02

    # Calculate raw output
    raw_output = capacity_kw * irradiance_factor * temp_factor * panel_efficiency

    # Apply scenario reduction
    reduction_factor = 1.0 - (scenario_reduction / 100.0)
    raw_output *= reduction_factor

    # Add realistic noise (±3-6%)
    noise = _rng.normal(1.0, 0.04)
    raw_output *= noise

    # Clamp to [0, capacity]
    output = max(0.0, min(capacity_kw, raw_output))
    output = round(output, 2)

    # Capacity factor
    capacity_factor = output / capacity_kw if capacity_kw > 0 else 0

    return {
        "current_output_kw": output,
        "efficiency": round(panel_efficiency * temp_factor, 3),
        "panel_temperature": round(panel_temp, 1),
        "capacity_factor": round(capacity_factor, 3),
    }


def predict_solar_generation(
    weather_forecast: list[dict],
    capacity_kw: float = 200.0,
    panel_efficiency: float = 0.85,
    scenario_reduction: float = 0.0,
) -> list[dict]:
    """
    Predict solar generation for a weather forecast.
    Returns list of {hour, predicted_kw, confidence}.
    """
    predictions = []
    saved_state = _rng.get_state()
    pred_rng = np.random.RandomState(44)

    for point in weather_forecast:
        result = calculate_solar_output(
            solar_irradiance=point.get("solar_irradiance", 0),
            cloud_cover=point.get("cloud_cover", 50),
            temperature=point.get("temperature", -18),
            capacity_kw=capacity_kw,
            panel_efficiency=panel_efficiency,
            scenario_reduction=scenario_reduction,
        )

        # Add prediction uncertainty
        confidence = point.get("confidence", 0.9)
        uncertainty_factor = 1.0 + pred_rng.normal(0, 0.05 * (1 - confidence + 0.1))
        predicted_kw = max(0, result["current_output_kw"] * uncertainty_factor)

        predictions.append({
            "hour": point.get("hour", 0),
            "predicted_solar_kw": round(predicted_kw, 2),
            "confidence": confidence,
        })

    _rng.set_state(saved_state)
    return predictions
