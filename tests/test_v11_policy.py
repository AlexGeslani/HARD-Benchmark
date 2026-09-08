from hard1.v11.policy import GRID, calibration, observe


def test_observer_abstains_without_state_and_never_terminates():
    item = observe({"name": "synthetic", "arguments": {"x": 1}}, None)
    assert item["available"] is False
    assert item["mode"] == "OBSERVE_ONLY"
    report = calibration([
        {"successful": True, "observations": [{"action": {"n": 1}, "state": {"v": n}} for n in range(12)]},
        {"successful": False, "observations": [{"action": {"n": 1}, "state": {"v": 1}} for _ in range(12)]},
        {"successful": False, "observations": [{"action": {"n": 1}, "state": None}]},
    ])
    assert tuple(row["n"] for row in report["grid"]) == GRID
    assert report["termination_enabled"] is False
    assert all(row["abstentions"] == 1 for row in report["grid"])
