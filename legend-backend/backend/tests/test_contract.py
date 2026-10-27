import json

from backend.schemas.contract import CONTRACT_PATH, build_contract


def test_committed_contract_matches_schemas() -> None:
    assert CONTRACT_PATH.exists(), "run: python -m backend.schemas.contract"
    committed = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(build_contract()))
    assert committed == current, "contract is stale: run python -m backend.schemas.contract"


def test_contract_covers_every_public_schema() -> None:
    schemas = build_contract()["schemas"]
    assert {"ChatRequest", "ChatResponse", "ChatOptions", "StopResponse", "ErrorEnvelope", "StreamEvent"} <= set(schemas)
