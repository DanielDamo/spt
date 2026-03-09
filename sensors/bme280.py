import os
import threading
from datetime import datetime

import board
import busio
from adafruit_bme280 import basic as adafruit_bme280


class BME280:
    """
    Reads temperature, humidity and pressure from a BME280 sensor and logs
    each value to daily CSV files.  Runs on its own daemon thread.

    Call start() once to begin logging.  Call update_interval() at any time
    to change the sampling period — the current sleep is interrupted immediately
    so the new interval takes effect on the very next cycle.
    """

    def __init__(self, interval: float, on_reading=None) -> None:
        self._i2c    = busio.I2C(board.SCL, board.SDA)
        self._sensor = adafruit_bme280.Adafruit_BME280_I2C(self._i2c, address=0x77)
        self._interval  = interval
        self._on_reading = on_reading   # optional callable, fired after each reading

        self._temp_dir     = os.path.join("logs", "temp")
        self._humidity_dir = os.path.join("logs", "humidity")
        self._pressure_dir = os.path.join("logs", "pressure")

        for d in (self._temp_dir, self._humidity_dir, self._pressure_dir):
            os.makedirs(d, exist_ok=True)

        self._stop_evt = threading.Event()
        self._wake_evt = threading.Event()
        self._thread   = threading.Thread(target=self._run, daemon=True, name="bme280")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the logging thread."""
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop and block until it exits (max 5 s)."""
        self._stop_evt.set()
        self._wake_evt.set()
        self._thread.join(timeout=5)

    def update_interval(self, interval: float) -> None:
        """Change the sampling interval.  Takes effect after the current reading."""
        self._interval = interval
        self._wake_evt.set()

    def get_log_dirs(self) -> list:
        return [self._humidity_dir, self._pressure_dir, self._temp_dir]

    @property
    def sensor(self):
        """Direct access to the underlying Adafruit sensor object."""
        return self._sensor

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            self._read_and_log()
            if self._on_reading is not None:
                try:
                    self._on_reading()
                except Exception as e:
                    print(f"[BME280] on_reading callback error: {e}")
            self._wake_evt.wait(timeout=self._interval)
            self._wake_evt.clear()

    def _read_and_log(self) -> None:
        now      = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        temp     = self._sensor.temperature
        humidity = self._sensor.humidity
        pressure = self._sensor.pressure

        self._append(os.path.join(self._temp_dir,     f"{date_str}.csv"), "temp",     time_str, temp)
        self._append(os.path.join(self._humidity_dir, f"{date_str}.csv"), "humidity", time_str, humidity)
        self._append(os.path.join(self._pressure_dir, f"{date_str}.csv"), "pressure", time_str, pressure)

    @staticmethod
    def _append(path: str, label: str, timestamp: str, value: float) -> None:
        new_file = not os.path.exists(path)
        with open(path, "a") as f:
            if new_file:
                f.write(f"time,{label}\n")
            f.write(f"{timestamp},{value}\n")