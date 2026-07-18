import importlib.util
import json
import zipfile
from pathlib import Path


def _load_portable_mcp_module():
    server_path = Path("skills/life-memory-mcp-connect/scripts/life_memory_mcp_server.py")
    spec = importlib.util.spec_from_file_location("portable_life_memory_mcp_server", server_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_mcp_contract_is_identical_for_repo_and_portable_proxy():
    from src.platform.mcp.server import TOOL_DEFS

    portable = _load_portable_mcp_module()
    repo_tools = {tool["name"]: tool for tool in TOOL_DEFS}
    portable_tools = {tool["name"]: tool for tool in portable.TOOLS}

    assert "memory_map" in repo_tools
    assert "memory_map" in portable_tools
    assert "memory_list_types" in repo_tools
    assert "memory_list_types" in portable_tools
    assert repo_tools == portable_tools


def test_memory_map_contract_has_agent_operating_protocol():
    from src.platform.mcp.server import memory_access_map as repo_map

    portable = _load_portable_mcp_module()

    repo_payload = repo_map()
    portable_payload = portable.memory_access_map()

    for payload in (repo_payload, portable_payload):
        assert payload["version"]
        assert payload["system_goal"].startswith("A shared long-term memory layer")
        assert payload["principle"].startswith("Agents read committed memory")
        assert any(
            "avoid asking the user to repeat" in item.lower()
            for item in payload["agent_benefits"]
        )
        assert any("Call memory_map" in item for item in payload["quickstart"])
        assert payload["habit_loop"]["before"].startswith("Call memory_before_start")
        assert "rtk_powershell" in payload["troubleshooting"]
        assert "sync_timeout" in payload["troubleshooting"]
        assert "installer" in payload["packaged_helpers"]
        assert "payload_validator" in payload["packaged_helpers"]
        assert "memory_before_start" in payload["tool_groups"]["read"]
        assert "memory_upload_daily_delta" in payload["tool_groups"]["write"]
        assert "context_tiers" in payload["context_fields"]
        assert "context_tree" in payload["context_fields"]
        assert any(
            step["tool"] == "memory_upload_daily_delta"
            for step in payload["recommended_flow"]
        )

    assert repo_payload == portable_payload
    assert repo_payload["version"] == "3.0.0"
    assert "Graphiti" not in json.dumps(repo_payload, ensure_ascii=False)


def test_admin_agent_helpers_accept_list_or_json_string_and_prompt_map():
    from src.platform.api.admin.agents import _mcp_test_prompt, _parse_json_list

    assert _parse_json_list(["a", "b"]) == ["a", "b"]
    assert _parse_json_list('["a", "b"]') == ["a", "b"]
    assert _parse_json_list('"not-a-list"') == []
    assert _parse_json_list("not-json") == []

    class Agent:
        id = "agent_test"

    prompt = _mcp_test_prompt(Agent())
    assert "memory_map" in prompt
    assert "recommended_flow" in prompt


def test_media_ingestion_skill_documents_required_mcp_tools():
    skill_dir = Path("skills/life-memory-media-ingestion")
    skill_doc = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    contract = (skill_dir / "templates" / "mcp-tool-contract.md").read_text(encoding="utf-8")
    expected_tools = {
        "memory_create_link_artifact",
        "memory_upload_media_base64",
        "memory_list_media_artifacts",
        "memory_get_media_artifact",
        "memory_extract_media_artifact",
    }

    for tool_name in expected_tools:
        assert tool_name in skill_doc
        assert tool_name in contract


def test_agent_bootstrap_prompt_stays_short_and_delegates_to_skill_map():
    prompt = Path(
        "skills/life-memory-mcp-connect/references/agent-bootstrap-prompt.md"
    ).read_text(encoding="utf-8")

    assert len(prompt) < 1800
    assert "memory_map" in prompt
    assert "memory_list_types" in prompt
    assert "memory_before_start" in prompt
    assert "RawEvent" in prompt
    assert "Graphiti/Neo4j" in prompt
    assert "context_tiers.L0" not in prompt
    assert "relation_graph" not in prompt


def test_windows_codex_troubleshooting_and_config_writer_are_packaged():
    skill_dir = Path("skills/life-memory-mcp-connect")
    guide = (skill_dir / "references" / "windows-codex-troubleshooting.md").read_text(
        encoding="utf-8"
    )
    writer = (skill_dir / "scripts" / "write_codex_mcp_config.py").read_text(
        encoding="utf-8"
    )

    assert "rtk powershell -NoProfile -Command" in guide
    assert "trigger_extraction=false" in guide
    assert "work_case_count" in guide
    assert "BEGIN life-memory-mcp-connect" in writer
    assert "<redacted>" in writer
    assert (skill_dir / "scripts" / "install_life_memory_skill.py").exists()
    assert (skill_dir / "scripts" / "doctor.py").exists()
    assert (skill_dir / "scripts" / "validate_memory_batch.py").exists()
    assert (skill_dir / "references" / "automation-templates.md").exists()


def test_codex_config_writer_replaces_old_life_memory_tables_without_leaking_token():
    import importlib.util

    script_path = Path("skills/life-memory-mcp-connect/scripts/write_codex_mcp_config.py")
    spec = importlib.util.spec_from_file_location("write_codex_mcp_config", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    old_config = """
[mcp_servers.'life-memory']
command = 'python'
args = ['old.py']

[mcp_servers.'life-memory'.env]
LIFE_MEMORY_AGENT_TOKEN = 'old-token'

[mcp_servers.node_repl]
command = 'node'
"""
    block = module._block("http://example.test", "agent_1", "secret-token", Path("server.py"))
    updated = module._upsert_block(old_config, block)
    redacted = module._redact_block(block)

    assert "old-token" not in updated
    assert "[mcp_servers.node_repl]" in updated
    assert "secret-token" in updated
    assert "secret-token" not in redacted
    assert "<redacted>" in redacted


def test_memory_batch_validator_cleans_risky_identifiers():
    import importlib.util

    script_path = Path("skills/life-memory-mcp-connect/scripts/validate_memory_batch.py")
    spec = importlib.util.spec_from_file_location("validate_memory_batch", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    cleaned = module.clean_payload(
        {
            "source_name": "codex_global_memory",
            "default_project_id": "F:\\中文\\项目",
            "memories": [
                {
                    "title": "Path case",
                    "content": "Remember the safe project preference.",
                    "external_id": "F:\\中文\\记忆.md",
                    "project_id": "中文项目",
                    "metadata": {},
                }
            ],
        }
    )

    item = cleaned["memories"][0]
    assert "\\" not in item["external_id"]
    assert "\\" not in item["project_id"]
    assert item["metadata"]["original_external_id"] == "F:\\中文\\记忆.md"
    assert cleaned["validation_warnings"]


def test_v3_skill_sources_have_no_legacy_candidate_or_http_distribution_terms():
    skill_roots = [
        Path("skills/life-memory-mcp-connect"),
        Path("skills/life-memory-media-ingestion"),
    ]
    forbidden = ("CandidateMemory", "candidate_auto_commit", "candidate_count")
    for root in skill_roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".md", ".py", ".yaml", ".json"}:
                text = path.read_text(encoding="utf-8")
                assert not any(term in text for term in forbidden), path


def test_release_manifest_matches_zip_hashes_and_safe_entries():
    import hashlib

    release_dir = Path("static/skills/releases/3.0.0")
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skill_version"] == "3.0.0"
    artifacts = {artifact["name"]: artifact for artifact in manifest["artifacts"]}
    for name in ("life-memory-mcp-connect-skill", "life-memory-media-ingestion-skill"):
        artifact = artifacts[name]
        zip_path = release_dir / Path(artifact["path"]).name
        assert hashlib.sha256(zip_path.read_bytes()).hexdigest() == artifact["sha256"]
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
        assert names == artifact["files"]
        assert all(".." not in Path(name).parts and not name.startswith("/") for name in names)


def test_public_bootstrap_project_binding_helper_rejects_cross_project():
    from fastapi import HTTPException
    from src.execution.api.agents import _bound_project_or_forbidden

    class Agent:
        constraints = ["mcp_bootstrap_project:demo-project"]

    assert _bound_project_or_forbidden(Agent(), None) == "demo-project"
    assert _bound_project_or_forbidden(Agent(), "demo-project") == "demo-project"
    try:
        _bound_project_or_forbidden(Agent(), "other-project")
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("cross-project bootstrap request was not rejected")
