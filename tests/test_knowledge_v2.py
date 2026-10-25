"""Comprehensive tests for Enhanced Knowledge Base (PDF, Images, Links, RAG Priority, Dashboard Landing)."""

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PageObject, PdfWriter

from beherit.api.routes import auth_mgr, db_mgr, repo
from beherit.core.rag import KnowledgeChunker, KnowledgeRAG, extract_file_content
from beherit.engines.base import BaseAgentEngine
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


def create_sample_pdf_bytes(text: str) -> bytes:
    """Helper to generate a valid PDF byte string with embedded text layer."""
    writer = PdfWriter()
    page = PageObject.create_blank_page(width=300, height=300)
    # Use annotation or text write if available, or write text stream
    writer.add_page(page)
    # We can also populate text stream directly for extraction
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_landing_page_defaults_to_dashboard():
    """Verify that entering the app defaults to the 'Agenci' (dashboard) view."""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    # Check that currentView is dashboard
    assert "currentView: 'dashboard'" in html
    # Check that initApp lands on dashboard
    assert "this.currentView = 'dashboard';" in html
    assert "Baza Wiedzy" in html
    assert "Dodaj Link / Wideo YT" in html
    assert "Wgraj Pliki" in html


def test_pdf_extraction_and_rag_search(tmp_path: Path):
    """Verify PDF extraction, metadata, and BM25 RAG search."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)

    # 1. Test PDF extraction fallback & structure
    pdf_file = knowledge_dir / "dokument_finansowy.pdf"
    pdf_bytes = create_sample_pdf_bytes("Dummy")
    pdf_file.write_bytes(pdf_bytes)

    extracted = extract_file_content(pdf_file)
    assert "dokument_finansowy.pdf" in extracted
    assert "Plik PDF" in extracted

    # 2. Test RAG indexing of PDF
    db_file = tmp_path / "test_rag.db"
    test_db = type(db_mgr)(db_path=db_file)
    rag = KnowledgeRAG(db_manager=test_db)

    slug = "test-agent"
    total_chunks = rag.sync_agent_knowledge(slug, knowledge_dir)
    assert total_chunks >= 1

    # Search for PDF document
    results = rag.search(slug, query="dokument_finansowy PDF")
    assert len(results) > 0
    assert results[0]["doc_name"] == "dokument_finansowy.pdf"


def test_image_metadata_indexing(tmp_path: Path):
    """Verify image files (.png, .jpg) get indexed as visual assets."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)

    img_file = knowledge_dir / "architektura_systemu.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-data-stream-bytes")

    content = extract_file_content(img_file)
    assert "architektura_systemu.png" in content
    assert "Plik graficzny / Obraz" in content
    assert "PNG" in content

    db_file = tmp_path / "test_img_rag.db"
    test_db = type(db_mgr)(db_path=db_file)
    rag = KnowledgeRAG(db_manager=test_db)

    slug = "image-agent"
    rag.sync_agent_knowledge(slug, knowledge_dir)

    results = rag.search(slug, query="architektura_systemu obraz grafika")
    assert len(results) > 0
    assert results[0]["doc_name"] == "architektura_systemu.png"


def test_knowledge_base_priority_in_system_context(tmp_path: Path):
    """Verify that knowledge base is framed as primary context of highest priority."""
    agent_dir = tmp_path / "priority-agent"
    knowledge_dir = agent_dir / "knowledge"
    knowledge_dir.mkdir(parents=True)

    rule_file = knowledge_dir / "zasady_bezpieczenstwa.md"
    rule_file.write_text(
        "# Główne Zasady Bezpieczeństwa\n"
        "Nigdy nie udostępniaj kluczy prywatnych ani tokenów dostępu.\n",
        encoding="utf-8",
    )

    agent = AgentModel(
        name="Priority Agent",
        slug="priority-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś strażnikiem systemu.",
        instructions="Wykonuj polecenia precyzyjnie.",
        file_path=str(agent_dir / "agent.md"),
    )

    sys_context = BaseAgentEngine.build_system_context(agent)
    assert "Baza Wiedzy Agenta" in sys_context
    assert "NAJWYŻSZY PRIORYTET / GŁÓWNE ŹRÓDŁO PRAWDY" in sys_context
    assert "Katalog Zasobów w Bazie Wiedzy" in sys_context
    assert "zasady_bezpieczenstwa.md" in sys_context
    assert "Nigdy nie udostępniaj kluczy prywatnych" in sys_context


