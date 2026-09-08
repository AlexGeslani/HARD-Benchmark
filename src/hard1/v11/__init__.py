"""HARD1 v1.1 execution and evidence contract."""

from importlib.resources import files
import json

CONTRACT_VERSION = "1.1.0"


def load_contract() -> dict:
    return json.loads(files("hard1.contracts").joinpath("hard1-v1.1.json").read_text())
