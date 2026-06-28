"""
====================================================
Log Manager
====================================================
Centralized logging for Core Server Monitor.
"""

from collections import deque
from datetime import datetime

from config import MAX_LOG_LINES


class LogManager:

    def __init__(self):

        self.logs = deque(maxlen=MAX_LOG_LINES)

    # ==================================================
    # INTERNAL
    # ==================================================

    def _write(self, level: str, message: str):

        timestamp = datetime.now().strftime("%H:%M:%S")

        self.logs.append({
            "time": timestamp,
            "level": level,
            "message": message
        })

    # ==================================================
    # EVENT LOG
    # ==================================================

    def request(self, message: str):
        self._write("REQUEST", message)

    def post(self, message: str):
        self._write("POST", message)

    def update(self, message: str):
        self._write("UPDATE", message)

    # ==================================================
    # SYSTEM LOG
    # ==================================================

    def info(self, message: str):
        self._write("INFO", message)

    def success(self, message: str):
        self._write("SUCCESS", message)

    def warning(self, message: str):
        self._write("WARNING", message)

    def error(self, message: str):
        self._write("ERROR", message)

    # ==================================================
    # PUBLIC
    # ==================================================

    def clear(self):
        self.logs.clear()

    def get_logs(self):
        return list(self.logs)

    def count(self):
        return len(self.logs)


logger = LogManager()
