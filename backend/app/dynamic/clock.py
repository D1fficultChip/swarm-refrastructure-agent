from dataclasses import dataclass


@dataclass
class SimulationClock:
    time: float = 0.0
    running: bool = False
    speed: float = 1.0

    def start(self):
        self.running = True

    def pause(self):
        self.running = False

    def resume(self):
        self.running = True

    def reset(self):
        self.time = 0.0
        self.running = False

    def set_speed(self, speed: float):
        if speed not in {0.5, 1.0, 2.0, 5.0}:
            raise ValueError("unsupported simulation speed")
        self.speed = speed

    def advance(self, real_seconds: float) -> float:
        if real_seconds < 0:
            raise ValueError("time cannot move backwards")
        if self.running:
            self.time += real_seconds * self.speed
        return self.time

    def step(self, simulation_seconds: float = 1.0) -> float:
        if simulation_seconds <= 0:
            raise ValueError("step must be positive")
        self.time += simulation_seconds
        return self.time