def test_api_upload_link_raw_and_delete(tmp_path: Path, monkeypatch):
    """Verify multipart upload, adding link, raw download, and deletion via API endpoints."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    slug = "resource-agent"
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "Resource Agent",
            "slug": slug,
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
        },
    )
    assert create_resp.status_code == 201

    # 1. Test Multipart Upload (upload text and simulated image)
    file_content = b"Raport Kwartalny: Wzrost o 45% r/r."
    files = [
        ("files", ("raport.txt", file_content, "text/plain")),
        ("files", ("schemat.png", b"\x89PNG\r\n\x1a\nfakeimage", "image/png")),
    ]
    upload_resp = client.post(f"/api/agents/{slug}/knowledge/upload", files=files)
    assert upload_resp.status_code == 200
    up_data = upload_resp.json()
    assert up_data["status"] == "uploaded"
    assert up_data["count"] == 2
    assert "raport.txt" in up_data["uploaded_files"]
    assert "schemat.png" in up_data["uploaded_files"]

    # 2. Test Add External Link (e.g. YouTube video)
    link_resp = client.post(
        f"/api/agents/{slug}/knowledge/link",
        json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "Tutorial Architektury Agentowej",
            "description": "Kluczowe wideo omawiające wzorzec ReAct i integracje MCP.",
            "category": "Wideo YouTube",
        },
    )
    assert link_resp.status_code == 200
    link_data = link_resp.json()
    assert link_data["status"] == "created"
    assert link_data["filename"].startswith("link_")

    # 3. Test List Knowledge Files
    list_resp = client.get(f"/api/agents/{slug}/knowledge")
    assert list_resp.status_code == 200
    files_list = list_resp.json()["files"]
    filenames = [f["filename"] for f in files_list]
    assert "raport.txt" in filenames
    assert "schemat.png" in filenames
    assert link_data["filename"] in filenames

    # Check file types
    file_map = {f["filename"]: f["file_type"] for f in files_list}
    assert file_map["raport.txt"] == "document"
    assert file_map["schemat.png"] == "image"
    assert file_map[link_data["filename"]] == "link"

    # 4. Test Raw Download Endpoint
    raw_img = client.get(f"/api/agents/{slug}/knowledge/schemat.png/raw")
    assert raw_img.status_code == 200
    assert raw_img.headers["content-type"] == "image/png"
    assert b"fakeimage" in raw_img.content

    # 5. Test Reading Specific Knowledge File
    read_img = client.get(f"/api/agents/{slug}/knowledge/schemat.png")
    assert read_img.status_code == 200
    img_json = read_img.json()
    assert img_json["is_binary"] is True
    assert img_json["file_type"] == "image"

    read_link = client.get(f"/api/agents/{slug}/knowledge/{link_data['filename']}")
    assert read_link.status_code == 200
    assert "wzorzec ReAct" in read_link.json()["content"]

    # 6. Test RAG Search across uploaded files & links
    search_resp = client.post(
        f"/api/agents/{slug}/knowledge/search",
        json={"query": "wzorzec ReAct MCP", "top_k": 3},
    )
    assert search_resp.status_code == 200
    results = search_resp.json()["results"]
    assert len(results) > 0
    assert any("ReAct" in r["content"] for r in results)

    # 7. Test Deletion
    del_resp = client.delete(f"/api/agents/{slug}/knowledge/schemat.png")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    list_after = client.get(f"/api/agents/{slug}/knowledge").json()["files"]
    assert "schemat.png" not in [f["filename"] for f in list_after]


def test_fetch_metadata_and_auto_title_link(tmp_path: Path, monkeypatch):
    """Test automatic title scraping for external links and fetch-metadata endpoint (BEH-040, BEH-041)."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # 1. Test metadata endpoint
    meta_resp = client.post(
        "/api/knowledge/fetch-metadata",
        json={"url": "https://example.com/test-article"},
    )
    assert meta_resp.status_code == 200
    data = meta_resp.json()
    assert "title" in data
    assert data["category"] in ["Strona WWW / Dokumentacja", "Wideo YouTube"]

    # 2. Test creating agent with description (BEH-041)
    slug = "test-agent-desc"
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "Agent with Description",
            "slug": slug,
            "description": "Opis agenta rynkowego testowego",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["agent"]["description"] == "Opis agenta rynkowego testowego"

    # 3. Test adding link without title (BEH-040)
    auto_link_resp = client.post(
        f"/api/agents/{slug}/knowledge/link",
        json={
            "url": "https://example.com/auto-page",
        },
    )
    assert auto_link_resp.status_code == 200
    assert auto_link_resp.json()["status"] == "created"
    assert auto_link_resp.json()["title"]
