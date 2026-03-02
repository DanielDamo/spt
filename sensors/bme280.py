import board
import time
import busio
import os
from datetime import datetime
from adafruit_bme280 import basic as adafruit_bme280

SLEEP_SLICE = 1.0 #TODO: add to constants file

class bme280:
    def __init__(self, interval):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor = adafruit_bme280.Adafruit_BME280_I2C(self.i2c, address=0x77)
        self.interval = interval

        # Base log directories
        self.tempdir = os.path.join("logs", "temp")
        self.humiditydir = os.path.join("logs", "humidity")
        self.pressuredir = os.path.join("logs", "pressure")

        # Ensure directories exist
        os.makedirs(self.tempdir, exist_ok=True)
        os.makedirs(self.humiditydir, exist_ok=True)
        os.makedirs(self.pressuredir, exist_ok=True)

        self.stopped = False

    def start(self):
        while not self.stopped:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H:%M:%S")

            temp = self.sensor.temperature
            humidity = self.sensor.humidity
            pressure = self.sensor.pressure

            temp_file = os.path.join(self.tempdir, f"{date_str}.csv")
            hum_file  = os.path.join(self.humiditydir, f"{date_str}.csv")
            pres_file = os.path.join(self.pressuredir, f"{date_str}.csv")

            self._append(temp_file, "temp", time_str, temp)
            self._append(hum_file,  "humidity", time_str, humidity)
            self._append(pres_file, "pressure", time_str, pressure)

            # Sleep in small steps so stop() reacts quickly
            for _ in range(int(self.interval / SLEEP_SLICE)):
                if self.stopped:
                    break
                time.sleep(SLEEP_SLICE)

    def _append(self, path, label, t, value):
        new_file = not os.path.exists(path)
        with open(path, "a") as f:
            if new_file:
                f.write(f"time,{label}\n")
            f.write(f"{t},{value}\n")

    def stop(self):
        self.stopped = True