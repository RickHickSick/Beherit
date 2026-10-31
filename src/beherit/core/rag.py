"""Lightweight Local Vector & Semantic Search (Local RAG) for Agent Knowledge Base."""

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from beherit.core.database import DatabaseManager
from beherit.models.agent import AgentModel

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
ALL_KNOWLEDGE_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS | IMAGE_EXTENSIONS


def extract_file_content(file_path: Path) -> str:
    """Extract plain text or metadata description from a document, PDF, or image file."""
    ext = file_path.suffix.lower()

    # 1. Text & structured documents
    if ext in TEXT_EXTENSIONS:
        try:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to read text file {file_path}: {e}")
            return ""

    # 2. PDF documents
    if ext in PDF_EXTENSIONS:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(file_path))
            pages_text = []
            for idx, page in enumerate(reader.pages):
                txt = page.extract_text()
                if txt and txt.strip():
                    pages_text.append(f"--- [Strona {idx + 1} dokumentu {file_path.name}] ---\n{txt.strip()}")
            if pages_text:
                return "\n\n".join(pages_text)
            else:
                return (
                    f"### [Dokument PDF: {file_path.name}]\n"
                    f"Plik PDF w bazie wiedzy agenta ({len(reader.pages)} stron, {file_path.stat().st_size} bajtów). "
                    "Dokument binarny/skan bez wydzielonej warstwy tekstowej."
                )
        except Exception as e:
            logger.warning(f"Error parsing PDF file {file_path}: {e}")
            return f"### [Dokument PDF: {file_path.name}]\nPlik PDF w bazie wiedzy agenta ({file_path.stat().st_size} bajtów)."

    # 3. Image files
    if ext in IMAGE_EXTENSIONS:
        size_kb = round(file_path.stat().st_size / 1024, 1)
        return (
            f"### [Plik graficzny / Obraz: {file_path.name}]\n"
            f"Zasób graficzny w bazie wiedzy agenta. Format: {ext.upper()[1:]}. Rozmiar: {size_kb} KB. "
            f"Ścieżka relatywna: knowledge/{file_path.name}. "
            "Plik dostępny w katalogu agenta do wglądu i analizy kontekstowej."
        )

    # 4. Fallback
    return f"### [Plik: {file_path.name}]\nPlik binarny w bazie wiedzy agenta ({file_path.stat().st_size} bajtów)."


def extract_wikilinks(text: str) -> list[tuple[str, str]]:
    """
    Extract Obsidian wikilinks from markdown or text content.
    Handles standard formats:
    - [[note]]
    - [[note|Custom Display Title]]
    - [[note#Section Heading]]
    - [[note#Section Heading|Custom Display Title]]
    Returns list of (target_name, link_text_or_alias).
    """
    pattern = r"\[\[([^\]|#\n]+)(?:#[^\]|\n]+)?(?:\|([^\]\n]+))?\]\]"
    matches = re.findall(pattern, text)
    results = []
    for target, alias in matches:
        target_clean = target.strip()
        alias_clean = alias.strip() if alias else ""
        if target_clean:
            results.append((target_clean, alias_clean))
    return results


def normalize_doc_name(target: str, existing_docs: dict[str, Path] | None = None) -> str:
    """Resolve a wikilink target to an existing document filename in knowledge/, or canonicalized name."""
    t = target.strip()
    if not existing_docs:
        return f"{t}.md" if not t.endswith(tuple(ALL_KNOWLEDGE_EXTENSIONS)) else t

    if t in existing_docs:
        return t
    if f"{t}.md" in existing_docs:
        return f"{t}.md"

    # Case-insensitive check
    t_lower = t.lower()
    for name in existing_docs:
        if name.lower() == t_lower or name.lower() == f"{t_lower}.md" or Path(name).stem.lower() == t_lower:
            return name

    return f"{t}.md" if not t.endswith(tuple(ALL_KNOWLEDGE_EXTENSIONS)) else t


