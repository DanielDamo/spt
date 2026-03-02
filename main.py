from pi_controller import PiController
import ble_server
import logging
import time
import threading


pi_controller = PiController()

# Start BLE server in its own thread so main thread can keep running
ble_thread = threading.Thread(
    target=ble_server.run,
    args=(pi_controller,),
    kwargs={"name": "SPT-Pi"},
    daemon=True
)
ble_thread.start()

try:
    # Main loop watches for shutdown request
    while not pi_controller.shutdown_requested.is_set():
        time.sleep(0.1)
finally:
    # Stop controller threads + GPIO + display + save
    pi_controller.stop()

    # Wait briefly for BLE thread to exit cleanly
    ble_thread.join(timeout=2.0)

    #TODO: also power off pi