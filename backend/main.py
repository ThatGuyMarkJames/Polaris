"""
POLARIS FastAPI Main Application
Entry point for the backend server.
Handles REST endpoints, WebSocket, and CORS.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.simulation.engine import SimulationEngine
from backend.simulation.state import StationConfig, ScenarioConfig, Load
from backend.scenarios.presets import get_scenario, get_all_scenarios


# --- Singleton simulation engine ---
engine = SimulationEngine()

# --- WebSocket connection manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)


ws_manager = ConnectionManager()


# --- Subscribe engine to broadcast ---
async def on_state_update(state: dict):
    await ws_manager.broadcast(state)

engine.subscribe(on_state_update)


# --- FastAPI app ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    engine.stop()


app = FastAPI(
    title="POLARIS API",
    description="AI-Powered Polar Energy Digital Twin",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REST ENDPOINTS
# ============================================================

@app.get("/api/station")
async def get_station():
    """Get current station state."""
    state = engine.get_state()
    return {
        "station": state["station_config"],
        "status": "OPERATIONAL" if not state.get("is_complete") else "SIMULATION COMPLETE",
        "simulation_hour": state["simulation_hour"],
        "is_running": state["is_running"],
        "is_paused": state["is_paused"],
        "speed": state["speed"],
    }


@app.get("/api/weather")
async def get_weather():
    """Get current weather state."""
    state = engine.get_state()
    return state["weather"]


@app.get("/api/forecast")
async def get_forecast(hours: int = Query(72, ge=1, le=72)):
    """Get energy forecast."""
    state = engine.get_state()
    forecast = state.get("forecast", [])
    return {
        "forecast": forecast[:hours],
        "schedule": state.get("optimization_schedule", [])[:hours],
    }


@app.get("/api/battery")
async def get_battery():
    """Get battery state."""
    state = engine.get_state()
    return state["battery"]


@app.get("/api/generator")
async def get_generator():
    """Get diesel generator state."""
    state = engine.get_state()
    return state["generator"]


@app.get("/api/loads")
async def get_loads():
    """Get station loads."""
    state = engine.get_state()
    return {"loads": state["loads"]}


@app.get("/api/optimization")
async def get_optimization():
    """Get optimization results."""
    state = engine.get_state()
    return {
        "schedule": state.get("optimization_schedule", []),
        "kpis": state["kpis"],
        "comparison": state.get("comparison"),
    }


@app.get("/api/state")
async def get_full_state():
    """Get complete simulation state."""
    return engine.get_state()


@app.get("/api/history")
async def get_history():
    """Get simulation history for charts."""
    state = engine.get_state()
    return {"history": state.get("history", [])}


# --- Simulation Control ---

class SimulationCommand(BaseModel):
    speed: Optional[int] = None


@app.post("/api/simulation/start")
async def start_simulation(cmd: Optional[SimulationCommand] = None):
    """Start or resume simulation."""
    if cmd and cmd.speed:
        engine.set_speed(cmd.speed)

    if engine.state.is_paused:
        engine.resume()
    else:
        engine.start()
    return {"status": "running", "speed": engine.state.speed}


@app.post("/api/simulation/pause")
async def pause_simulation():
    """Pause simulation."""
    engine.pause()
    return {"status": "paused"}


@app.post("/api/simulation/reset")
async def reset_simulation():
    """Reset simulation."""
    engine.stop()
    engine.reset()
    return {"status": "reset"}


@app.post("/api/simulation/speed")
async def set_speed(cmd: SimulationCommand):
    """Set simulation speed."""
    if cmd.speed:
        engine.set_speed(cmd.speed)
    return {"speed": engine.state.speed}


# --- Scenario ---

@app.get("/api/scenarios")
async def list_scenarios():
    """List available scenarios."""
    return {"scenarios": get_all_scenarios()}


class ScenarioRequest(BaseModel):
    scenario_name: str
    custom_config: Optional[dict] = None


@app.post("/api/simulation/scenario")
async def apply_scenario(req: ScenarioRequest):
    """Apply a scenario to the simulation."""
    scenario = get_scenario(req.scenario_name)
    if req.custom_config:
        scenario_dict = scenario.model_dump()
        scenario_dict.update(req.custom_config)
        scenario = ScenarioConfig(**scenario_dict)
    engine.apply_scenario(scenario)
    return {"status": "scenario_applied", "scenario": scenario.model_dump()}


# --- Agent ---

@app.get("/api/agent/logs")
async def get_agent_logs(limit: int = Query(50, ge=1, le=200)):
    """Get agent activity logs."""
    state = engine.get_state()
    logs = state.get("agent_logs", [])
    return {"logs": logs[-limit:]}


@app.get("/api/alerts")
async def get_alerts(limit: int = Query(20, ge=1, le=100)):
    """Get system alerts."""
    state = engine.get_state()
    alerts = state.get("alerts", [])
    return {"alerts": alerts[-limit:]}


# --- Configuration ---

@app.post("/api/config/station")
async def update_station_config(config: StationConfig):
    """Update station configuration."""
    engine.update_config(config)
    return {"status": "config_updated"}


class LoadsUpdate(BaseModel):
    loads: list[dict]


@app.post("/api/config/loads")
async def update_loads(update: LoadsUpdate):
    """Update station loads."""
    engine.update_loads(update.loads)
    return {"status": "loads_updated"}


# --- Demo / Presentation Mode ---

@app.post("/api/demo/start")
async def start_demo():
    """Start the automated demo sequence."""
    engine.start_demo()
    return {"status": "demo_started"}


# ============================================================
# WEBSOCKET
# ============================================================

@app.websocket("/ws/simulation")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time simulation updates."""
    await ws_manager.connect(websocket)
    try:
        # Send initial state
        await websocket.send_json(engine.get_state())

        # Keep connection alive and handle client messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                msg = json.loads(data)

                if msg.get("type") == "start":
                    speed = msg.get("speed", 1)
                    engine.set_speed(speed)
                    if engine.state.is_paused:
                        engine.resume()
                    else:
                        engine.start()
                elif msg.get("type") == "pause":
                    engine.pause()
                elif msg.get("type") == "reset":
                    engine.stop()
                    engine.reset()
                    await websocket.send_json(engine.get_state())
                elif msg.get("type") == "speed":
                    engine.set_speed(msg.get("speed", 1))
                elif msg.get("type") == "scenario":
                    scenario = get_scenario(msg.get("name", "NORMAL"))
                    engine.apply_scenario(scenario)
                elif msg.get("type") == "demo":
                    engine.start_demo()
                elif msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({"type": "heartbeat", "state": engine.get_state()})
                except Exception:
                    break

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "POLARIS API"}
