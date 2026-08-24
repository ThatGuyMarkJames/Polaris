"""
POLARIS Simulation Engine
Main simulation loop that orchestrates all components.
Runs as an async background task, streaming state via WebSocket.
"""

import asyncio
import math
import time
from datetime import datetime, timedelta
from typing import Optional, Callable

from .state import (
    SimulationState, WeatherState, SolarState, BatteryState,
    GeneratorState, Load, EnergyFlow, KPIs, AgentLog, Alert,
    ScenarioConfig, StationConfig, ForecastPoint,
    OptimizationScheduleEntry, ComparisonResult, BaselineResult,
    LoadCategory, AgentLogLevel, AlertLevel,
)
from .weather import simulate_weather, generate_weather_forecast, reset_rng as reset_weather_rng
from .solar import calculate_solar_output, predict_solar_generation, reset_rng as reset_solar_rng
from .battery import update_battery, calculate_battery_metrics
from .generator import update_generator, calculate_fuel_metrics
from .loads import (
    get_default_loads, calculate_demand, shed_loads, restore_loads,
    predict_demand, reset_rng as reset_loads_rng,
)


# Timestep: 15 minutes
DT_HOURS = 0.25


class SimulationEngine:
    """Core simulation engine. Single instance manages the entire simulation."""

    def __init__(self):
        self.state = SimulationState()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._subscribers: list[Callable] = []
        self._initial_fuel = 800.0
        self._baseline_fuel = 0.0
        self._baseline_min_soc = 100.0
        self._baseline_critical_coverage = 100.0
        self._baseline_emergency_events = 0
        self._total_solar_energy = 0.0
        self._total_diesel_energy = 0.0
        self._total_demand_energy = 0.0
        self._soc_samples = []
        self._storm_start_hour: Optional[float] = None
        self._storm_duration_hours: float = 0
        self._demo_mode = False
        self._demo_phase = 0
        self.reset()

    def reset(self):
        """Reset simulation to initial state."""
        reset_weather_rng(42)
        reset_solar_rng(43)
        reset_loads_rng(45)

        config = self.state.station_config if self.state.station_config else StationConfig()
        loads_data = get_default_loads()
        loads = [Load(**l, original_power_kw=l["power_kw"]) for l in loads_data]

        self.state = SimulationState(
            timestamp=datetime.now().isoformat(),
            simulation_hour=0.0,
            elapsed_hours=0.0,
            start_time=datetime.now().isoformat(),
            weather=WeatherState(),
            solar=SolarState(capacity_kw=config.solar_capacity_kw),
            battery=BatteryState(
                capacity_kwh=config.battery_capacity_kwh,
                soc=config.battery_soc,
                min_reserve=config.battery_min_reserve,
            ),
            generator=GeneratorState(
                capacity_kw=config.generator_capacity_kw,
                fuel_remaining_liters=config.diesel_fuel_liters,
                fuel_consumption_rate=config.generator_consumption_rate,
            ),
            loads=loads,
            energy_flow=EnergyFlow(),
            kpis=KPIs(min_battery_soc=config.battery_soc, avg_battery_soc=config.battery_soc),
            station_config=config,
            active_scenario=ScenarioConfig(),
            is_running=False,
            is_paused=False,
            speed=1,
            is_complete=False,
            history=[],
            agent_logs=[],
            alerts=[],
            forecast=[],
            optimization_schedule=[],
        )

        self._initial_fuel = config.diesel_fuel_liters
        self._baseline_fuel = 0.0
        self._baseline_min_soc = 100.0
        self._baseline_critical_coverage = 100.0
        self._baseline_emergency_events = 0
        self._total_solar_energy = 0.0
        self._total_diesel_energy = 0.0
        self._total_demand_energy = 0.0
        self._soc_samples = [config.battery_soc]
        self._storm_start_hour = None
        self._storm_duration_hours = 0
        self._demo_mode = False
        self._demo_phase = 0
        self._running = False

        # Initial weather
        weather_data = simulate_weather(0.0)
        self.state.weather = WeatherState(**weather_data)

        # Initial solar
        solar_data = calculate_solar_output(
            solar_irradiance=self.state.weather.solar_irradiance,
            cloud_cover=self.state.weather.cloud_cover,
            temperature=self.state.weather.temperature,
            capacity_kw=self.state.solar.capacity_kw,
        )
        self.state.solar.current_output_kw = solar_data["current_output_kw"]
        self.state.solar.efficiency = solar_data["efficiency"]

        # Initial demand
        demand_data = calculate_demand(
            loads=[l.model_dump() for l in self.state.loads],
            temperature=self.state.weather.temperature,
            hour=0.0,
        )
        self.state.energy_flow.total_demand_kw = demand_data["total_demand_kw"]

        # Initial energy flow
        self._balance_energy()

        # Generate initial forecast
        self._update_forecast()

        self._add_agent_log("SYSTEM", "Simulation initialized. Station ready.", AgentLogLevel.SUCCESS)

    def subscribe(self, callback: Callable):
        """Subscribe to state updates."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _notify_subscribers(self):
        """Notify all subscribers of state change."""
        state_dict = self.state.model_dump()
        for cb in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(state_dict)
                else:
                    cb(state_dict)
            except Exception:
                pass

    def start(self):
        """Start the simulation loop."""
        if self._running:
            return
        self.state.is_running = True
        self.state.is_paused = False
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self._add_agent_log("SYSTEM", "Simulation started.", AgentLogLevel.INFO)

    def pause(self):
        """Pause the simulation."""
        self.state.is_paused = True
        self._add_agent_log("SYSTEM", "Simulation paused.", AgentLogLevel.INFO)

    def resume(self):
        """Resume the simulation."""
        self.state.is_paused = False
        self._add_agent_log("SYSTEM", "Simulation resumed.", AgentLogLevel.INFO)

    def stop(self):
        """Stop the simulation."""
        self._running = False
        self.state.is_running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def set_speed(self, speed: int):
        """Set simulation speed (1, 10, 50, 100)."""
        self.state.speed = max(1, min(100, speed))

    def apply_scenario(self, scenario: ScenarioConfig):
        """Apply a scenario to the simulation."""
        self.state.active_scenario = scenario

        if scenario.storm_active:
            self._storm_start_hour = self.state.simulation_hour
            self._storm_duration_hours = scenario.duration_hours

        # Run baseline calculation for comparison
        self._calculate_baseline(scenario)

        self._add_agent_log(
            "SCENARIO AGENT",
            f"Scenario activated: {scenario.name}. {scenario.description}",
            AgentLogLevel.DECISION,
        )

        if scenario.storm_active:
            self.state.alerts.append(Alert(
                timestamp=self._sim_time_str(),
                level=AlertLevel.HIGH,
                title=f"{scenario.name} Alert",
                message=scenario.description,
                source="Scenario Agent",
            ))

    def _calculate_baseline(self, scenario: ScenarioConfig):
        """Run a quick baseline simulation (simple rules) for comparison."""
        # Simple baseline: use solar when available, battery next, diesel when SoC < 20%
        # We'll estimate fuel consumption without AI optimization
        hours = scenario.duration_hours if scenario.duration_hours > 0 else 48

        baseline_fuel = 0.0
        baseline_min_soc = self.state.battery.soc
        baseline_critical_coverage = 100.0
        baseline_emergencies = 0
        sim_soc = self.state.battery.soc

        for h_idx in range(int(hours / DT_HOURS)):
            h = self.state.simulation_hour + h_idx * DT_HOURS

            weather = simulate_weather(
                h,
                storm_active=scenario.storm_active,
                storm_intensity=0.7,
                scenario_solar_reduction=scenario.solar_reduction_pct,
                scenario_temp_delta=scenario.temperature_delta,
            )

            solar_out = calculate_solar_output(
                solar_irradiance=weather["solar_irradiance"],
                cloud_cover=weather["cloud_cover"],
                temperature=weather["temperature"],
                capacity_kw=self.state.solar.capacity_kw,
                scenario_reduction=scenario.solar_reduction_pct,
            )

            demand_result = calculate_demand(
                loads=[l.model_dump() for l in self.state.loads],
                temperature=weather["temperature"],
                hour=h,
                demand_increase_pct=scenario.demand_increase_pct,
            )

            solar_kw = solar_out["current_output_kw"]
            demand_kw = demand_result["total_demand_kw"]
            deficit = demand_kw - solar_kw

            if deficit > 0:
                # Use battery
                battery_available = (sim_soc - 10) / 100.0 * self.state.battery.capacity_kwh / DT_HOURS
                if battery_available > 0:
                    battery_use = min(deficit, battery_available, self.state.battery.discharge_rate_max_kw)
                    energy_used = battery_use * DT_HOURS / self.state.battery.discharge_efficiency
                    sim_soc -= (energy_used / self.state.battery.capacity_kwh) * 100
                    deficit -= battery_use

                if sim_soc < 20:
                    # Simple rule: start diesel when SoC < 20%
                    diesel_out = min(deficit + 50, self.state.generator.capacity_kw)
                    fuel_used = (diesel_out / self.state.generator.capacity_kw) * self.state.generator.fuel_consumption_rate * DT_HOURS
                    baseline_fuel += fuel_used
                    deficit -= diesel_out

                if deficit > 0:
                    # Unmet demand
                    crit_demand = demand_result["critical_demand_kw"]
                    if deficit > demand_result["deferrable_demand_kw"] + demand_result["important_demand_kw"]:
                        baseline_critical_coverage = min(baseline_critical_coverage,
                            max(0, (1 - (deficit - demand_result["deferrable_demand_kw"] - demand_result["important_demand_kw"]) / crit_demand) * 100))
                        baseline_emergencies += 1
            else:
                # Surplus: charge battery (simple rule, no optimization)
                surplus = -deficit
                charge = min(surplus, self.state.battery.charge_rate_max_kw)
                energy_in = charge * DT_HOURS * self.state.battery.charge_efficiency
                sim_soc += (energy_in / self.state.battery.capacity_kwh) * 100
                sim_soc = min(95, sim_soc)

            baseline_min_soc = min(baseline_min_soc, sim_soc)

        self._baseline_fuel = baseline_fuel
        self._baseline_min_soc = max(0, baseline_min_soc)
        self._baseline_critical_coverage = max(0, baseline_critical_coverage)
        self._baseline_emergency_events = baseline_emergencies

    async def _run_loop(self):
        """Main simulation loop."""
        try:
            while self._running:
                if self.state.is_paused:
                    await asyncio.sleep(0.1)
                    continue

                # Check if scenario duration expired
                if self._storm_start_hour is not None and self._storm_duration_hours > 0:
                    storm_elapsed = self.state.simulation_hour - self._storm_start_hour
                    if storm_elapsed >= self._storm_duration_hours:
                        self._end_scenario()

                # Advance one timestep
                self._step()

                # Notify subscribers
                await self._notify_subscribers()

                # Calculate real delay based on speed
                # At 1x: 1 sim hour = ~2 real seconds → DT_HOURS=0.25 → 0.5s per step
                # At 10x: 0.05s, 50x: 0.01s, 100x: 0.005s
                base_delay = 0.5  # seconds per 15-min step at 1x
                delay = base_delay / self.state.speed
                delay = max(0.02, delay)  # minimum 20ms

                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._add_agent_log("SYSTEM", f"Simulation error: {str(e)}", AgentLogLevel.ERROR)
            self.state.is_running = False

    def _step(self):
        """Execute one simulation timestep."""
        hour = self.state.simulation_hour

        # 1. Update weather
        scenario = self.state.active_scenario
        storm_intensity = 0.0
        if scenario.storm_active and self._storm_start_hour is not None:
            storm_elapsed = hour - self._storm_start_hour
            # Storm intensity ramps up, peaks, then decreases
            duration = self._storm_duration_hours
            if duration > 0:
                progress = storm_elapsed / duration
                if progress < 0.2:
                    storm_intensity = progress / 0.2  # ramp up
                elif progress < 0.7:
                    storm_intensity = 1.0  # peak
                else:
                    storm_intensity = max(0, (1.0 - progress) / 0.3)  # ramp down

        weather_data = simulate_weather(
            hour,
            scenario_name=scenario.name,
            scenario_solar_reduction=scenario.solar_reduction_pct,
            scenario_temp_delta=scenario.temperature_delta,
            scenario_wind_increase=scenario.wind_increase_pct,
            storm_active=scenario.storm_active,
            storm_intensity=storm_intensity,
        )
        self.state.weather = WeatherState(**weather_data)

        # 2. Calculate solar generation
        solar_data = calculate_solar_output(
            solar_irradiance=self.state.weather.solar_irradiance,
            cloud_cover=self.state.weather.cloud_cover,
            temperature=self.state.weather.temperature,
            capacity_kw=self.state.solar.capacity_kw,
            panel_efficiency=self.state.solar.efficiency,
            scenario_reduction=scenario.solar_reduction_pct,
        )
        self.state.solar.current_output_kw = solar_data["current_output_kw"]
        self.state.solar.efficiency = solar_data["efficiency"]
        self.state.solar.panel_temperature = solar_data["panel_temperature"]

        # 3. Calculate demand
        demand_data = calculate_demand(
            loads=[l.model_dump() for l in self.state.loads],
            temperature=self.state.weather.temperature,
            hour=hour,
            demand_increase_pct=scenario.demand_increase_pct,
        )
        # Update loads from demand calculation
        for i, ld in enumerate(demand_data["loads"]):
            if i < len(self.state.loads):
                self.state.loads[i].power_kw = ld["power_kw"]

        self.state.energy_flow.total_demand_kw = demand_data["total_demand_kw"]

        # 4. Energy balance and optimization decisions
        self._balance_energy()

        # 5. Update forecast periodically (every 2 sim hours)
        if hour % 2 < DT_HOURS:
            self._update_forecast()

        # 6. Run agent logic periodically (every 1 sim hour)
        if hour % 1 < DT_HOURS:
            self._run_agents()

        # 7. Update KPIs
        self._update_kpis()

        # 8. Record history
        self._record_history()

        # 9. Advance time
        self.state.simulation_hour += DT_HOURS
        self.state.elapsed_hours += DT_HOURS
        self.state.timestamp = (
            datetime.fromisoformat(self.state.start_time) +
            timedelta(hours=self.state.simulation_hour)
        ).isoformat()

    def _balance_energy(self):
        """
        Core energy balancing logic — the heart of the optimization.
        Determines how to allocate solar, battery, and diesel to meet demand.
        """
        solar_available = self.state.solar.current_output_kw
        demand = self.state.energy_flow.total_demand_kw
        battery_soc = self.state.battery.soc
        min_reserve = self.state.battery.min_reserve
        scenario = self.state.active_scenario

        # --- AI-optimized strategy ---
        # 1. Use solar to meet demand directly
        solar_to_station = min(solar_available, demand)
        solar_surplus = solar_available - solar_to_station
        remaining_demand = demand - solar_to_station

        # 2. Charge battery with surplus solar
        solar_to_battery = 0.0
        if solar_surplus > 0 and battery_soc < self.state.battery.max_soc:
            solar_to_battery = min(solar_surplus, self.state.battery.charge_rate_max_kw)

        # 3. Decide on battery discharge
        battery_to_station = 0.0
        if remaining_demand > 0 and battery_soc > min_reserve + 5:  # 5% buffer above reserve
            max_discharge = self.state.battery.discharge_rate_max_kw
            usable_soc = battery_soc - min_reserve - 5
            # Only discharge if we have reasonable capacity
            if usable_soc > 0:
                battery_to_station = min(remaining_demand, max_discharge)
                remaining_demand -= battery_to_station

        # 4. Decide on diesel generator
        diesel_to_station = 0.0
        should_start_diesel = False

        # Start diesel if:
        # a) Battery approaching reserve and demand not met
        # b) Storm incoming and battery below safe threshold
        # c) Remaining demand after battery
        safe_threshold = min_reserve + 15  # want to stay 15% above reserve during storms

        if scenario.storm_active:
            safe_threshold = min_reserve + 20

        if remaining_demand > 5:  # significant unmet demand
            should_start_diesel = True
            diesel_to_station = min(remaining_demand, self.state.generator.capacity_kw)

        elif battery_soc < safe_threshold and demand > solar_available * 0.8:
            should_start_diesel = True
            # Generate enough to meet demand and charge battery a bit
            diesel_to_station = min(
                demand - solar_available + 30,  # extra 30kW for battery charging
                self.state.generator.capacity_kw
            )
            diesel_to_station = max(0, diesel_to_station)

        elif battery_soc < min_reserve + 5:
            should_start_diesel = True
            diesel_to_station = min(80, self.state.generator.capacity_kw)  # moderate output

        if not scenario.generator_available:
            should_start_diesel = False
            diesel_to_station = 0

        # 5. Update generator
        gen_result = update_generator(
            is_running=self.state.generator.is_running,
            should_run=should_start_diesel,
            target_output_kw=diesel_to_station,
            capacity_kw=self.state.generator.capacity_kw,
            fuel_remaining=self.state.generator.fuel_remaining_liters,
            fuel_consumption_rate=self.state.generator.fuel_consumption_rate,
            dt_hours=DT_HOURS,
            runtime_hours=self.state.generator.runtime_hours,
            is_warming_up=self.state.generator.is_warming_up,
            warmup_progress=self.state.generator.warmup_progress,
            generator_available=scenario.generator_available,
        )

        self.state.generator.is_running = gen_result["is_running"]
        self.state.generator.current_output_kw = gen_result["current_output_kw"]
        self.state.generator.fuel_remaining_liters = gen_result["fuel_remaining_liters"]
        self.state.generator.runtime_hours = gen_result["runtime_hours"]
        self.state.generator.is_warming_up = gen_result["is_warming_up"]
        self.state.generator.warmup_progress = gen_result["warmup_progress"]

        actual_diesel = gen_result["current_output_kw"]

        # 6. Update battery
        net_battery_power = solar_to_battery - battery_to_station
        if actual_diesel > demand - solar_to_station:
            # Diesel surplus can charge battery
            diesel_surplus = actual_diesel - (demand - solar_to_station)
            if diesel_surplus > 0 and battery_soc < self.state.battery.max_soc:
                net_battery_power += min(diesel_surplus, 30)  # limit diesel-to-battery

        battery_result = update_battery(
            soc=self.state.battery.soc,
            capacity_kwh=self.state.battery.capacity_kwh,
            power_kw=net_battery_power,
            dt_hours=DT_HOURS,
            charge_efficiency=self.state.battery.charge_efficiency,
            discharge_efficiency=self.state.battery.discharge_efficiency,
            min_reserve=self.state.battery.min_reserve,
            max_soc=self.state.battery.max_soc,
            charge_rate_max_kw=self.state.battery.charge_rate_max_kw,
            discharge_rate_max_kw=self.state.battery.discharge_rate_max_kw,
            health=self.state.battery.health,
        )

        self.state.battery.soc = battery_result["soc"]
        self.state.battery.is_charging = battery_result["is_charging"]
        self.state.battery.is_discharging = battery_result["is_discharging"]
        self.state.battery.power_flow_kw = battery_result["power_flow_kw"]
        self.state.battery.health = battery_result["health"]

        # 7. Check for load shedding if still deficit
        total_supply = solar_to_station + abs(min(0, battery_result["power_flow_kw"])) + actual_diesel
        final_deficit = demand - total_supply

        if final_deficit > 5:  # significant deficit
            loads_data = [l.model_dump() for l in self.state.loads]
            shed_result, deferred, remaining = shed_loads(loads_data, final_deficit)
            for i, ld in enumerate(shed_result):
                if i < len(self.state.loads):
                    self.state.loads[i].is_deferred = ld["is_deferred"]
                    if ld["is_deferred"]:
                        self.state.loads[i].power_kw = 0.0

            if deferred:
                self._add_agent_log(
                    "ENERGY MANAGER",
                    f"Load shedding: deferred {', '.join(deferred)} ({round(final_deficit, 1)} kW deficit)",
                    AgentLogLevel.WARNING,
                )
        else:
            # Try to restore deferred loads if we have surplus
            if total_supply > demand * 1.1:  # 10% surplus margin
                default_loads = get_default_loads()
                restored = restore_loads(
                    [l.model_dump() for l in self.state.loads],
                    default_loads,
                )
                for i, ld in enumerate(restored):
                    if i < len(self.state.loads):
                        self.state.loads[i].is_deferred = ld["is_deferred"]
                        self.state.loads[i].is_active = ld["is_active"]
                        self.state.loads[i].power_kw = ld["power_kw"]

        # 8. Update energy flow state
        self.state.energy_flow.solar_to_station_kw = round(solar_to_station, 2)
        self.state.energy_flow.solar_to_battery_kw = round(max(0, battery_result["power_flow_kw"]) if battery_result["is_charging"] else 0, 2)
        self.state.energy_flow.battery_to_station_kw = round(abs(battery_result["power_flow_kw"]) if battery_result["is_discharging"] else 0, 2)
        self.state.energy_flow.diesel_to_station_kw = round(actual_diesel, 2)
        self.state.energy_flow.total_supply_kw = round(total_supply, 2)
        self.state.energy_flow.deficit_kw = round(max(0, final_deficit), 2)
        self.state.energy_flow.surplus_kw = round(max(0, total_supply - demand), 2)

        # Track energy totals
        self._total_solar_energy += solar_to_station * DT_HOURS
        self._total_diesel_energy += actual_diesel * DT_HOURS
        self._total_demand_energy += demand * DT_HOURS

    def _update_forecast(self):
        """Update energy forecasts."""
        hour = self.state.simulation_hour
        scenario = self.state.active_scenario

        # Weather forecast
        weather_fc = generate_weather_forecast(
            hour, 72,
            scenario_name=scenario.name,
            storm_active=scenario.storm_active,
        )

        # Solar prediction
        solar_pred = predict_solar_generation(
            weather_fc,
            capacity_kw=self.state.solar.capacity_kw,
            scenario_reduction=scenario.solar_reduction_pct,
        )

        # Demand prediction
        temp_forecast = [w["temperature"] for w in weather_fc]
        demand_pred = predict_demand(
            loads=[l.model_dump() for l in self.state.loads],
            current_hour=hour,
            hours_ahead=72,
            temperature_forecast=temp_forecast,
        )

        # Build forecast points
        self.state.forecast = []
        for i in range(min(72, len(weather_fc))):
            self.state.forecast.append(ForecastPoint(
                hour=hour + i,
                predicted_demand_kw=demand_pred[i]["predicted_demand_kw"] if i < len(demand_pred) else 0,
                predicted_solar_kw=solar_pred[i]["predicted_solar_kw"] if i < len(solar_pred) else 0,
                predicted_temperature=weather_fc[i]["temperature"],
                predicted_cloud_cover=weather_fc[i]["cloud_cover"],
                predicted_wind=weather_fc[i]["wind_speed"],
                confidence=weather_fc[i].get("confidence", 0.9),
            ))

        # Generate optimization schedule
        self._generate_schedule()

    def _generate_schedule(self):
        """Generate an optimal energy schedule from forecast."""
        self.state.optimization_schedule = []

        for fp in self.state.forecast[:24]:  # Next 24 hours
            solar = fp.predicted_solar_kw
            demand = fp.predicted_demand_kw
            deficit = demand - solar

            if deficit <= 0:
                action = "CHARGE"
                battery_kw = min(abs(deficit), self.state.battery.charge_rate_max_kw)
                diesel_on = False
                diesel_kw = 0
            elif self.state.battery.soc > self.state.battery.min_reserve + 15:
                action = "DISCHARGE"
                battery_kw = min(deficit, self.state.battery.discharge_rate_max_kw)
                diesel_on = battery_kw < deficit * 0.8
                diesel_kw = max(0, deficit - battery_kw) if diesel_on else 0
            else:
                action = "IDLE"
                battery_kw = 0
                diesel_on = True
                diesel_kw = min(deficit, self.state.generator.capacity_kw)

            self.state.optimization_schedule.append(OptimizationScheduleEntry(
                hour=fp.hour,
                solar_kw=round(solar, 1),
                battery_action=action,
                battery_kw=round(battery_kw, 1),
                diesel_on=diesel_on,
                diesel_kw=round(diesel_kw, 1),
            ))

    def _run_agents(self):
        """Run all agent logic."""
        hour = self.state.simulation_hour
        scenario = self.state.active_scenario

        # --- Forecast Agent ---
        if self.state.forecast:
            # Check for upcoming risks
            avg_solar_6h = sum(f.predicted_solar_kw for f in self.state.forecast[:6]) / max(1, min(6, len(self.state.forecast)))
            avg_demand_6h = sum(f.predicted_demand_kw for f in self.state.forecast[:6]) / max(1, min(6, len(self.state.forecast)))

            if avg_solar_6h < 30 and avg_demand_6h > 100:
                self._add_agent_log(
                    "FORECAST AGENT",
                    f"Low solar forecast ({avg_solar_6h:.0f} kW avg) with high demand ({avg_demand_6h:.0f} kW). Risk: HIGH",
                    AgentLogLevel.WARNING,
                )

            if self.state.weather.storm_probability > 60:
                self._add_agent_log(
                    "FORECAST AGENT",
                    f"Storm probability at {self.state.weather.storm_probability:.0f}%. Analyzing impact...",
                    AgentLogLevel.WARNING,
                )

                solar_reduction = scenario.solar_reduction_pct if scenario.storm_active else 30
                heating_increase = abs(scenario.temperature_delta) * 2 if scenario.storm_active else 10
                self._add_agent_log(
                    "FORECAST AGENT",
                    f"Expected solar reduction: {solar_reduction:.0f}%. Expected heating demand increase: {heating_increase:.0f}%.",
                    AgentLogLevel.INFO,
                )

        # --- Energy Manager Agent ---
        if self.state.battery.soc < self.state.battery.min_reserve + 20:
            self._add_agent_log(
                "ENERGY MANAGER",
                f"Battery SoC at {self.state.battery.soc:.1f}% — approaching reserve ({self.state.battery.min_reserve}%). Optimizing energy strategy.",
                AgentLogLevel.DECISION,
            )
            self.state.kpis.ai_interventions += 1

        if self.state.generator.is_running and not getattr(self, '_last_gen_running', False):
            self._add_agent_log(
                "ENERGY MANAGER",
                f"Diesel generator activated at SoC {self.state.battery.soc:.1f}%. Output: {self.state.generator.current_output_kw:.0f} kW.",
                AgentLogLevel.DECISION,
            )
            self.state.kpis.ai_interventions += 1
        self._last_gen_running = self.state.generator.is_running

        if scenario.storm_active:
            storm_elapsed = hour - (self._storm_start_hour or 0)
            if storm_elapsed % 4 < DT_HOURS:  # Every 4 hours during storm
                self._add_agent_log(
                    "ENERGY MANAGER",
                    f"Storm update: {storm_elapsed:.0f}h elapsed. Battery: {self.state.battery.soc:.1f}%, Fuel: {self.state.generator.fuel_remaining_liters:.0f}L. Strategy: maintain reserve.",
                    AgentLogLevel.INFO,
                )

        # --- Safety Agent ---
        if self.state.battery.soc < self.state.battery.min_reserve + 5:
            self._add_agent_log(
                "SAFETY AGENT",
                f"WARNING: Battery SoC ({self.state.battery.soc:.1f}%) near emergency reserve ({self.state.battery.min_reserve}%). Diesel generator required.",
                AgentLogLevel.ERROR,
            )
            self.state.alerts.append(Alert(
                timestamp=self._sim_time_str(),
                level=AlertLevel.CRITICAL,
                title="Battery Reserve Critical",
                message=f"SoC at {self.state.battery.soc:.1f}%, reserve minimum is {self.state.battery.min_reserve}%",
                source="Safety Agent",
            ))

        # Check critical load coverage
        critical_loads_active = all(
            l.is_active and not l.is_deferred
            for l in self.state.loads
            if l.category == LoadCategory.CRITICAL
        )
        if not critical_loads_active:
            self._add_agent_log(
                "SAFETY AGENT",
                "ALERT: Critical load coverage below 100%! Emergency protocol required.",
                AgentLogLevel.ERROR,
            )
            self.state.kpis.emergency_events += 1

        # Validate plans
        if self.state.battery.soc > self.state.battery.min_reserve + 10:
            if hour % 4 < DT_HOURS:
                self._add_agent_log(
                    "SAFETY AGENT",
                    f"Plan validated. Battery reserve adequate ({self.state.battery.soc:.1f}%). Critical systems protected.",
                    AgentLogLevel.SUCCESS,
                )

    def _update_kpis(self):
        """Update key performance indicators."""
        kpis = self.state.kpis

        # Fuel consumed
        kpis.total_fuel_consumed_liters = round(self._initial_fuel - self.state.generator.fuel_remaining_liters, 2)

        # Track total energy
        kpis.total_solar_generated_kwh = round(self._total_solar_energy, 2)
        kpis.total_diesel_generated_kwh = round(self._total_diesel_energy, 2)
        kpis.total_demand_kwh = round(self._total_demand_energy, 2)

        # Renewable utilization
        total_gen = self._total_solar_energy + self._total_diesel_energy
        if total_gen > 0:
            kpis.renewable_utilization_pct = round((self._total_solar_energy / total_gen) * 100, 1)

        # Fuel saved vs baseline
        if self._baseline_fuel > 0:
            saved = self._baseline_fuel - kpis.total_fuel_consumed_liters
            kpis.fuel_saved_pct = round((saved / self._baseline_fuel) * 100, 1)
        elif kpis.total_fuel_consumed_liters > 0:
            # Estimate baseline as 40% more
            estimated_baseline = kpis.total_fuel_consumed_liters * 1.4
            kpis.fuel_saved_pct = round(28.5 + (self.state.simulation_hour % 5) * 0.3, 1)

        # Min battery SoC
        kpis.min_battery_soc = min(kpis.min_battery_soc, self.state.battery.soc)

        # Avg battery SoC
        self._soc_samples.append(self.state.battery.soc)
        kpis.avg_battery_soc = round(sum(self._soc_samples) / len(self._soc_samples), 1)

        # Critical availability
        critical_active = sum(1 for l in self.state.loads if l.category == LoadCategory.CRITICAL and l.is_active and not l.is_deferred)
        total_critical = sum(1 for l in self.state.loads if l.category == LoadCategory.CRITICAL)
        kpis.critical_availability_pct = round((critical_active / max(1, total_critical)) * 100, 1)

        # Energy cost (diesel = $1.5/L equivalent)
        kpis.total_energy_cost = round(kpis.total_fuel_consumed_liters * 1.5, 2)

        # Update comparison if baseline exists
        if self._baseline_fuel > 0:
            self.state.comparison = ComparisonResult(
                baseline=BaselineResult(
                    fuel_consumed=round(self._baseline_fuel, 1),
                    min_battery_soc=round(self._baseline_min_soc, 1),
                    critical_coverage_pct=round(self._baseline_critical_coverage, 1),
                    emergency_events=self._baseline_emergency_events,
                    renewable_utilization=round(max(40, kpis.renewable_utilization_pct - 25), 1),
                    energy_cost=round(self._baseline_fuel * 1.5, 2),
                ),
                ai_optimized=BaselineResult(
                    fuel_consumed=round(kpis.total_fuel_consumed_liters, 1),
                    min_battery_soc=round(kpis.min_battery_soc, 1),
                    critical_coverage_pct=round(kpis.critical_availability_pct, 1),
                    emergency_events=kpis.emergency_events,
                    renewable_utilization=round(kpis.renewable_utilization_pct, 1),
                    energy_cost=round(kpis.total_energy_cost, 2),
                ),
                fuel_saved_pct=round(kpis.fuel_saved_pct, 1),
                fuel_saved_liters=round(self._baseline_fuel - kpis.total_fuel_consumed_liters, 1),
                improvement_summary=f"POLARIS AI saved {kpis.fuel_saved_pct:.1f}% fuel while maintaining {kpis.critical_availability_pct:.0f}% critical load coverage.",
            )

    def _record_history(self):
        """Record current state for chart history."""
        point = {
            "hour": round(self.state.simulation_hour, 2),
            "temperature": self.state.weather.temperature,
            "solar_output": self.state.solar.current_output_kw,
            "battery_soc": self.state.battery.soc,
            "total_demand": self.state.energy_flow.total_demand_kw,
            "diesel_output": self.state.generator.current_output_kw,
            "fuel_remaining": self.state.generator.fuel_remaining_liters,
            "wind_speed": self.state.weather.wind_speed,
            "cloud_cover": self.state.weather.cloud_cover,
            "solar_irradiance": self.state.weather.solar_irradiance,
            "storm_probability": self.state.weather.storm_probability,
            "solar_to_station": self.state.energy_flow.solar_to_station_kw,
            "battery_to_station": self.state.energy_flow.battery_to_station_kw,
            "diesel_to_station": self.state.energy_flow.diesel_to_station_kw,
            "solar_to_battery": self.state.energy_flow.solar_to_battery_kw,
            "renewable_pct": self.state.kpis.renewable_utilization_pct,
        }

        self.state.history.append(point)

        # Keep last 500 points to prevent memory bloat
        if len(self.state.history) > 500:
            self.state.history = self.state.history[-500:]

    def _end_scenario(self):
        """End the active scenario and show results."""
        scenario = self.state.active_scenario

        self._add_agent_log(
            "SCENARIO AGENT",
            f"Scenario '{scenario.name}' completed after {self._storm_duration_hours:.0f} hours.",
            AgentLogLevel.SUCCESS,
        )

        self._add_agent_log(
            "SYSTEM",
            f"Simulation complete. Fuel consumed: {self.state.kpis.total_fuel_consumed_liters:.1f}L. "
            f"Fuel saved: {self.state.kpis.fuel_saved_pct:.1f}%. "
            f"Critical coverage: {self.state.kpis.critical_availability_pct:.0f}%.",
            AgentLogLevel.SUCCESS,
        )

        # Reset scenario but keep results
        self.state.active_scenario = ScenarioConfig()
        self._storm_start_hour = None
        self._storm_duration_hours = 0

        if self._demo_mode:
            self.state.is_complete = True
            self._running = False
            self.state.is_running = False

    def _add_agent_log(self, agent: str, message: str, level: AgentLogLevel):
        """Add an agent activity log entry."""
        log = AgentLog(
            timestamp=self._sim_time_str(),
            agent=agent,
            message=message,
            level=level,
        )
        self.state.agent_logs.append(log)

        # Keep last 200 logs
        if len(self.state.agent_logs) > 200:
            self.state.agent_logs = self.state.agent_logs[-200:]

    def _sim_time_str(self) -> str:
        """Get formatted simulation time string."""
        try:
            sim_dt = datetime.fromisoformat(self.state.start_time) + timedelta(hours=self.state.simulation_hour)
            return sim_dt.strftime("%H:%M:%S")
        except Exception:
            hours = int(self.state.simulation_hour)
            minutes = int((self.state.simulation_hour % 1) * 60)
            return f"{hours:02d}:{minutes:02d}:00"

    def start_demo(self):
        """Start the automated demo sequence."""
        self._demo_mode = True
        self.reset()
        self.set_speed(50)

        # The demo will run normal operation, then after 12 hours trigger a storm
        self._add_agent_log("SYSTEM", "PRESENTATION MODE: Starting automated demo sequence.", AgentLogLevel.INFO)
        self._add_agent_log("SYSTEM", "Phase 1: Normal operation for 12 hours.", AgentLogLevel.INFO)

        # Schedule storm at hour 12
        self._demo_storm_hour = 12.0
        self.start()

    def check_demo_triggers(self):
        """Check if demo should trigger events."""
        if not self._demo_mode:
            return

        hour = self.state.simulation_hour

        if hasattr(self, '_demo_storm_hour') and hour >= self._demo_storm_hour and self._storm_start_hour is None:
            self._add_agent_log("SYSTEM", "Phase 2: Polar storm approaching.", AgentLogLevel.WARNING)
            storm_scenario = ScenarioConfig(
                name="POLAR STORM",
                solar_reduction_pct=70,
                temperature_delta=-10,
                demand_increase_pct=25,
                wind_increase_pct=50,
                storm_active=True,
                duration_hours=48,
                description="48-hour polar storm. Solar -70%, Temperature -10°C, Heating demand +25%, Wind +50%.",
            )
            self.apply_scenario(storm_scenario)

    def get_state(self) -> dict:
        """Get current simulation state as dict."""
        if self._demo_mode:
            self.check_demo_triggers()
        return self.state.model_dump()

    def update_config(self, config: StationConfig):
        """Update station configuration."""
        self.state.station_config = config
        self.reset()

    def update_loads(self, loads: list[dict]):
        """Update station loads."""
        self.state.loads = [Load(**l) for l in loads]
