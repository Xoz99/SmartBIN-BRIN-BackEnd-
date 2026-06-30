from dataclasses import dataclass, field


# =====================================================
# SYSTEM STATE
# =====================================================

@dataclass
class SystemState:

    cpu: float = 0.0
    ram: float = 0.0
    disk: float = 0.0
    temp: float = 0.0
    cache: float = 0.0   # dalam MB

    uptime: str = "-"
    ip: str = "-"


# =====================================================
# MQTT STATE
# =====================================================

@dataclass
class MQTTState:

    connected: bool = False

    broker: str = "-"
    topic: str = "-"


# =====================================================
# RASPBERRY PI A (WiFi)
# =====================================================

@dataclass
class WiFiState:

    # -----------------------------
    # Connection
    # -----------------------------

    connected: bool = False

    # -----------------------------
    # Packet Statistics
    # -----------------------------

    packets: int = 0
    received: int = 0

    loss: int = 0
    loss_percent: float = 0.0

    # -----------------------------
    # Real-time Statistics
    # -----------------------------

    packet_rate: float = 0.0
    throughput: float = 0.0
    latency: float = 0.0

    # -----------------------------
    # Last Packet Information
    # -----------------------------

    last_sequence: int = 0

    last_packet_time: float = 0.0
    last_packet_age: float = 0.0


# =====================================================
# DASHBOARD STATE
# =====================================================

@dataclass
class DashboardState:

    system: SystemState = field(
        default_factory=SystemState
    )

    mqtt: MQTTState = field(
        default_factory=MQTTState
    )

    wifi: WiFiState = field(
        default_factory=WiFiState
    )


# =====================================================
# GLOBAL STATE
# =====================================================

state = DashboardState()