class KnowledgeChunker:
    """Splits knowledge base documents into semantically coherent overlapping chunks."""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 150):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> list[str]:
        """Split raw text into clean chunks respecting paragraphs and sentences."""
        clean = text.strip()
        if not clean:
            return []
        if len(clean) <= self.chunk_size:
            return [clean]

        chunks = []
        paragraphs = re.split(r"\n\s*\n", clean)
        current_chunk: list[str] = []
        current_len = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # If single paragraph is larger than chunk_size, split by lines or sentences
            if len(para) > self.chunk_size:
                sub_lines = para.split("\n")
                for line in sub_lines:
                    line = line.strip()
                    if not line:
                        continue
                    if current_len + len(line) > self.chunk_size and current_chunk:
                        chunk_str = "\n".join(current_chunk)
                        chunks.append(chunk_str)
                        # Retain overlap
                        overlap_tokens = chunk_str[-self.chunk_overlap:] if len(chunk_str) > self.chunk_overlap else ""
                        current_chunk = [overlap_tokens, line] if overlap_tokens else [line]
                        current_len = sum(len(x) for x in current_chunk)
                    else:
                        current_chunk.append(line)
                        current_len += len(line)
            else:
                if current_len + len(para) > self.chunk_size and current_chunk:
                    chunk_str = "\n\n".join(current_chunk)
                    chunks.append(chunk_str)
                    overlap_tokens = chunk_str[-self.chunk_overlap:] if len(chunk_str) > self.chunk_overlap else ""
                    current_chunk = [overlap_tokens, para] if overlap_tokens else [para]
                    current_len = sum(len(x) for x in current_chunk)
                else:
                    current_chunk.append(para)
                    current_len += len(para)

        if current_chunk:
            final_str = "\n\n".join(current_chunk).strip()
            if final_str:
                chunks.append(final_str)

        return chunks


