"""M5b compatibility rules engine unit tests."""
from __future__ import annotations


def _cpu(id_: str, socket: str = "AM5", cores: int = 8) -> dict:
    return {"id": id_, "name": f"CPU {id_}", "category": "cpu", "socket": socket, "cpu_cores": cores, "cpu_model": "Test"}


def _mobo(id_: str, socket: str = "AM5", ram_type: str = "DDR5", max_ram_gb: int = 64) -> dict:
    return {
        "id": id_, "name": f"Mobo {id_}", "category": "mainboard",
        "socket": socket, "ram_type": ram_type, "max_ram_gb": max_ram_gb,
        "form_factor": "ATX", "supported_mainboard_form_factors": ["ATX", "mATX"],
    }


def _ram(id_: str, ram_type: str = "DDR5", gb: int = 16) -> dict:
    return {"id": id_, "name": f"RAM {id_}", "category": "ram", "ram_type": ram_type, "ram_gb": gb}


def _psu(id_: str, watt: int = 650, recommended_total: int = 0) -> dict:
    return {
        "id": id_, "name": f"PSU {id_}", "category": "psu",
        "psu_wattage_w": watt, "recommended_psu_w": recommended_total,
    }


def _case(id_: str, supported: list[str] | None = None) -> dict:
    return {
        "id": id_, "name": f"Case {id_}", "category": "case",
        "supported_mainboard_form_factors": supported or ["ATX", "mATX", "ITX"],
    }


def _item_with_warnings(id_: str, warnings: list[str]) -> dict:
    base = _cpu(id_)
    base["warnings"] = warnings
    base["recommended_psu_w"] = 100
    return base


def test_compatible_build_no_issues():
    from agents.compat.rules import evaluate

    items = [
        _cpu("cpu1", "AM5"),
        _mobo("m1", "AM5", "DDR5"),
        _ram("r1", "DDR5", 16),
        _psu("p1", 650, 300),
    ]
    result = evaluate(items)
    assert result.compatible is True
    assert result.issues == []
    assert result.psu_wattage_required == 300
    assert result.total_price_vnd == 0


def test_socket_mismatch_fails():
    from agents.compat.rules import evaluate

    items = [_cpu("cpu1", "AM5"), _mobo("m1", "LGA1700", "DDR5")]
    result = evaluate(items)
    assert result.compatible is False
    assert any(i.rule == "socket_mismatch" for i in result.issues)
    assert ("cpu1", "m1") in [(i.pair) for i in result.issues]


def test_ram_type_mismatch_fails():
    from agents.compat.rules import evaluate

    items = [_mobo("m1", "AM5", "DDR5"), _ram("r1", "DDR4", 16)]
    result = evaluate(items)
    assert result.compatible is False
    assert any(i.rule == "ram_type_mismatch" for i in result.issues)


def test_ram_capacity_exceeds_max():
    from agents.compat.rules import evaluate

    items = [_mobo("m1", "AM5", "DDR5", max_ram_gb=32), _ram("r1", "DDR5", 64)]
    result = evaluate(items)
    assert result.compatible is False
    assert any(i.rule == "ram_capacity" for i in result.issues)


def test_form_factor_mismatch():
    from agents.compat.rules import evaluate

    items = [
        _mobo("m1", "AM5", "DDR5"),
        _case("c1", supported=["ITX"]),
    ]
    result = evaluate(items)
    assert result.compatible is False
    assert any(i.rule == "form_factor_mismatch" for i in result.issues)


def test_psu_underpowered():
    from agents.compat.rules import evaluate

    items = [
        _cpu("cpu1", "AM5"),
        _psu("p1", watt=300, recommended_total=0),
    ]
    items[0]["recommended_psu_w"] = 400
    result = evaluate(items)
    assert result.compatible is False
    assert any(i.rule == "psu_underpowered" for i in result.issues)


def test_data_driven_warnings_collected():
    from agents.compat.rules import evaluate

    items = [
        _item_with_warnings("cpu1", ["Yêu cầu BIOS version 1.2"]),
        _mobo("m1"),
    ]
    result = evaluate(items)
    assert any("BIOS" in w for w in result.warnings)


def test_compatible_skips_form_factor_when_no_case():
    from agents.compat.rules import evaluate

    items = [_cpu("cpu1"), _mobo("m1"), _ram("r1"), _psu("p1", watt=650, recommended_total=300)]
    items[-3]["recommended_psu_w"] = 0
    result = evaluate(items)
    assert not any(i.rule == "form_factor_mismatch" for i in result.issues)
    assert result.compatible is True


def test_warnings_loads_from_jsonb_string():
    from agents.compat.rules import evaluate

    items = [_item_with_warnings("c1", '["Needs BIOS update"]'), _mobo("m1")]
    result = evaluate(items)
    assert any("BIOS" in w for w in result.warnings)
