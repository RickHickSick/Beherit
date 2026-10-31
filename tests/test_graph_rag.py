"""Unit tests for Obsidian Wikilinks & Graph RAG (BEH-067)."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from beherit.api.routes import repo, db_mgr
from beherit.core.rag import (
    KnowledgeRAG,
    extract_wikilinks,
    normalize_doc_name,
)
from beherit.core.repository import AgentRepository
from beherit.core.tools import CapabilitiesGuard
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


def test_extract_wikilinks():
    """Verify regex extraction of various Obsidian wikilink patterns."""
    text = """
    Sprawdź dokumentację w [[Architektura Systemu]] oraz [[Baza Danych|Schemat SQL]].
    Dodatkowo przeczytaj sekcję [[API#Uwierzytelnianie]] oraz [[Bezpieczeństwo#Reguły|Zasady Firewall]].
    """
    links = extract_wikilinks(text)
    assert len(links) == 4
    assert links[0] == ("Architektura Systemu", "")
    assert links[1] == ("Baza Danych", "Schemat SQL")
    assert links[2] == ("API", "")
    assert links[3] == ("Bezpieczeństwo", "Zasady Firewall")


def test_normalize_doc_name():
    """Verify resolving wikilink target to file in knowledge/ directory."""
    existing = {
        "architektura_systemu.md": Path("architektura_systemu.md"),
        "baza-danych.md": Path("baza-danych.md"),
        "manual.pdf": Path("manual.pdf"),
    }
    assert normalize_doc_name("architektura_systemu", existing) == "architektura_systemu.md"
    assert normalize_doc_name("Baza-Danych", existing) == "baza-danych.md"
    assert normalize_doc_name("manual.pdf", existing) == "manual.pdf"
    assert normalize_doc_name("nieznana-notatka", existing) == "nieznana-notatka.md"


@pytest.fixture
def graph_agent(tmp_path: Path):
    """Setup a test agent with connected knowledge notes."""
    repo_inst = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="Agent Grafowy",
        slug="graph-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś asystentem analitycznym.",
    )
    repo_inst.save_agent(agent)
    loaded_agent = repo_inst.load_agent(agent.slug)

    agent_dir = Path(loaded_agent.file_path).parent
    kdir = agent_dir / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)

    # Note A links to Note B and Note C
    (kdir / "architektura.md").write_text(
        "# Architektura Beherit\nSystem składa się z silnika [[silnik_rag.md]] oraz bazy danych [[baza_sqlite|Baza SQLite]].\n",
        encoding="utf-8",
    )
    # Note B links to Note D
    (kdir / "silnik_rag.md").write_text(
        "# Silnik RAG\nSilnik korzysta z modułu [[wektory.md]] do wyszukiwania semantycznego.\n",
        encoding="utf-8",
    )
    # Note C
    (kdir / "baza_sqlite.md").write_text(
        "# Baza Danych SQLite\nPrzechowuje historię i tabele [[architektura.md]].\n",
        encoding="utf-8",
    )
    # Note D
    (kdir / "wektory.md").write_text(
        "# Wektory i Embeddings\nWektory służą do dopasowywania zapytań użytkownika.\n",
        encoding="utf-8",
    )

    return loaded_agent, tmp_path


def test_sync_and_get_graph(graph_agent):
    """Verify sync indexes wikilinks and get_graph returns nodes and edges."""
    agent, _ = graph_agent
    rag = KnowledgeRAG(db_manager=db_mgr)
    kdir = Path(agent.file_path).parent / "knowledge"

    total_chunks = rag.sync_agent_knowledge(agent.slug, kdir)
    assert total_chunks >= 4

    graph = rag.get_graph(agent.slug)
    assert graph["total_nodes"] >= 4
    assert graph["total_edges"] >= 4

    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("architektura.md", "silnik_rag.md") in edges
    assert ("architektura.md", "baza_sqlite.md") in edges
    assert ("silnik_rag.md", "wektory.md") in edges
    assert ("baza_sqlite.md", "architektura.md") in edges


def test_1hop_and_2hop_expansion(graph_agent):
    """Verify 1-hop and 2-hop graph expansion from a seed document."""
    agent, _ = graph_agent
    rag = KnowledgeRAG(db_manager=db_mgr)
    kdir = Path(agent.file_path).parent / "knowledge"
    rag.sync_agent_knowledge(agent.slug, kdir)

    # Expand from 'architektura.md':
    # 1-hop: silnik_rag.md, baza_sqlite.md
    # 2-hop: wektory.md (linked via silnik_rag.md)
    expansion = rag.expand_graph(agent.slug, ["architektura.md"], max_hops=2)

    assert "silnik_rag.md" in expansion
    assert expansion["silnik_rag.md"]["hops"] == 1

    assert "baza_sqlite.md" in expansion
    assert expansion["baza_sqlite.md"]["hops"] == 1

    assert "wektory.md" in expansion
    assert expansion["wektory.md"]["hops"] == 2
    assert expansion["wektory.md"]["connected_to"] == "silnik_rag.md"


def test_get_document_backlinks(graph_agent):
    """Verify retrieving backlinks for a target document."""
    agent, _ = graph_agent
    rag = KnowledgeRAG(db_manager=db_mgr)
    kdir = Path(agent.file_path).parent / "knowledge"
    rag.sync_agent_knowledge(agent.slug, kdir)

    backlinks = rag.get_document_backlinks(agent.slug, "silnik_rag.md")
    assert len(backlinks) >= 1
    sources = [b["source_doc"] for b in backlinks]
    assert "architektura.md" in sources


def test_build_knowledge_context_injects_graph_expansion(graph_agent):
    """Verify search in build_knowledge_context includes 1-hop and 2-hop linked notes."""
    agent, _ = graph_agent
    rag = KnowledgeRAG(db_manager=db_mgr)

    # Search query targeting 'Architektura'
    context = rag.build_knowledge_context(agent, query="Architektura Beherit")
    assert "## 📚 Baza Wiedzy Agenta" in context
    assert "### 🕸️ Graf Powiązań Wiedzy" in context or "### 🔍 Dopasowane Fragmenty" in context


@pytest.mark.asyncio
async def test_capabilities_guard_graph_tool(graph_agent):
    """Verify CapabilitiesGuard executes query_knowledge_graph tool."""
    agent, tmp_path = graph_agent
    rag = KnowledgeRAG(db_manager=db_mgr)
    kdir = Path(agent.file_path).parent / "knowledge"
    rag.sync_agent_knowledge(agent.slug, kdir)

    guard = CapabilitiesGuard(default_workspace=tmp_path)

    # 1. Full graph query
    res_full = await guard.dispatch_tool(agent, "query_knowledge_graph")
    assert res_full["success"] is True
    assert "graph" in res_full
    assert res_full["graph"]["total_nodes"] >= 4

    # 2. Targeted document query
    res_doc = await guard.dispatch_tool(agent, "query_knowledge_graph", {"doc_name": "silnik_rag.md"})
    assert res_doc["success"] is True
    assert res_doc["doc_name"] == "silnik_rag.md"
    assert len(res_doc["backlinks"]) >= 1
    assert "wektory.md" in res_doc["graph_expansion"]


def test_graph_rest_endpoint(graph_agent, monkeypatch):
    """Verify GET /api/agents/{slug}/knowledge/graph endpoint."""
    agent, tmp_path = graph_agent
    monkeypatch.setattr(repo, "base_dir", tmp_path)

    resp = client.get(f"/api/agents/{agent.slug}/knowledge/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == agent.slug
    assert "graph" in data
    assert data["graph"]["total_nodes"] >= 4
    assert data["graph"]["total_edges"] >= 4
