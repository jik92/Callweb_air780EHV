"""In-process transports for legacy API behavior tests; no production USB owner."""

from app import main


def install_bridge(monkeypatch, factory):
    original = main.Service

    class SimulatedService(original):
        def __init__(self, store, device_id="local"):
            super().__init__(store, device_id)
            self.bridge = factory(None, self.events.put_nowait)
            self.bridge.start()

    monkeypatch.setattr(main, "Service", SimulatedService)
