"""
POLARIS Simulation State Models
Central state models that drive the entire simulation.
All components read from and write to these models.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class LoadCategory(str, Enum):
    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    DEFERRABLE = "DEFERRABLE"


class AgentLogLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    DECISION = "DECISION"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    SUCCESS = "SUCCESS"


class WeatherState(BaseModel):
    temperature: float = -18.0
    wind_speed: float = 35.0
    solar_irradiance: float = 120.0
    cloud_cover: float = 80.0
    snowfall: float = 0.0
    storm_probability: float = 10.0
    weather_severity: str = "NORMAL"


class SolarState(BaseModel):
    capacity_kw: float = 200.0
    current_output_kw: float = 0.0
    efficiency: float = 0.85
    panel_temperature: float = -15.0


class BatteryState(BaseModel):
    capacity_kwh: float = 500.0
    soc: float = 72.0  # percentage
    min_reserve: float = 30.0  # percentage
    max_soc: float = 95.0  # percentage
    charge_rate_max_kw: float = 100.0
    discharge_rate_max_kw: float = 150.0
    is_charging: bool = False
    is_discharging: bool = False
    power_flow_kw: float = 0.0  # positive=charging, negative=discharging
    health: float = 98.0
    charge_efficiency: float = 0.92
    discharge_efficiency: float = 0.95


class GeneratorState(BaseModel):
    capacity_kw: float = 200.0
    is_running: bool = False
    current_output_kw: float = 0.0
    fuel_remaining_liters: float = 800.0
    fuel_consumption_rate: float = 8.0  # L/hour at full load
    runtime_hours: float = 0.0
    warmup_time_minutes: float = 2.0
    is_warming_up: bool = False
    warmup_progress: float = 0.0  # 0-1


class Load(BaseModel):
    name: str
    power_kw: float
    category: LoadCategory
    is_active: bool = True
    is_deferred: bool = False
    original_power_kw: float = 0.0  # for tracking reductions

    def model_post_init(self, __context):
        if self.original_power_kw == 0.0:
            self.original_power_kw = self.power_kw


class EnergyFlow(BaseModel):
    solar_to_station_kw: float = 0.0
    solar_to_battery_kw: float = 0.0
    battery_to_station_kw: float = 0.0
    diesel_to_station_kw: float = 0.0
    total_supply_kw: float = 0.0
    total_demand_kw: float = 0.0
    deficit_kw: float = 0.0
    surplus_kw: float = 0.0


class ForecastPoint(BaseModel):
    hour: float = 0.0
    predicted_demand_kw: float = 0.0
    predicted_solar_kw: float = 0.0
    predicted_temperature: float = 0.0
    predicted_cloud_cover: float = 0.0
    predicted_wind: float = 0.0
    confidence: float = 0.95


class OptimizationScheduleEntry(BaseModel):
    hour: float = 0.0
    solar_kw: float = 0.0
    battery_action: str = "IDLE"  # CHARGE, DISCHARGE, IDLE
    battery_kw: float = 0.0
    diesel_on: bool = False
    diesel_kw: float = 0.0
    load_shedding: list[str] = []
    notes: str = ""


class AgentLog(BaseModel):
    timestamp: str = ""
    agent: str = ""
    message: str = ""
    level: AgentLogLevel = AgentLogLevel.INFO
    details: Optional[str] = None


class Alert(BaseModel):
    timestamp: str = ""
    level: AlertLevel = AlertLevel.INFO
    title: str = ""
    message: str = ""
    source: str = ""


class KPIs(BaseModel):
    total_fuel_consumed_liters: float = 0.0
    fuel_saved_pct: float = 0.0
    renewable_utilization_pct: float = 0.0
    critical_availability_pct: float = 100.0
    min_battery_soc: float = 72.0
    total_solar_generated_kwh: float = 0.0
    total_diesel_generated_kwh: float = 0.0
    total_demand_kwh: float = 0.0
    ai_interventions: int = 0
    emergency_events: int = 0
    avg_battery_soc: float = 72.0
    total_energy_cost: float = 0.0


class ScenarioConfig(BaseModel):
    name: str = "NORMAL"
    solar_reduction_pct: float = 0.0
    temperature_delta: float = 0.0
    demand_increase_pct: float = 0.0
    wind_increase_pct: float = 0.0
    generator_available: bool = True
    battery_health: float = 100.0
    duration_hours: float = 0.0
    storm_active: bool = False
    description: str = "Normal operating conditions"


class StationConfig(BaseModel):
    name: str = "Antarctic Research Station Alpha"
    latitude: float = -69.0
    longitude: float = 76.0
    current_demand_kw: float = 120.0
    solar_capacity_kw: float = 200.0
    battery_capacity_kwh: float = 500.0
    battery_soc: float = 72.0
    battery_min_reserve: float = 30.0
    generator_capacity_kw: float = 200.0
    diesel_fuel_liters: float = 800.0
    generator_consumption_rate: float = 8.0


class BaselineResult(BaseModel):
    fuel_consumed: float = 0.0
    min_battery_soc: float = 0.0
    critical_coverage_pct: float = 0.0
    emergency_events: int = 0
    renewable_utilization: float = 0.0
    energy_cost: float = 0.0


class ComparisonResult(BaseModel):
    baseline: BaselineResult = BaselineResult()
    ai_optimized: BaselineResult = BaselineResult()
    fuel_saved_pct: float = 0.0
    fuel_saved_liters: float = 0.0
    improvement_summary: str = ""


class SimulationState(BaseModel):
    """Master state object. Single source of truth for the entire simulation."""
    # Time
    timestamp: str = ""
    simulation_hour: float = 0.0
    elapsed_hours: float = 0.0
    start_time: str = ""

    # Sub-states
    weather: WeatherState = WeatherState()
    solar: SolarState = SolarState()
    battery: BatteryState = BatteryState()
    generator: GeneratorState = GeneratorState()
    loads: list[Load] = []
    energy_flow: EnergyFlow = EnergyFlow()
    kpis: KPIs = KPIs()

    # Intelligence
    forecast: list[ForecastPoint] = []
    optimization_schedule: list[OptimizationScheduleEntry] = []
    agent_logs: list[AgentLog] = []
    alerts: list[Alert] = []

    # Scenario
    active_scenario: ScenarioConfig = ScenarioConfig()
    station_config: StationConfig = StationConfig()

    # Comparison
    comparison: Optional[ComparisonResult] = None
    baseline_running: bool = False

    # Control
    is_running: bool = False
    is_paused: bool = False
    speed: int = 1
    is_complete: bool = False

    # History for charts
    history: list[dict] = []
