import threading
import time

import ble_server
from pi_controller import PiController


pi = PiController()

_ble_thread: dict[str, threading.Thread | None] = {"thread": None}


def start_ble() -> None:
    """
    Start the BLE server on a daemon thread.
    If a previous thread is still alive, wait for it to finish first.
    """
    old = _ble_thread["thread"]
    if old is not None and old.is_alive():
        old.join(timeout=3.0)

    t = threading.Thread(
        target=ble_server.run,
        args=(pi,),
        kwargs={"name": "SPT-Pi"},
        daemon=True,
        name="ble-server",
    )
    _ble_thread["thread"] = t
    t.start()


# Wire BLE hooks into the controller so it can start/stop/reset BLE
pi.ble_start = start_ble

start_ble()

try:
    while not pi.shutdown_requested.is_set():
        time.sleep(0.1)
finally:
    pi.stop()
    ble_thread = _ble_thread["thread"]
    if ble_thread is not None:
        ble_thread.join(timeout=2.0)