import time

from rich.live import Live

from config import REFRESH_RATE

from dashboard.layout import make_layout
from dashboard.display import (
    header_panel,
    system_panel,
    status_panel,
    wifi_panel,
    footer_panel,
)

from system.system_info import update_system_state

from logger.log_manager import logger

from network.mqtt_client import mqtt_client
from network.wifi_statistics import wifi_statistics


layout = make_layout()


logger.info("Core Server Monitor started")
logger.success("Dashboard initialized")


mqtt_client.connect()

with Live(
    layout,
    refresh_per_second=REFRESH_RATE,
    screen=True,
) as live:

    counter = 0

    while True:

        # ===================================
        # UPDATE STATE
        # ===================================

        update_system_state()
        wifi_statistics.update()

        # ===================================
        # UPDATE UI
        # ===================================

        layout["header"].update(header_panel())
        layout["system"].update(system_panel())
        layout["status"].update(status_panel())
        layout["wifi"].update(wifi_panel())
        layout["footer"].update(footer_panel())

        # ===================================
        # PERIODIC LOG
        # ===================================

        if counter % 10 == 0:

            logger.info(
                "System monitoring is running"
            )

        live.update(layout)

        counter += 1

        time.sleep(1)
