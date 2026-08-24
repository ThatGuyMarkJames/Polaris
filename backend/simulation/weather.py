"""
POLARIS Weather Simulation
Generates realistic polar weather patterns with daily cycles,
noise, and scenario modifiers (storms, extreme cold).
"""

import math
import numpy as np
from typing import Optional


# Fixed seed for deterministic behavior
_rng = np.random.RandomState(42)


def reset_rng(seed: int = 42):
    global _rng
    _rng = np.random.RandomState(seed)


def simulate_weather(
    hour: float,
    scenario_name: str = "NORMAL",
    scenario_solar_reduction: float = 0.0,
    scenario_temp_delta: float = 0.0,
    scenario_wind_increase: float = 0.0,
    storm_active: bool = False,
    storm_intensity: float = 0.0,  # 0-1
) -> dict:
    """
    Generate weather state for a given simulation hour.

    Polar summer conditions (Antarctic):
    - Near-continuous daylight (18-20h of usable sun)
    - Temperature: -5°C to -35°C depending on conditions
    - Wind: highly variable
    - Solar: low angle but long duration
    """
    # --- Temperature ---
    # Base: -18°C with small diurnal variation (±5°C)
    day_of_sim = hour / 24.0
    base_temp = -18.0
    diurnal = 5.0 * math.sin(2 * math.pi * (hour % 24 - 6) / 24.0)
    # Multi-day drift
    drift = 3.0 * math.sin(2 * math.pi * day_of_sim / 7.0)
    noise = _rng.normal(0, 1.0)
    temperature = base_temp + diurnal + drift + noise + scenario_temp_delta

    if storm_active:
        temperature -= 10.0 * storm_intensity
        temperature += _rng.normal(0, 2.0)

    temperature = round(temperature, 1)

    # --- Wind Speed ---
    base_wind = 25.0
    wind_variation = 15.0 * math.sin(2 * math.pi * hour / 12.0 + 1.5)
    wind_noise = _rng.normal(0, 5.0)
    wind_speed = max(0, base_wind + wind_variation + wind_noise)
    wind_speed *= (1 + scenario_wind_increase / 100.0)

    if storm_active:
        wind_speed += 40.0 * storm_intensity
        wind_speed += _rng.normal(0, 8.0)

    wind_speed = round(max(0, wind_speed), 1)

    # --- Solar Irradiance ---
    # Polar summer: sun is up ~18-20 hours, peaks around noon
    # Simple model: sinusoidal with extended daylight
    hour_of_day = hour % 24
    sunrise = 3.0   # 3 AM
    sunset = 21.0   # 9 PM
    solar_duration = sunset - sunrise

    if sunrise <= hour_of_day <= sunset:
        solar_phase = (hour_of_day - sunrise) / solar_duration
        # Peak irradiance at solar noon
        raw_irradiance = 350.0 * math.sin(math.pi * solar_phase)
        # Add noise
        raw_irradiance *= (1 + _rng.normal(0, 0.05))
    else:
        raw_irradiance = 0.0

    # Apply cloud cover effect
    cloud_cover = _simulate_cloud_cover(hour, storm_active, storm_intensity)
    cloud_factor = 1.0 - (cloud_cover / 100.0) * 0.8  # clouds reduce up to 80%

    # Apply scenario reduction
    scenario_factor = 1.0 - (scenario_solar_reduction / 100.0)

    solar_irradiance = max(0, raw_irradiance * cloud_factor * scenario_factor)
    solar_irradiance = round(solar_irradiance, 1)

    # --- Snowfall ---
    snowfall = 0.0
    if storm_active:
        snowfall = _rng.exponential(2.0) * storm_intensity
    elif _rng.random() < 0.1:
        snowfall = _rng.exponential(0.5)
    snowfall = round(max(0, snowfall), 1)

    # --- Storm Probability ---
    base_storm_prob = 10.0
    if storm_active:
        storm_probability = 80.0 + 20.0 * storm_intensity
    else:
        # Some natural variation
        storm_probability = base_storm_prob + 15.0 * math.sin(2 * math.pi * day_of_sim / 5.0)
        storm_probability += _rng.normal(0, 5.0)
    storm_probability = round(max(0, min(100, storm_probability)), 1)

    # --- Weather Severity ---
    if storm_active and storm_intensity > 0.7:
        severity = "EXTREME"
    elif storm_active and storm_intensity > 0.3:
        severity = "SEVERE"
    elif wind_speed > 50 or temperature < -30:
        severity = "MODERATE"
    else:
        severity = "NORMAL"

    return {
        "temperature": temperature,
        "wind_speed": wind_speed,
        "solar_irradiance": solar_irradiance,
        "cloud_cover": round(cloud_cover, 1),
        "snowfall": snowfall,
        "storm_probability": storm_probability,
        "weather_severity": severity,
    }


def _simulate_cloud_cover(
    hour: float,
    storm_active: bool = False,
    storm_intensity: float = 0.0,
) -> float:
    """Generate cloud cover percentage (0-100)."""
    base = 45.0
    variation = 25.0 * math.sin(2 * math.pi * hour / 18.0 + 0.7)
    daily_drift = 15.0 * math.sin(2 * math.pi * hour / (24 * 3.0))
    noise = _rng.normal(0, 8.0)

    cloud = base + variation + daily_drift + noise

    if storm_active:
        cloud += 40.0 * storm_intensity
        cloud += _rng.normal(0, 5.0)

    return max(0, min(100, cloud))


def generate_weather_forecast(
    current_hour: float,
    hours_ahead: int = 72,
    scenario_name: str = "NORMAL",
    storm_active: bool = False,
) -> list[dict]:
    """Generate a weather forecast for the next N hours."""
    forecast = []
    # Save rng state to not affect main simulation
    saved_state = _rng.get_state()

    forecast_rng = np.random.RandomState(int(current_hour * 100) % (2**31))

    for h in range(hours_ahead):
        future_hour = current_hour + h
        # Use a slightly different noise for forecast uncertainty
        weather = simulate_weather(
            future_hour,
            scenario_name=scenario_name,
            storm_active=storm_active,
            storm_intensity=0.6 if storm_active else 0.0,
        )
        # Add forecast uncertainty that grows with time
        uncertainty = 1.0 + 0.02 * h
        weather["temperature"] += forecast_rng.normal(0, 1.0 * uncertainty)
        weather["solar_irradiance"] = max(0, weather["solar_irradiance"] * (1 + forecast_rng.normal(0, 0.03 * uncertainty)))
        weather["wind_speed"] = max(0, weather["wind_speed"] + forecast_rng.normal(0, 2.0 * uncertainty))
        weather["cloud_cover"] = max(0, min(100, weather["cloud_cover"] + forecast_rng.normal(0, 3.0 * uncertainty)))
        weather["hour"] = future_hour
        weather["confidence"] = max(0.5, 1.0 - 0.005 * h)
        forecast.append(weather)

    # Restore rng state
    _rng.set_state(saved_state)
    return forecast
