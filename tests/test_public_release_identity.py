import tomllib
import json
from pathlib import Path

import hard1
from hard1.v11 import CONTRACT_VERSION, load_contract


ROOT = Path(__file__).parents[1]


def test_public_release_identity_preserves_internal_contract_lineage():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert hard1.__version__ == "1.0.0"
    assert project["project"]["version"] == "1.0.0"
    assert CONTRACT_VERSION == "1.1.0"
    readme = (ROOT / "README.md").read_text()
    assert "Hard1 v1.0" in readme
    assert "internal contract identifier `1.1.0`" in readme
    t3 = load_contract()["families"]["T3"]
    assert t3["simulator_mode"] == "candidate-selfplay"
    assert "reference_simulator" not in t3
    native_t3 = json.loads((ROOT / "src/hard1/contracts/t3.json").read_text())
    assert native_t3["user_simulator"] == {
        "mode": "candidate-selfplay",
        "settings_source": "campaign-role-config",
    }
    compatibility_runner = (ROOT / "src/hard1/runners/t3.py").read_text()
    assert "gpt-4" not in compatibility_runner
    assert "OPENAI_API_KEY" not in compatibility_runner
    assert "llm_user=candidate_model" in compatibility_runner
