state = {
    "display_mode": 0       # 0 = Graph, 1 = pciture
}

config = {
    "sensor_interval": 1    # Seconds
}





save_state(state)
print(load_state())
