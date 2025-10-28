from threading import Thread
from bme280 import bme280
from tsl2561 import tsl2561
import pandas as pd
import time
import matplotlib.pyplot as plt

bme = bme280()
bme.format()

tsl = tsl2561()
tsl.format()

bmeThread = Thread(target=bme.start)
tslThread = Thread(target=tsl.start)

bmeThread.start()
tslThread.start()

while True:
    c = input("(S)top, (P)lot, (Q)uit: ")
    
    if c.lower() == "s":
        bme.stop()
        tsl.stop()
    if c.lower() == "p":
        df = pd.read_csv(tsl.lightlog)
        yp = df["light"]
        xp = df["time"]
        plt.plot(xp,yp)
        plt.show()
    if c.lower() == "q":
        break