from threading import Lock, Thread
import os
import datetime as dt
import pandas as pd
import time
import matplotlib.pyplot as plt
from sensors.bme280 import bme280
from sensors.tsl2561 import tsl2561
from sensors.hcsr501 import hcsr501
import display.waveshare as waveshare

PIXELS_X = 480
PIXELS_Y = 800
DPI = 100
INTERVAL = 1

class PiController:
    def __init__(
            self,
            upload_folder="assets/images"
        ):
        
        # Temp, Humidity, Pressure
        self.bme = bme280(INTERVAL)
        self.bme.format()

        # Light
        self.tsl = tsl2561(INTERVAL)
        self.tsl.format()

        # Motion
        self.hcsr = hcsr501(INTERVAL)
        self.hcsr.format()

        self.sensor_threads = [
            self.bme,
            self.tsl,
            self.hcsr
        ]

        # Display
        try:
            self.epd = waveshare.EPD()
            self.epd.init()
            self.display_lock = Lock()        
        except IOError as e:
            logging.info(e)
        except KeyboardInterrupt:    
            waveshare.epdconfig.module_exit(cleanup=True)
            exit()

        # Directories
        self.upload_folder = upload_folder
        os.makedirs(self.upload_folder, exist_ok=True)
        self.logs_folder = "logs"
        os.makedirs(self.logs_folder, exist_ok=True)

        # Alarm
        self.alarm_time = None

    # Start Sensors
    def start_sensors(self):
        for sensor in self.sensor_threads:
            sensor_thread = Thread(target=sensor.start)
            sensor_thread.start()
        print("[PiController] Sensor threads started.")

    # Stop all sensors and display
    def stop(self):
        for sensor in self.sensor_threads:
            sensor.stop()
        print("[PiController] Sensor threads stopped.")
        waveshare.epdconfig.module_exit(cleanup=True)
    
    def display_image(self, img_name):
        try:
            with self.display_lock:
                self.epd.display(self.epd.getbuffer(os.path.join(self.upload_folder, img_name)))
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            exit()
    
    def display_graph(self):
        self.generate_plot()
        try:
            with self.display_lock:
                self.epd.display(self.epd.getbuffer(f"assets/graph.png"))
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            exit()

    # Show current values
    def get_sensor_values(self):
        return {
            "temperature": self.bme.sensor.temperature,
            "humidity": self.bme.sensor.humidity,
            "pressure": self.bme.sensor.pressure,
            "light": self.tsl.sensor.lux,
            #"motion": self.hcsr.motion_detected
        }
    
    # Show all available images
    def list_uploaded_photos(self):
        return os.listdir(self.upload_folder)

    # Plot Helper function
    def read_two_column_log(self, path):
        times = []
        values = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.lower().startswith("time"):
                    continue
                t_str, v_str = line.split(",", 1)
                times.append(dt.datetime.fromisoformat(t_str))
                values.append(float(v_str))
        return times, values

    # Plot Helper function
    def read_motion_log(self, path):
        times = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.lower().startswith("time"):
                    continue
                times.append(dt.datetime.fromisoformat(line))
        return times

    # Generate plot
    def generate_plot(self):
        hum_t, hum_v = self.read_two_column_log(self.bme.humiditylog)
        light_t, light_v = self.read_two_column_log(self.tsl.lightlog)
        pres_t, pres_v = self.read_two_column_log(self.bme.pressurelog)
        temp_t, temp_v = self.read_two_column_log(self.bme.templog)
        motion_t = self.read_motion_log(self.hcsr.motionlog)

        fig, axes = plt.subplots(5, 1, figsize=(PIXELS_X/DPI, PIXELS_Y/DPI), dpi=DPI, sharex=True)
        ax_hum, ax_light, ax_pres, ax_temp, ax_motion = axes

        ax_hum.plot(hum_t, hum_v)
        ax_hum.set_title("Humidity")

        ax_light.plot(light_t, light_v)
        ax_light.set_title("Light Level")

        ax_pres.plot(pres_t, pres_v)
        ax_pres.set_title("Pressure")

        ax_temp.plot(temp_t, temp_v)
        ax_temp.set_title("Temperature")

        ax_motion.vlines(motion_t, ymin=0, ymax=1, linewidth=2)
        ax_motion.set_ylim(0, 1)
        ax_motion.set_title("Motion Events")
        ax_motion.set_yticks([])

        plt.tight_layout()
        plt.savefig("assets/graph.png")

