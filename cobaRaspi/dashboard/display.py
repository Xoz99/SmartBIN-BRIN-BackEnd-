from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dashboard.state import state
from logger.log_manager import logger


# =====================================================
# HEADER
# =====================================================

def header_panel():

    title = (
        "[bold cyan]CORE SERVER MONITOR[/bold cyan]\n"
        "[white]Version 0.5[/white]"
    )

    return Panel(
        title,
        border_style="cyan"
    )


# =====================================================
# HELPERS
# =====================================================

def format_cache(mb: float) -> str:
    if mb < 1:
        return f"{mb * 1024:.1f} KB"
    elif mb < 1024:
        return f"{mb:.1f} MB"
    else:
        return f"{mb / 1024:.2f} GB"


# =====================================================
# SYSTEM PANEL
# =====================================================

def system_panel():

    s = state.system

    table = Table.grid(padding=(0, 2))

    table.add_row("CPU", f"{s.cpu:.1f} %")
    table.add_row("RAM", f"{s.ram:.1f} %")
    table.add_row("Disk", f"{s.disk:.1f} %")
    table.add_row("Temperature", f"{s.temp:.1f} °C")
    table.add_row("Cache", format_cache(s.cache))
    table.add_row("Uptime", s.uptime)
    table.add_row("IP Address", s.ip)

    return Panel(
        table,
        title="System",
        border_style="green"
    )


# =====================================================
# STATUS PANEL
# =====================================================

def status_panel():

    table = Table.grid(padding=(0, 2))

    mqtt = (
        "[green]Connected[/green]"
        if state.mqtt.connected
        else "[red]Disconnected[/red]"
    )

    wifi = (
        "[green]Online[/green]"
        if state.wifi.connected
        else "[red]Offline[/red]"
    )

    table.add_row("MQTT", mqtt)
    table.add_row("WiFi", wifi)

    return Panel(
        table,
        title="Communication Status",
        border_style="yellow"
    )


# =====================================================
# WIFI PANEL
# =====================================================

def wifi_panel():

    w = state.wifi

    table = Table.grid(padding=(0, 2))

    table.add_row("Packets", str(w.packets))

    table.add_row("Received", str(w.received))

    table.add_row(
        "Loss",
        f"{w.loss} ({w.loss_percent:.2f}%)"
    )

    table.add_row(
        "Packet Rate",
        f"{w.packet_rate:.2f} pkt/s"
    )

    table.add_row(
        "Latency",
        f"{w.latency:.2f} ms"
    )

    table.add_row(
        "Throughput",
        f"{w.throughput:.2f} KB/s"
    )

    table.add_row(
        "Last Seq",
        str(w.last_sequence)
    )

    table.add_row(
        "Last Packet",
        f"{w.last_packet_age:.1f} s ago"
    )

    return Panel(
        table,
        title="WiFi",
        border_style="blue"
    )


# =====================================================
# EVENT LOG
# =====================================================

def footer_panel():

    logs = logger.get_logs()

    text = Text()

    colors = {

        "REQUEST": "cyan",

        "POST": "green",

        "UPDATE": "yellow",

        "INFO": "white",

        "SUCCESS": "bright_green",

        "WARNING": "orange3",

        "ERROR": "red",

    }

    if not logs:

        text.append(
            "Waiting for events..."
        )

    else:

        for log in logs:

            level = log["level"]

            color = colors.get(
                level,
                "white"
            )

            text.append(
                f"{log['time']} ",
                style="white"
            )

            text.append(
                f"{level:<8}",
                style=color
            )

            text.append(
                f"{log['message']}\n",
                style="white"
            )

    return Panel(
        text,
        title="Live Event Log",
        border_style="white"
    )
