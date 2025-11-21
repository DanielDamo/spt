import board
import time
import busio
from datetime import datetime
from adafruit_bme280 import basic as adafruit_bme280

class bme280:
    def __init__(self, interval):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor = adafruit_bme280.Adafruit_BME280_I2C(self.i2c, address=0x77)
        self.interval = interval
        self.templog = "logs/temp.log"
        self.humiditylog = "logs/humidity.log"
        self.pressurelog = "logs/pressure.log"

        self.stopped = False


    def start(self):
        while not self.stopped:
            temp = self.sensor.temperature
            humidity = self.sensor.humidity
            pressure = self.sensor.pressure

            with open(self.templog, "a") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{temp}\n")
            
            with open(self.humiditylog, "a") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{humidity}\n")

            with open(self.pressurelog, "a") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{pressure}\n")

            time.sleep(self.interval)
    

    def stop(self):
        self.stopped = True


    def format(self):
        with open(self.templog, "w") as f:
            f.write("time,temp\n")
        with open(self.humiditylog, "w") as f:
            f.write("time,humidity\n")
        with open(self.pressurelog, "w") as f:
            f.write("time,pressure\n")
