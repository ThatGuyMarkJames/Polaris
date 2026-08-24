"""
POLARIS Battery Model
Tracks state of charge, charge/discharge with efficiency losses,
safety constraints, and degradation.
"""

import math


def update_battery(
    soc: float,              # current SoC percentage
    capacity_kwh: float,     # total capacity
    power_kw: float,         # positive=charge, negative=discharge
    dt_hours: float,         # timestep duration in hours
    charge_efficiency: float = 0.92,
    discharge_efficiency: float = 0.95,
    min_reserve: float = 30.0,
    max_soc: float = 95.0,
    charge_rate_max_kw: float = 100.0,
    discharge_rate_max_kw: float = 150.0,
    health: float = 98.0,
) -> dict:
    """
    Update battery state for one timestep.

    Returns updated state including actual power flow,
    new SoC, and whether constraints were hit.
    """
    # Adjust capacity for health
    effective_capacity = capacity_kwh * (health / 100.0)

    current_energy = (soc / 100.0) * effective_capacity
    constraint_hit = None

    if power_kw > 0:
        # CHARGING
        actual_power = min(power_kw, charge_rate_max_kw)
        energy_in = actual_power * dt_hours * charge_efficiency

        # Check max SoC constraint
        max_energy = (max_soc / 100.0) * effective_capacity
        available_capacity = max_energy - current_energy

        if available_capacity <= 0:
            actual_power = 0.0
            energy_in = 0.0
            constraint_hit = "MAX_SOC"
        elif energy_in > available_capacity:
            energy_in = available_capacity
            actual_power = energy_in / (dt_hours * charge_efficiency)
            constraint_hit = "NEAR_MAX"

        new_energy = current_energy + energy_in
        new_soc = (new_energy / effective_capacity) * 100.0

        return {
            "soc": round(min(max_soc, new_soc), 2),
            "power_flow_kw": round(actual_power, 2),
            "is_charging": actual_power > 0.1,
            "is_discharging": False,
            "energy_change_kwh": round(energy_in, 3),
            "constraint_hit": constraint_hit,
            "health": health,
        }

    elif power_kw < 0:
        # DISCHARGING
        actual_power = max(power_kw, -discharge_rate_max_kw)
        energy_out = abs(actual_power) * dt_hours / discharge_efficiency

        # Check min reserve constraint
        min_energy = (min_reserve / 100.0) * effective_capacity
        available_energy = current_energy - min_energy

        if available_energy <= 0:
            actual_power = 0.0
            energy_out = 0.0
            constraint_hit = "MIN_RESERVE"
        elif energy_out > available_energy:
            energy_out = available_energy
            actual_power = -(energy_out * discharge_efficiency / dt_hours)
            constraint_hit = "NEAR_MIN"

        new_energy = current_energy - energy_out
        new_soc = (new_energy / effective_capacity) * 100.0

        # Slight degradation from cycling
        degradation = 0.0001 * abs(energy_out / effective_capacity)

        return {
            "soc": round(max(0, new_soc), 2),
            "power_flow_kw": round(actual_power, 2),
            "is_charging": False,
            "is_discharging": abs(actual_power) > 0.1,
            "energy_change_kwh": round(-energy_out, 3),
            "constraint_hit": constraint_hit,
            "health": round(health - degradation, 4),
        }

    else:
        # IDLE
        # Small self-discharge (0.01% per hour)
        self_discharge = 0.0001 * dt_hours
        new_soc = soc * (1 - self_discharge)

        return {
            "soc": round(new_soc, 2),
            "power_flow_kw": 0.0,
            "is_charging": False,
            "is_discharging": False,
            "energy_change_kwh": 0.0,
            "constraint_hit": None,
            "health": health,
        }


def calculate_battery_metrics(
    soc: float,
    capacity_kwh: float,
    min_reserve: float,
    health: float = 98.0,
) -> dict:
    """Calculate battery metrics for display."""
    effective_capacity = capacity_kwh * (health / 100.0)
    current_energy = (soc / 100.0) * effective_capacity
    reserve_energy = (min_reserve / 100.0) * effective_capacity
    usable_energy = max(0, current_energy - reserve_energy)

    hours_at_100kw = usable_energy / 100.0 if usable_energy > 0 else 0

    return {
        "effective_capacity_kwh": round(effective_capacity, 1),
        "stored_energy_kwh": round(current_energy, 1),
        "usable_energy_kwh": round(usable_energy, 1),
        "reserve_energy_kwh": round(reserve_energy, 1),
        "hours_remaining_at_100kw": round(hours_at_100kw, 1),
        "soc_above_reserve": round(max(0, soc - min_reserve), 1),
    }
