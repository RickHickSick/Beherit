"""Tests for Agent Tool Execution and Capabilities Guard."""

import pytest
from pathlib import Path
from beherit.core.tools import CapabilitiesGuard
from beherit.models.agent import AgentModel, AgentCapabilities, TerminalMode


@pytest.mark.asyncio
async def test_terminal_disabled(tmp_path: Path):
    """Verify that commands are blocked when terminal is disabled."""
    guard = CapabilitiesGuard(default_workspace=tmp_path)
    agent = AgentModel(
        name="Passive Agent",
        slug="passive-agent",
        capabilities=AgentCapabilities(terminal=False, terminal_mode=TerminalMode.DISABLED),
    )

    result = await guard.execute_bash(agent, "echo 'hello'")
    assert result["success"] is False
    assert "disabled" in result["error"].lower()


@pytest.mark.asyncio
async def test_terminal_restricted_sandbox(tmp_path: Path):
    """Verify restricted execution in workspace and blocking traversal."""
    guard = CapabilitiesGuard(default_workspace=tmp_path)
    agent = AgentModel(
        name="Sandboxed Agent",
        slug="sandboxed-agent",
        capabilities=AgentCapabilities(terminal=True, terminal_mode=TerminalMode.RESTRICTED),
    )

    # Valid command in workspace
    result = await guard.execute_bash(agent, "echo 'sandboxed' > output.txt && cat output.txt")
    assert result["success"] is True
    assert "sandboxed" in result["stdout"]

    # Blocked traversal attempt
    result_escape = await guard.execute_bash(agent, "cd / && ls -la")
    assert result_escape["success"] is False
    assert "forbidden" in result_escape["error"].lower() or "denied" in result_escape["error"].lower()

    # Blocked sensitive path (/etc)
    result_sensitive = await guard.execute_bash(agent, "cat /etc/passwd")
    assert result_sensitive["success"] is False
    assert "forbidden" in result_sensitive["error"].lower()

    # Blocked parent traversal
    result_traversal = await guard.execute_bash(agent, "cd .. && ls")
    assert result_traversal["success"] is False
    assert "forbidden" in result_traversal["error"].lower()


@pytest.mark.asyncio
async def test_terminal_unrestricted_freedom(tmp_path: Path):
    """Verify full freedom when unrestricted mode is chosen by user."""
    guard = CapabilitiesGuard(default_workspace=tmp_path)
    agent = AgentModel(
        name="Power Agent",
        slug="power-agent",
        capabilities=AgentCapabilities(terminal=True, terminal_mode=TerminalMode.UNRESTRICTED),
    )

    # Full execution allows arbitrary host command execution
    result = await guard.execute_bash(agent, "uname -s")
    assert result["success"] is True
    assert result["mode"] == "unrestricted"
    assert "Linux" in result["stdout"] or len(result["stdout"]) > 0


def test_file_read_and_write_containment(tmp_path: Path):
    """Verify file read and write operations."""
    guard = CapabilitiesGuard(default_workspace=tmp_path)
    agent = AgentModel(
        name="Writer Agent",
        slug="writer-agent",
        capabilities=AgentCapabilities(file_read=True, file_write=True, terminal_mode=TerminalMode.RESTRICTED),
    )

    # Write inside workspace
    write_res = guard.write_file(agent, "notes/log.txt", "Audit entry 1")
    assert write_res["success"] is True

    # Read inside workspace
    read_res = guard.read_file(agent, "notes/log.txt")
    assert read_res["success"] is True
    assert read_res["content"] == "Audit entry 1"

    # Restricted mode blocks writing outside
    outside_res = guard.write_file(agent, "/tmp/evil_file.txt", "data")
    assert outside_res["success"] is False
    assert "outside agent workspace" in outside_res["error"]


@pytest.mark.asyncio
async def test_tool_dispatcher(tmp_path: Path):
    """Verify BEH-016: CapabilitiesGuard dispatch_tool executes supported tools."""
    guard = CapabilitiesGuard(default_workspace=tmp_path)
    agent = AgentModel(
        name="Tool Agent",
        slug="tool-agent",
        capabilities=AgentCapabilities(
            terminal=True,
            terminal_mode=TerminalMode.RESTRICTED,
            file_read=True,
            file_write=True,
        ),
    )

    # 1. Dispatch write_file
    res_write = await guard.dispatch_tool(agent, "write_file", {"path": "test.txt", "content": "123"})
    assert res_write["success"] is True

    # 2. Dispatch read_file
    res_read = await guard.dispatch_tool(agent, "read_file", {"path": "test.txt"})
    assert res_read["success"] is True
    assert res_read["content"] == "123"

    # 3. Dispatch list_dir
    res_list = await guard.dispatch_tool(agent, "list_dir", {"path": "."})
    assert res_list["success"] is True
    assert any(item["name"] == "test.txt" for item in res_list["items"])

    # 4. Unknown tool
    res_unknown = await guard.dispatch_tool(agent, "non_existent_tool", {})
    assert res_unknown["success"] is False
    assert "Unknown tool" in res_unknown["error"]
