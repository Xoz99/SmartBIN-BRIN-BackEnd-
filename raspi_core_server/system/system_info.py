"""
====================================================
System Info - MQTT-based (remote Raspi metrics)
====================================================
State di-update dari MQTT topic raspi/system,
bukan dari psutil lokal.
====================================================
"""

from dashboard.state import state


def update_system_state():
    """
    Tidak lagi baca psutil lokal.
    State sudah di-update oleh MQTTClient saat
    menerima pesan di topic 'raspi/system'.
    Fungsi ini tetap ada supaya main.py tidak perlu diubah.
    """
    pass