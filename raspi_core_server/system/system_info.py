import socket
import time

import psutil

from dashboard.state import state


def update_system_state():

    # CPU
    state.system.cpu = psutil.cpu_percent(interval=None)

    # RAM
    state.system.ram = psutil.virtual_memory().percent

    # Disk
    state.system.disk = psutil.disk_usage("/").percent

    # Temperature
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            state.system.temp = int(f.read()) / 1000
    except Exception:
        state.system.temp = 0.0

    # Uptime
    uptime_seconds = int(time.time() - psutil.boot_time())

    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60

    state.system.uptime = f"{hours}h {minutes}m"

    # IP Address
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        state.system.ip = s.getsockname()[0]
        s.close()
    except Exception:
        state.system.ip = "Unknown"
