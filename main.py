from threading import Thread
from sensors.bme280 import bme280
from sensors.tsl2561 import tsl2561
from sensors.hcsr501 import hcsr501
import pandas as pd
import time
import matplotlib.pyplot as plt

INTERVAL = 1

bme = bme280(INTERVAL)
bme.format()

tsl = tsl2561(INTERVAL)
tsl.format()

hcsr = hcsr501(INTERVAL)
hcsr.format()

bmeThread = Thread(target=bme.start)
tslThread = Thread(target=tsl.start)
hcsrThread = Thread(target=hcsr.start)

bmeThread.start()
tslThread.start()
hcsrThread.start()

while True:
    c = input("(S)top, (P)lot: ")
    
    if c.lower() == "s":
        bme.stop()
        tsl.stop()
        hcsr.stop()
        break
        
    if c.lower() == "p":
        df = pd.read_csv(tsl.lightlog)
        yp = df["light"]
        xp = df["time"]
        plt.plot(xp,yp)
        plt.show()