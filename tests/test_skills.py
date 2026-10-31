"""Unit tests for Dynamic Skill Loader and Runner (skills/ - BEH-066)."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from beherit.api.routes import repo
from beherit.core.repository import AgentRepository
from beherit.core.skills import SkillManager
from beherit.core.tools import CapabilitiesGuard
from beherit.engines.base import BaseAgentEngine
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


@pytest.fixture
def skills_agent(tmp_path: Path):
    """Setup a test agent with workspace and skills folder."""
    repo_inst = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="Agent z Umiejętnościami",
        slug="skills-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś asystentem.",
    )
    repo_inst.save_agent(agent)
    loaded_agent = repo_inst.load_agent(agent.slug)
    return loaded_agent, tmp_path


def test_discover_skills(skills_agent):
    """Verify scanning skills/ discovers Python and Bash scripts with their docstrings."""
    agent, tmp_path = skills_agent
    smgr = SkillManager(base_dir=tmp_path)
    skills_dir = smgr.get_skills_dir(agent)

    # 1. Python skill
    py_skill = skills_dir / "calc_square.py"
    py_skill.write_text('"""Oblicza kwadrat podanej liczby x."""\nimport sys, json\nval = int(sys.argv[1]) if len(sys.argv) > 1 else 2\nprint(json.dumps({"result": val * val}))\n')

    # 2. Bash skill
    sh_skill = skills_dir / "system_info.sh"
    sh_skill.write_text('#!/usr/bin/env bash\n# Description: Zwraca informacje o systemie hosta\necho \'{"os": "linux", "status": "ok"}\'\n')
    sh_skill.chmod(0o755)

    skills = smgr.list_skills(agent)
    assert len(skills) == 2
    names = {s.name: s for s in skills}

    assert "calc_square" in names
    assert names["calc_square"].skill_type == "python"
    assert "kwadrat podanej liczby" in names["calc_square"].description

    assert "system_info" in names
    assert names["system_info"].skill_type == "bash"
    assert "Zwraca informacje o systemie" in names["system_info"].description


@pytest.mark.asyncio
async def test_execute_python_skill(skills_agent):
    """Verify executing python skill with JSON input/output."""
    agent, tmp_path = skills_agent
    smgr = SkillManager(base_dir=tmp_path)
    skills_dir = smgr.get_skills_dir(agent)

    py_skill = skills_dir / "multiply.py"
    py_skill.write_text("""import sys, json
raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
if not raw and len(sys.argv) > 1:
    raw = sys.argv[1]
data = json.loads(raw) if raw else {"a": 3, "b": 4}
print(json.dumps({"product": data["a"] * data["b"]}))
""")

    result = await smgr.execute_skill(agent, "multiply", args={"a": 6, "b": 7})
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["data"] == {"product": 42}


@pytest.mark.asyncio
async def test_execute_bash_skill(skills_agent):
    """Verify executing bash skill script."""
    agent, tmp_path = skills_agent
    smgr = SkillManager(base_dir=tmp_path)
    skills_dir = smgr.get_skills_dir(agent)

    sh_skill = skills_dir / "greet.sh"
    sh_skill.write_text('#!/usr/bin/env bash\necho "Witaj, $1!"\n')
    sh_skill.chmod(0o755)

    result = await smgr.execute_skill(agent, "greet", args="Swarm")
    assert result["success"] is True
    assert "Witaj, Swarm!" in result["stdout"]


@pytest.mark.asyncio
async def test_capabilities_guard_skill_dispatch(skills_agent):
    """Verify CapabilitiesGuard exposes and executes dynamic skills."""
    agent, tmp_path = skills_agent
    smgr = SkillManager(base_dir=tmp_path)
    skills_dir = smgr.get_skills_dir(agent)

    py_skill = skills_dir / "double_val.py"
    py_skill.write_text('"""Podwaja liczbę."""\nimport sys, json\nargs = json.loads(sys.argv[1])\nprint(json.dumps({"doubled": args["n"] * 2}))\n')

    guard = CapabilitiesGuard(default_workspace=tmp_path)

    # 1. Test get_tools_schema
    tools = guard.get_tools_schema(agent, provider="gemini")
    func_names = [f["name"] for f in tools[0]["functionDeclarations"]]
    assert "list_skills" in func_names
    assert "run_skill" in func_names
    assert "skill_double_val" in func_names

    # 2. Test list_skills tool
    list_res = await guard.dispatch_tool(agent, "list_skills")
    assert list_res["success"] is True
    assert list_res["total"] == 1
    assert list_res["skills"][0]["name"] == "double_val"

    # 3. Test run_skill tool
    run_res = await guard.dispatch_tool(agent, "run_skill", {"skill_name": "double_val", "args": {"n": 21}})
    assert run_res["success"] is True
    assert run_res["data"] == {"doubled": 42}

    # 4. Test direct skill_double_val invocation
    direct_res = await guard.dispatch_tool(agent, "skill_double_val", {"args": {"n": 50}})
    assert direct_res["success"] is True
    assert direct_res["data"] == {"doubled": 100}


def test_system_context_includes_skills(skills_agent):
    """Verify build_system_context lists skills discovered in skills/."""
    agent, tmp_path = skills_agent
    smgr = SkillManager(base_dir=tmp_path)
    skills_dir = smgr.get_skills_dir(agent)

    py_skill = skills_dir / "benchmark.py"
    py_skill.write_text('"""Wykonuje testy wydajnościowe."""\nprint("ok")\n')

    ctx = BaseAgentEngine.build_system_context(agent)
    assert "## Dostępne Umiejętności Skryptowe" in ctx
    assert "benchmark" in ctx
    assert "testy wydajnościowe" in ctx


def test_skills_rest_endpoints(skills_agent, monkeypatch):
    """Verify REST API endpoints for managing and executing skills."""
    agent, tmp_path = skills_agent
    monkeypatch.setattr(repo, "base_dir", tmp_path)

    # 1. Create skill via API
    create_resp = client.post(
        f"/api/agents/{agent.slug}/skills",
        json={
            "name": "api_ping",
            "skill_type": "python",
            "content": '"""Wysyła ping."""\nprint("PONG")',
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["status"] == "created"
    assert create_resp.json()["skill"]["name"] == "api_ping"

    # 2. List skills via API
    list_resp = client.get(f"/api/agents/{agent.slug}/skills")
    assert list_resp.status_code == 200
    skills = list_resp.json()["skills"]
    assert len(skills) == 1
    assert skills[0]["name"] == "api_ping"

    # 3. Run skill via API
    run_resp = client.post(f"/api/agents/{agent.slug}/skills/api_ping/run", json={})
    assert run_resp.status_code == 200
    assert run_resp.json()["status"] == "success"
    assert "PONG" in run_resp.json()["result"]["stdout"]

    # 4. Delete skill via API
    del_resp = client.delete(f"/api/agents/{agent.slug}/skills/api_ping")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # 5. Verify deleted
    list_after = client.get(f"/api/agents/{agent.slug}/skills")
    assert list_after.json()["total"] == 0
