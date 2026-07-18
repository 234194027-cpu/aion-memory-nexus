import uuid

def generate_id(prefix: str = "") -> str:
    base_id = str(uuid.uuid4()).replace("-", "")[:16]
    if prefix:
        return f"{prefix}_{base_id}"
    return base_id

def generate_event_id() -> str:
    return generate_id("evt")

def generate_memory_id() -> str:
    return generate_id("mem")

def generate_source_id() -> str:
    return generate_id("src")

def generate_agent_id() -> str:
    return generate_id("agt")

def generate_user_id() -> str:
    return generate_id("usr")

def generate_embedding_id() -> str:
    return generate_id("emb")

def generate_decision_id() -> str:
    return generate_id("dec")

def generate_weekly_review_id() -> str:
    return generate_id("wrv")

def generate_persona_snapshot_id() -> str:
    return generate_id("psn")

def generate_task_id() -> str:
    return generate_id("tsk")

def generate_timeline_entry_id() -> str:
    return generate_id("tle")

def generate_agent_permission_id() -> str:
    return generate_id("perm")

def generate_simulation_run_id() -> str:
    return generate_id("sim")

def generate_audit_log_id() -> str:
    return generate_id("aud")

def generate_decision_review_id() -> str:
    return generate_id("drv")

def generate_conflict_record_id() -> str:
    return generate_id("cfl")

def generate_memory_relation_id() -> str:
    return generate_id("mrel")

def generate_advisor_session_id() -> str:
    return generate_id("adv")

def generate_belief_id() -> str:
    return generate_id("blf")

def generate_conflict_edge_id() -> str:
    return generate_id("cfe")


def generate_knowledge_page_id() -> str:
    return generate_id("kwp")


def generate_knowledge_page_memory_id() -> str:
    return generate_id("kwm")


def generate_lifecycle_audit_id() -> str:
    return generate_id("lca")


def generate_knowledge_page_version_id() -> str:
    return generate_id("kwv")
