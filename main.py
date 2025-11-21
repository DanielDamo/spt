from threading import Thread
from sensors.bme280 import bme280
from sensors.tsl2561 import tsl2561
from sensors.hcsr501 import hcsr501
import pandas as pd
import time
import matplotlib.pyplot as plt
import datetime as dt
import logging
import display.waveshare as waveshare

INTERVAL = 1
PIXELS_X = 480
PIXELS_Y = 800
DPI = 100 

logging.basicConfig(level=logging.DEBUG)

# Temp, Humidity, Pressure
bme = bme280(INTERVAL)
bme.format()

# Light
tsl = tsl2561(INTERVAL)
tsl.format()

# Motion
hcsr = hcsr501(INTERVAL)
hcsr.format()

# Display
try:
    epd = waveshare.EPD()
    epd.init()        
except IOError as e:
    logging.info(e)
except KeyboardInterrupt:    
    waveshare.epdconfig.module_exit(cleanup=True)
    exit()

# Threads
bmeThread = Thread(target=bme.start)
tslThread = Thread(target=tsl.start)
hcsrThread = Thread(target=hcsr.start)

bmeThread.start()
tslThread.start()
hcsrThread.start()


def read_two_column_log(path):
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

def read_motion_log(path):
    times = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.lower().startswith("time"):
                continue
            times.append(dt.datetime.fromisoformat(line))
    return times

def plot_logs():
    hum_t, hum_v = read_two_column_log("logs/humidity.log")
    light_t, light_v = read_two_column_log("logs/light.log")
    pres_t, pres_v = read_two_column_log("logs/pressure.log")
    temp_t, temp_v = read_two_column_log("logs/temp.log")
    motion_t = read_motion_log("logs/motion.log")

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
    plt.show()

while True:
    try:
        c = input("(S)top, (P)lot, (D)og: ")
        
        if c.lower() == "s":
            bme.stop()
            tsl.stop()
            hcsr.stop()
            waveshare.epdconfig.module_exit(cleanup=True)
            break
            
        if c.lower() == "p":
            plot_logs()
            epd.display(epd.getbuffer("assets/graph.png"))
        
        if c.lower() == "d":
            epd.display(epd.getbuffer("assets/images/dog.jpg"))

    except KeyboardInterrupt:
        bme.stop()
        tsl.stop()
        hcsr.stop()
        waveshare.epdconfig.module_exit(cleanup=True)
        break
    
    except IOError as e:
        logging.info(e)