from pi_controller import PiController
from web_server import SPTWebServer
import logging
import time

logging.basicConfig(level=logging.DEBUG)

# Initialize Pi controller with sensors
pi_controller = PiController()

# Start sensor threads
pi_controller.start_sensors()


webserver = SPTWebServer(pi_controller)
webserver.start()

# Main loop
try:
    while True:
        # Do other Pi stuff here if needed
        pass
except KeyboardInterrupt:
    pi_controller.stop()

#time.sleep(10)

# # Example: read latest values
# pi_controller.display_image("dog.jpg")
# time.sleep(5)
# pi_controller.display_graph()
# time.sleep(5)
# pi_controller.display_image("lake.jpg")

# # Stop sensors
# pi_controller.stop()