class KnowledgeRAG:
    """Local RAG Indexer and BM25 / Vector Search engine for Beherit agent knowledge."""

    def __init__(
        self,
        db_manager: DatabaseManager | None = None,
        chunker: KnowledgeChunker | None = None,
    ):
        self.db = db_manager or DatabaseManager()
        self.chunker = chunker or KnowledgeChunker()

    def sync_agent_knowledge(self, agent_slug: str, knowledge_dir: Path) -> int:
        """Scan agent knowledge/ directory and index newly added or modified documents."""
        if not knowledge_dir.exists() or not knowledge_dir.is_dir():
            return 0

        total_chunks = 0
        now = time.time()

        with self.db._get_connection() as conn:
            # Check existing indexed documents and timestamps
            cursor = conn.execute(
                "SELECT doc_name, MAX(updated_at) as last_updated FROM knowledge_chunks WHERE agent_slug = ? GROUP BY doc_name",
                (agent_slug,),
            )
            indexed_docs = {row["doc_name"]: row["last_updated"] for row in cursor.fetchall()}

            current_files = {}
            for doc_file in knowledge_dir.iterdir():
                if doc_file.is_file() and doc_file.suffix.lower() in ALL_KNOWLEDGE_EXTENSIONS:
                    current_files[doc_file.name] = doc_file

            # Purge deleted files from DB
            for old_doc in list(indexed_docs.keys()):
                if old_doc not in current_files:
                    conn.execute("DELETE FROM knowledge_chunks WHERE agent_slug = ? AND doc_name = ?", (agent_slug, old_doc))
                    conn.execute("DELETE FROM knowledge_fts WHERE agent_slug = ? AND doc_name = ?", (agent_slug, old_doc))
                    conn.execute("DELETE FROM knowledge_links WHERE agent_slug = ? AND (source_doc = ? OR target_doc = ?)", (agent_slug, old_doc, old_doc))

            # Index new or modified files
            for doc_name, doc_file in current_files.items():
                mtime = doc_file.stat().st_mtime
                last_idx = indexed_docs.get(doc_name, 0)

                # Reindex if modified
                if doc_name not in indexed_docs or mtime > last_idx:
                    conn.execute("DELETE FROM knowledge_chunks WHERE agent_slug = ? AND doc_name = ?", (agent_slug, doc_name))
                    conn.execute("DELETE FROM knowledge_fts WHERE agent_slug = ? AND doc_name = ?", (agent_slug, doc_name))
                    conn.execute("DELETE FROM knowledge_links WHERE agent_slug = ? AND source_doc = ?", (agent_slug, doc_name))

                    content = extract_file_content(doc_file)
                    if not content or not content.strip():
                        continue

                    # Extract and index Obsidian wikilinks (BEH-067 Graph RAG)
                    wikilinks = extract_wikilinks(content)
                    for target_raw, alias in wikilinks:
                        target_resolved = normalize_doc_name(target_raw, current_files)
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO knowledge_links (agent_slug, source_doc, target_doc, link_text, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (agent_slug, doc_name, target_resolved, alias or target_raw, now),
                        )

                    chunks = self.chunker.split_text(content)
                    for idx, chunk_text in enumerate(chunks):
                        c_cursor = conn.execute(
                            """
                            INSERT INTO knowledge_chunks (agent_slug, doc_name, chunk_index, content, char_count, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (agent_slug, doc_name, idx, chunk_text, len(chunk_text), now),
                        )
                        chunk_id = c_cursor.lastrowid
                        conn.execute(
                            """
                            INSERT INTO knowledge_fts (content, agent_slug, doc_name, chunk_id)
                            VALUES (?, ?, ?, ?)
                            """,
                            (chunk_text, agent_slug, doc_name, str(chunk_id)),
                        )

            conn.commit()

            count_res = conn.execute(
                "SELECT COUNT(*) as total FROM knowledge_chunks WHERE agent_slug = ?",
                (agent_slug,),
            ).fetchone()
            total_chunks = count_res["total"] if count_res else 0

        return total_chunks

    def search(
        self,
        agent_slug: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Execute semantic keyword / BM25 search over the agent's knowledge chunks.
        Returns matching chunks with relevance score and metadata.
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        # Tokenize query into alphanumeric search terms for FTS5
        raw_terms = re.findall(r"\w+", clean_query)
        # Filter terms
        terms = [t for t in raw_terms if len(t) > 1]
        if not terms:
            terms = raw_terms

        fts_query = " OR ".join(f'"{t}"*' for t in terms)

        results: list[dict[str, Any]] = []

        with self.db._get_connection() as conn:
            # 1. Try SQLite FTS5 with BM25 ranking
            if fts_query:
                try:
                    cursor = conn.execute(
                        """
                        SELECT 
                            c.id, c.doc_name, c.chunk_index, c.content,
                            bm25(knowledge_fts) as rank
                        FROM knowledge_fts f
                        JOIN knowledge_chunks c ON c.id = CAST(f.chunk_id AS INTEGER)
                        WHERE knowledge_fts MATCH ? AND f.agent_slug = ?
                        ORDER BY rank ASC
                        LIMIT ?
                        """,
                        (fts_query, agent_slug, top_k * 2),
                    )
                    for row in cursor.fetchall():
                        # bm25 rank is negative or positive lower is better in SQLite FTS5
                        raw_rank = abs(float(row["rank"]))
                        score = round(1.0 / (1.0 + raw_rank), 4)
                        results.append({
                            "id": row["id"],
                            "doc_name": row["doc_name"],
                            "chunk_index": row["chunk_index"],
                            "content": row["content"],
                            "score": score,
                        })
                except sqlite3.OperationalError:
                    pass

            # 2. Fallback / supplementary keyword overlap scoring if FTS returned few results
            if len(results) < top_k:
                cursor = conn.execute(
                    "SELECT id, doc_name, chunk_index, content FROM knowledge_chunks WHERE agent_slug = ?",
                    (agent_slug,),
                )
                query_tokens = set(t.lower() for t in terms)
                for row in cursor.fetchall():
                    if any(r["id"] == row["id"] for r in results):
                        continue
                    text_lower = row["content"].lower()
                    matches = sum(1 for tok in query_tokens if tok in text_lower)
                    if matches > 0:
                        term_score = round(matches / max(len(query_tokens), 1), 4)
                        results.append({
                            "id": row["id"],
                            "doc_name": row["doc_name"],
                            "chunk_index": row["chunk_index"],
                            "content": row["content"],
                            "score": term_score,
                        })

        # Sort descending by relevance score
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get_graph(self, agent_slug: str) -> dict[str, Any]:
        """Return all nodes and directed edges forming the Obsidian knowledge graph (BEH-067)."""
        with self.db._get_connection() as conn:
            doc_rows = conn.execute(
                "SELECT DISTINCT doc_name FROM knowledge_chunks WHERE agent_slug = ?",
                (agent_slug,),
            ).fetchall()
            existing_docs = {r["doc_name"] for r in doc_rows}

            link_rows = conn.execute(
                "SELECT source_doc, target_doc, link_text FROM knowledge_links WHERE agent_slug = ?",
                (agent_slug,),
            ).fetchall()

            all_node_names = set(existing_docs)
            edges = []
            for r in link_rows:
                s = r["source_doc"]
                t = r["target_doc"]
                all_node_names.add(s)
                all_node_names.add(t)
                edges.append({
                    "source": s,
                    "target": t,
                    "label": r["link_text"] or "",
                })

            nodes = []
            for n in sorted(all_node_names):
                nodes.append({
                    "id": n,
                    "label": Path(n).stem,
                    "exists": n in existing_docs,
                    "type": "document" if n in existing_docs else "unresolved",
                })

            return {
                "agent_slug": agent_slug,
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "nodes": nodes,
                "edges": edges,
            }

    def expand_graph(
        self,
        agent_slug: str,
        seed_docs: list[str],
        max_hops: int = 2,
    ) -> dict[str, dict[str, Any]]:
        """
        Perform 1-hop and 2-hop graph expansion for a list of seed documents (BEH-067).
        Traverses outgoing wikilinks and incoming backlinks.
        """
        expanded: dict[str, dict[str, Any]] = {}
        visited = set(seed_docs)
        current_layer = list(seed_docs)

        with self.db._get_connection() as conn:
            for hop in range(1, max_hops + 1):
                if not current_layer:
                    break
                next_layer = []

                for doc in current_layer:
                    # 1. Outgoing links (doc -> target)
                    out_rows = conn.execute(
                        "SELECT target_doc, link_text FROM knowledge_links WHERE agent_slug = ? AND source_doc = ?",
                        (agent_slug, doc),
                    ).fetchall()
                    for r in out_rows:
                        tgt = r["target_doc"]
                        if tgt not in visited:
                            visited.add(tgt)
                            next_layer.append(tgt)
                            expanded[tgt] = {
                                "hops": hop,
                                "connected_to": doc,
                                "direction": "outgoing",
                                "link_text": r["link_text"],
                            }

                    # 2. Incoming links / Backlinks (source -> doc)
                    in_rows = conn.execute(
                        "SELECT source_doc, link_text FROM knowledge_links WHERE agent_slug = ? AND target_doc = ?",
                        (agent_slug, doc),
                    ).fetchall()
                    for r in in_rows:
                        src = r["source_doc"]
                        if src not in visited:
                            visited.add(src)
                            next_layer.append(src)
                            expanded[src] = {
                                "hops": hop,
                                "connected_to": doc,
                                "direction": "incoming",
                                "link_text": r["link_text"],
                            }

                current_layer = next_layer

        return expanded

    def get_document_backlinks(self, agent_slug: str, doc_name: str) -> list[dict[str, str]]:
        """Return all documents that link to the specified document via Obsidian wikilinks."""
        with self.db._get_connection() as conn:
            in_rows = conn.execute(
                "SELECT source_doc, link_text FROM knowledge_links WHERE agent_slug = ? AND target_doc = ?",
                (agent_slug, doc_name),
            ).fetchall()
            return [{"source_doc": r["source_doc"], "link_text": r["link_text"] or ""} for r in in_rows]

    def build_knowledge_context(
        self,
        agent: AgentModel,
        query: str | None = None,
        max_total_chars: int = 12000,
    ) -> str:
        """
        Build optimized knowledge context for the agent turn.
        Presents the knowledge base as the primary, highest-priority source of truth.
        """
        if not agent.file_path:
            return ""

        agent_dir = Path(agent.file_path).parent
        knowledge_dir = agent_dir / "knowledge"
        if not knowledge_dir.exists() or not knowledge_dir.is_dir():
            return ""

        # Sync knowledge index
        self.sync_agent_knowledge(agent.slug, knowledge_dir)

        # Categorize all files in knowledge directory
        all_files = sorted(knowledge_dir.iterdir())
        if not all_files:
            return ""

        docs_list = []
        pdf_list = []
        img_list = []
        link_list = []
        other_list = []

        total_extracted_len = 0
        extracted_docs: list[tuple[str, str, str]] = []  # (name, type, content)

        for f in all_files:
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            size_kb = round(f.stat().st_size / 1024, 1)

            if ext in PDF_EXTENSIONS:
                pdf_list.append(f"- 📄 **{f.name}** (PDF, {size_kb} KB)")
                content = extract_file_content(f)
                extracted_docs.append((f.name, "PDF", content))
                total_extracted_len += len(content)
            elif ext in IMAGE_EXTENSIONS:
                img_list.append(f"- 🖼️ **{f.name}** (Obraz/Grafika, {size_kb} KB)")
                content = extract_file_content(f)
                extracted_docs.append((f.name, "Obraz", content))
                total_extracted_len += len(content)
            elif f.name.startswith("link_") and ext in TEXT_EXTENSIONS:
                link_list.append(f"- 🔗 **{f.name}** (Zewnętrzne Źródło / Link)")
                content = extract_file_content(f)
                extracted_docs.append((f.name, "Link", content))
                total_extracted_len += len(content)
            elif ext in TEXT_EXTENSIONS:
                docs_list.append(f"- 📝 **{f.name}** (Dokument tekstowy, {size_kb} KB)")
                content = extract_file_content(f)
                extracted_docs.append((f.name, "Dokument", content))
                total_extracted_len += len(content)
            else:
                other_list.append(f"- 📁 **{f.name}** ({size_kb} KB)")

        if not extracted_docs:
            return ""

        # Build catalog section
        catalog_lines = ["### 📂 Katalog Zasobów w Bazie Wiedzy (knowledge/):"]
        if docs_list:
            catalog_lines.append("**Dokumenty i procedury:**\n" + "\n".join(docs_list))
        if pdf_list:
            catalog_lines.append("**Materiały PDF:**\n" + "\n".join(pdf_list))
        if img_list:
            catalog_lines.append("**Pliki graficzne i zdjęcia:**\n" + "\n".join(img_list))
        if link_list:
            catalog_lines.append("**Linki zewnętrzne i wideo (YouTube / WWW):**\n" + "\n".join(link_list))
        if other_list:
            catalog_lines.append("**Inne pliki:**\n" + "\n".join(other_list))

        # Ingest knowledge graph edges summary (BEH-067)
        graph_data = self.get_graph(agent.slug)
        if graph_data.get("edges"):
            edge_samples = [
                f"- [[{e['source']}]] ──({e['label'] or 'wikilink'})──> [[{e['target']}]]"
                for e in graph_data["edges"][:12]
            ]
            catalog_lines.append("**🕸️ Sieć powiązań wiedzy (Obsidian Wikilinks):**\n" + "\n".join(edge_samples))

        catalog_str = "\n\n".join(catalog_lines)

        header = (
            "## 📚 Baza Wiedzy Agenta (NAJWYŻSZY PRIORYTET / GŁÓWNE ŹRÓDŁO PRAWDY)\n"
            "Poniższa baza wiedzy to Twoje PIERWSZORZĘDNE i NAJWAŻNIEJSZE źródło informacji. "
            "W pierwszej kolejności bazuj na poniższych dokumentach, plikach i załącznikach. "
            "Mają one bezwzględny priorytet przed Twoją wiedzą ogólną. Zawsze odwołuj się do faktów, "
            "wytycznych i materiałów z bazy wiedzy."
        )

        # Small knowledge base: include all documents and catalog
        if total_extracted_len <= 7000 and not query:
            formatted = []
            for name, dtype, content in extracted_docs:
                formatted.append(f"#### [{dtype}: {name}]\n{content[:7000]}")
            return f"{header}\n\n{catalog_str}\n\n### 📄 Pełna Zawartość Bazy Wiedzy:\n\n" + "\n\n".join(formatted)

        # RAG Search Mode
        search_query = query or (agent.master_prompt + " " + (agent.instructions or ""))
        matches = self.search(agent.slug, query=search_query, top_k=6)

        if not matches:
            formatted = []
            chars_used = 0
            for name, dtype, content in extracted_docs:
                chunk = content[:1500]
                formatted.append(f"#### [{dtype}: {name}]\n{chunk}")
                chars_used += len(chunk)
                if chars_used >= max_total_chars:
                    break
            return f"{header}\n\n{catalog_str}\n\n### 📄 Podgląd Dokumentów:\n\n" + "\n\n".join(formatted)

        rag_parts = [
            header,
            catalog_str,
            "### 🔍 Dopasowane Fragmenty z Bazy Wiedzy (Wyszukiwanie RAG):",
            f"*Dopasowano {len(matches)} najbardziej relewantnych fragmentów do aktualnego zapytania.*",
        ]

        for m in matches:
            rel_pct = int(m["score"] * 100)
            rag_parts.append(
                f"#### [Źródło: {m['doc_name']} | Fragment #{m['chunk_index'] + 1}] (Relewantność: {rel_pct}%)\n{m['content']}"
            )

        # Graph RAG Expansion (BEH-067)
        matched_doc_names = list(dict.fromkeys(m["doc_name"] for m in matches))
        graph_expansions = self.expand_graph(agent.slug, matched_doc_names, max_hops=2)

        graph_sections = []
        if graph_expansions:
            with self.db._get_connection() as conn:
                for linked_doc, meta in list(graph_expansions.items())[:5]:
                    chunk_row = conn.execute(
                        "SELECT content FROM knowledge_chunks WHERE agent_slug = ? AND doc_name = ? ORDER BY chunk_index ASC LIMIT 1",
                        (agent.slug, linked_doc),
                    ).fetchone()
                    if chunk_row:
                        hop_label = "1-hop" if meta["hops"] == 1 else "2-hop"
                        dir_label = "odwołuje się do" if meta["direction"] == "outgoing" else "jest cytowany przez"
                        sample_content = chunk_row["content"][:1000]
                        graph_sections.append(
                            f"##### [Powiązana notatka grafu ({hop_label}): {linked_doc}] ({meta['connected_to']} {dir_label} {linked_doc})\n{sample_content}"
                        )

        if graph_sections:
            rag_parts.append(
                "### 🕸️ Graf Powiązań Wiedzy (Graph RAG - Połączone Notatki Obsidian):\n"
                "*Poniższe notatki zostały automatycznie połączone poprzez sieć wikilinków [[...]] z głównymi dokumentami zapytania (1-hop / 2-hop):*\n"
                + "\n\n".join(graph_sections)
            )

        return "\n\n".join(rag_parts)
