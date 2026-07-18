"""Architecture guardrails after removal of the V1 candidate-memory core."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_candidate_memory_model_and_api_modules_are_absent():
    assert not (ROOT / "src/memory/models/candidate_memory.py").exists()
    assert not (ROOT / "src/memory/api/candidates.py").exists()
    assert not (ROOT / "src/memory/api/commit.py").exists()


def test_only_working_agent_governance_constructs_formal_memories():
    writers: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "CommittedMemory(" in text and path.name != "committed_memory.py":
            writers.append(path.relative_to(ROOT).as_posix())
    assert writers == ["src/execution/services/memory_commit_service.py"]
