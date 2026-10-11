from uuid import uuid4

import pytest

from app.main import Service
from app.store import Store


def command(action, sim_id="sim-a", **extra):
    return {"action": action, "request_id": str(uuid4()), "sim_id": sim_id, **extra}


def test_manual_number_is_bound_to_sim_and_survives_refresh_restart(tmp_path):
    path = tmp_path / "console.sqlite3"
    store = Store(path)
    service = Service(store)
    service.connection["ready"] = True
    service.device = {"mobile": {"sim_ready": True, "iccid": "sim-a", "number": ""}}
    # Local configuration must not go through the serial bridge.
    service.command(command("set_sim_number", to="+15555550100"))
    mobile = service.snapshot()["device"]["mobile"]
    assert mobile["number"] == "+15555550100" and mobile["number_source"] == "manual"
    assert service.device["mobile"]["number"] == ""
    assert mobile["number_confirmed_at"] > 0
    service.device = {"mobile": {"sim_ready": True, "iccid": "sim-b", "number": ""}}
    assert service.snapshot()["device"]["mobile"]["number"] == ""
    with pytest.raises(ValueError, match="SIM 卡已变化"):
        service.command(command("set_sim_number", to="+15555550101"))
    service.command(command("set_sim_number", sim_id="sim-b", to="+15555550101"))
    store.close()
    restored = Store(path)
    service = Service(restored)
    service.connection["ready"] = True
    service.device = {"mobile": {"sim_ready": True, "iccid": "sim-a", "number": ""}}
    assert service.snapshot()["device"]["mobile"]["number"] == "+15555550100"
    # Receiving a reply never changes the local number to the sender's number.
    restored.incoming({"from": "+15555550199", "content": "test"}, "sim-a")
    assert service.snapshot()["device"]["mobile"]["number"] == "+15555550100"
    service.command(command("clear_sim_number"))
    assert service.snapshot()["device"]["mobile"]["number_source"] == "unavailable"
    assert restored.sim_number("sim-b")["phone"] == "+15555550101"
    restored.close()


def test_sim_number_takes_precedence_and_configuration_requires_ready_sim(tmp_path):
    store = Store(tmp_path / "console.sqlite3")
    service = Service(store)
    with pytest.raises(ValueError, match="设备尚未连接"):
        service.command(command("set_sim_number", to="+15555550100"))
    service.connection["ready"] = True
    service.device = {"mobile": {"sim_ready": False, "iccid": "sim-a"}}
    with pytest.raises(ValueError, match="SIM 卡未就绪"):
        service.command(command("set_sim_number", to="+15555550100"))
    service.device = {"mobile": {"sim_ready": True, "iccid": "sim-a", "number": "+15555550199"}}
    service.command(command("set_sim_number", to="+15555550100"))
    assert service.snapshot()["device"]["mobile"]["number"] == "+15555550199"
    assert service.snapshot()["device"]["mobile"]["number_source"] == "sim"
    store.close()
