import re
from datetime import time

import serial


class SerialReader():

    def __init__(self, port, baudrate, initial_timestamp):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.running = True
        self.initial_timestamp = initial_timestamp

    def run(self):
        with serial.Serial(self.port, self.baudrate) as ser:
            while self.running:
                if ser.in_waiting:
                    data = ser.readline().decode('utf-8', errors='replace').strip()
                    match = re.search(r'(\d+\.\d+)pH', data)
                    if match:
                        ph_value = float(match.group(1))
                        current_timestamp = time.time()
                        elapsed_time = (
                                                   current_timestamp - self.initial_timestamp) / 60  # minutes since initial timestamp
                        return current_timestamp, ph_value
                    time.sleep(1)
