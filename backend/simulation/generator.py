"""
POLARIS Diesel Generator Model
Manages generator startup, fuel consumption, and output.
"""


def update_generator(
    is_running: bool,
    should_run: bool,
    target_output_kw: float,
    capacity_kw: float = 200.0,
    fuel_remaining: float = 800.0,
    fuel_consumption_rate: float = 8.0,  # L/hour at full load
    dt_hours: float = 0.25,
    runtime_hours: float = 0.0,
    is_warming_up: bool = False,
    warmup_progress: float = 0.0,
    warmup_time_minutes: float = 2.0,
    generator_available: bool = True,
) -> dict:
    """
    Update diesel generator state for one timestep.

    The generator has a warmup period, consumes fuel proportionally
    to output, and cannot exceed capacity.
    """
    if not generator_available:
        return {
            "is_running": False,
            "current_output_kw": 0.0,
            "fuel_remaining_liters": fuel_remaining,
            "fuel_consumed_liters": 0.0,
            "runtime_hours": runtime_hours,
            "is_warming_up": False,
            "warmup_progress": 0.0,
            "status": "UNAVAILABLE",
        }

    if fuel_remaining <= 0:
        return {
            "is_running": False,
            "current_output_kw": 0.0,
            "fuel_remaining_liters": 0.0,
            "fuel_consumed_liters": 0.0,
            "runtime_hours": runtime_hours,
            "is_warming_up": False,
            "warmup_progress": 0.0,
            "status": "NO_FUEL",
        }

    # --- Starting up ---
    if should_run and not is_running and not is_warming_up:
        # Begin warmup
        return {
            "is_running": False,
            "current_output_kw": 0.0,
            "fuel_remaining_liters": fuel_remaining,
            "fuel_consumed_liters": 0.0,
            "runtime_hours": runtime_hours,
            "is_warming_up": True,
            "warmup_progress": 0.0,
            "status": "STARTING",
        }

    # --- Warming up ---
    if is_warming_up:
        if not should_run:
            # Cancelled startup
            return {
                "is_running": False,
                "current_output_kw": 0.0,
                "fuel_remaining_liters": fuel_remaining,
                "fuel_consumed_liters": 0.0,
                "runtime_hours": runtime_hours,
                "is_warming_up": False,
                "warmup_progress": 0.0,
                "status": "STANDBY",
            }

        warmup_increment = (dt_hours * 60) / warmup_time_minutes
        new_warmup = warmup_progress + warmup_increment

        if new_warmup >= 1.0:
            # Warmup complete, generator now running
            actual_output = min(target_output_kw, capacity_kw)
            load_factor = actual_output / capacity_kw if capacity_kw > 0 else 0
            fuel_consumed = fuel_consumption_rate * load_factor * dt_hours
            # Idle fuel consumption during warmup
            fuel_consumed += fuel_consumption_rate * 0.1 * dt_hours

            return {
                "is_running": True,
                "current_output_kw": round(actual_output, 2),
                "fuel_remaining_liters": round(max(0, fuel_remaining - fuel_consumed), 2),
                "fuel_consumed_liters": round(fuel_consumed, 3),
                "runtime_hours": round(runtime_hours + dt_hours, 3),
                "is_warming_up": False,
                "warmup_progress": 1.0,
                "status": "RUNNING",
            }
        else:
            # Still warming up — small idle fuel consumption
            idle_fuel = fuel_consumption_rate * 0.15 * dt_hours
            return {
                "is_running": False,
                "current_output_kw": 0.0,
                "fuel_remaining_liters": round(max(0, fuel_remaining - idle_fuel), 2),
                "fuel_consumed_liters": round(idle_fuel, 3),
                "runtime_hours": runtime_hours,
                "is_warming_up": True,
                "warmup_progress": round(new_warmup, 3),
                "status": "WARMING_UP",
            }

    # --- Running ---
    if is_running and should_run:
        actual_output = min(target_output_kw, capacity_kw)
        actual_output = max(0, actual_output)
        load_factor = actual_output / capacity_kw if capacity_kw > 0 else 0
        # Fuel consumption: proportional to load, with minimum idle consumption
        fuel_consumed = fuel_consumption_rate * max(0.1, load_factor) * dt_hours

        new_fuel = max(0, fuel_remaining - fuel_consumed)

        status = "RUNNING"
        if new_fuel <= 0:
            actual_output = 0
            status = "NO_FUEL"

        return {
            "is_running": new_fuel > 0,
            "current_output_kw": round(actual_output, 2),
            "fuel_remaining_liters": round(new_fuel, 2),
            "fuel_consumed_liters": round(fuel_consumed, 3),
            "runtime_hours": round(runtime_hours + dt_hours, 3),
            "is_warming_up": False,
            "warmup_progress": 0.0,
            "status": status,
        }

    # --- Shutting down ---
    if is_running and not should_run:
        return {
            "is_running": False,
            "current_output_kw": 0.0,
            "fuel_remaining_liters": fuel_remaining,
            "fuel_consumed_liters": 0.0,
            "runtime_hours": runtime_hours,
            "is_warming_up": False,
            "warmup_progress": 0.0,
            "status": "STANDBY",
        }

    # --- Standby ---
    return {
        "is_running": False,
        "current_output_kw": 0.0,
        "fuel_remaining_liters": fuel_remaining,
        "fuel_consumed_liters": 0.0,
        "runtime_hours": runtime_hours,
        "is_warming_up": False,
        "warmup_progress": 0.0,
        "status": "STANDBY",
    }


def calculate_fuel_metrics(
    fuel_remaining: float,
    fuel_consumption_rate: float = 8.0,
    generator_capacity_kw: float = 200.0,
) -> dict:
    """Calculate fuel-related metrics."""
    hours_at_full_load = fuel_remaining / fuel_consumption_rate if fuel_consumption_rate > 0 else 0
    hours_at_half_load = hours_at_full_load * 2

    return {
        "fuel_remaining_liters": round(fuel_remaining, 1),
        "hours_at_full_load": round(hours_at_full_load, 1),
        "hours_at_half_load": round(hours_at_half_load, 1),
        "fuel_level_pct": round((fuel_remaining / 1000.0) * 100, 1),  # assume 1000L tank
    }
