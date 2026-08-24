"""
POLARIS Scenario Presets
Pre-defined what-if scenarios for the simulation.
"""

from backend.simulation.state import ScenarioConfig


SCENARIO_PRESETS = {
    "NORMAL": ScenarioConfig(
        name="NORMAL",
        description="Normal operating conditions. Clear skies, moderate wind.",
    ),

    "POLAR_STORM": ScenarioConfig(
        name="POLAR STORM",
        solar_reduction_pct=70,
        temperature_delta=-10,
        demand_increase_pct=25,
        wind_increase_pct=50,
        storm_active=True,
        duration_hours=48,
        description="48-hour polar storm. Solar generation drops 70%, temperature falls 10°C, heating demand rises 25%, winds increase 50%.",
    ),

    "SOLAR_FAILURE": ScenarioConfig(
        name="SOLAR FAILURE",
        solar_reduction_pct=95,
        duration_hours=24,
        description="Solar panel array failure. 95% reduction in solar generation for 24 hours.",
    ),

    "GENERATOR_FAILURE": ScenarioConfig(
        name="GENERATOR FAILURE",
        generator_available=False,
        duration_hours=12,
        description="Diesel generator failure. No backup generation available for 12 hours. Station must survive on solar and battery alone.",
    ),

    "BATTERY_DEGRADATION": ScenarioConfig(
        name="BATTERY DEGRADATION",
        battery_health=60.0,
        duration_hours=72,
        description="Battery bank degradation. Effective capacity reduced to 60%. Tests system resilience with reduced storage.",
    ),

    "EXTREME_COLD": ScenarioConfig(
        name="EXTREME COLD",
        temperature_delta=-20,
        demand_increase_pct=40,
        wind_increase_pct=30,
        duration_hours=36,
        description="Extreme cold snap. Temperature drops 20°C, heating demand surges 40%, high wind chill.",
    ),

    "LOAD_SPIKE": ScenarioConfig(
        name="SUDDEN LOAD SPIKE",
        demand_increase_pct=60,
        duration_hours=6,
        description="Sudden 60% increase in station demand for 6 hours. Simulates emergency equipment activation or equipment malfunction.",
    ),
}


def get_scenario(name: str) -> ScenarioConfig:
    """Get a scenario preset by name."""
    key = name.upper().replace(" ", "_")
    return SCENARIO_PRESETS.get(key, SCENARIO_PRESETS["NORMAL"])


def get_all_scenarios() -> list[dict]:
    """Get all scenario presets as dicts."""
    return [
        {
            "id": key,
            "name": scenario.name,
            "description": scenario.description,
            "duration_hours": scenario.duration_hours,
            "solar_reduction_pct": scenario.solar_reduction_pct,
            "temperature_delta": scenario.temperature_delta,
            "demand_increase_pct": scenario.demand_increase_pct,
            "wind_increase_pct": scenario.wind_increase_pct,
            "generator_available": scenario.generator_available,
            "storm_active": scenario.storm_active,
        }
        for key, scenario in SCENARIO_PRESETS.items()
    ]
