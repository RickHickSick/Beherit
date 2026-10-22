# Beherit 🧿

> **Autonomous AI Agent Management Platform powered by Agent SDKs with Per-Agent Subscription Isolation.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Architecture: Markdown-First](https://img.shields.io/badge/architecture-markdown--first-success.svg)]()
[![Zero API Token Costs](https://img.shields.io/badge/cost-zero_api_tokens-brightgreen.svg)]()

**Beherit** is a lightweight, open-source platform for defining, executing, and monitoring autonomous AI agents. Unlike traditional agent frameworks requiring metered API keys, Beherit runs natively on top of **Agent SDKs** (Google Antigravity SDK, Claude Code, OpenAI Codex) tied directly to existing subscriptions.

---

## 🌟 Key Features

* 🔑 **Per-Agent Subscription Isolation (Multi-Tenant Auth):**
  Create unlimited agents, each independently authenticated against its own subscription profile (`.auth/`). Agent A can run on personal Claude Pro, Agent B on company Antigravity SDK, with zero crosstalk.
* 📝 **Markdown-First Architecture:**
  Every agent is defined as a plain directory with human-readable Markdown files (`agent.md`, `instructions.md`, `skills/`). Version-controlled, directly editable in Obsidian or VS Code, and synchronized with the Web UI.
* ⚡ **Zero Additional Token Costs:**
  Leverages desktop and cloud Agent SDK sessions instead of per-token API billing.
* 📱 **Mobile PWA First (Safari iOS & Desktop):**
  Designed for seamless interaction on mobile devices (iOS safe-area, virtual keyboard handling) with real-time SSE streaming, reasoning thoughts visibility, and tool-call inspection.
* ⏰ **Dual Operational Modes:**
  * **Interactive Live Chat:** Stream responses with thought process and interactive tool calls.
  * **Autonomous Background Scheduler:** Periodic cron execution for automated audits, database maintenance, or data polling.
* 🛡️ **Zero-Leak Security by Design:**
  100% portable and environment-driven. Zero hardcoded server addresses, IPs, credentials, or private paths.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    subgraph UI ["Web UI & Safari iOS PWA"]
        A1["Agent Directory & Status"]
        A2["Creator with Per-Agent SDK Auth Handshake"]
        A3["Live Chat & SSE Streaming"]
        A4["Background Scheduler History"]
    end

    subgraph Core ["Beherit Core Server (FastAPI)"]
        B1["REST API & SSE Hub"]
        B2["Markdown Parser & Serializer (Pydantic v2)"]
        B3["Engine Orchestrator"]
        B4["Per-Agent Auth Session Manager"]
        B5["Task Scheduler (APScheduler)"]
    end

    subgraph IsolatedStorage ["Agent Storage (/data/agents/*)"]
        subgraph Agent1 ["Agent 1"]
            A_MD["agent.md"]
            A_AUTH[".auth/ (Subscription A Session)"]
        end
        subgraph Agent2 ["Agent 2"]
            B_MD["agent.md"]
            B_AUTH[".auth/ (Subscription B Session)"]
        end
    end

    subgraph SDKEngines ["Supported Agent SDKs"]
        E1["Google Antigravity SDK Runner"]
        E2["Claude Code SDK Runner"]
        E3["OpenAI Codex Runner"]
    end

    UI --> Core
    Core --> IsolatedStorage
    Core --> SDKEngines
    SDKEngines -.->|Injects isolated session| IsolatedStorage
```

---

## 📂 Agent Directory Structure

Every agent lives in its own directory inside `data/agents/`:

```text
data/agents/<agent-slug>/
├── agent.md            # Frontmatter YAML metadata + Master Prompt
├── instructions.md     # Detailed operating procedures & guidelines
├── .auth/              # [GITIGNORED] Isolated SDK session token / credentials
│   └── session.json
├── knowledge/          # Reference documents, schemas, examples
└── skills/             # Custom python/bash tools for this agent
```

### Example `agent.md`:

```markdown
---
name: "Financial Data Analyst"
slug: "financial-analyst"
description: "Audits market data tables and detects price anomalies"
engine: "antigravity"          # Options: antigravity | claude-code | codex
model: "gemini-3.8-flash"
created_at: 2026-10-22
auth:
  provider: "antigravity"
  status: "authenticated"
  profile_id: "financial-analyst-profile"
capabilities:
  terminal: true
  file_read: true
  file_write: false
  database_read: true
schedule:
  enabled: true
  cron: "0 7 * * *"             # Daily at 07:00
---

# Master Prompt
You are an autonomous financial data analyst.
Inspect daily tables for anomalies and timestamp gaps.
Format output as a concise structured report.
```

---

## 🚀 Quickstart

### Prerequisites
* Python 3.11+
* Git
* (Optional) Docker & Docker Compose

### 1. Clone the repository
```bash
git clone https://github.com/RickHickSick/Beherit.git
cd Beherit
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your preferred port and secret key
```

### 3. Run with Python
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn beherit.main:app --host 0.0.0.0 --port 8650 --reload
```

### 4. Or Run with Docker Compose
```bash
docker compose up -d
```

Open your browser at `http://localhost:8650`.

---

## 🛡️ Privacy & Zero-Leak Security

* Beherit does not transmit telemetry or user prompts to external tracking services.
* All credentials, SDK session files, and prompt data reside locally in your data directory.
* The repository contains automated pre-commit and CI guards preventing secrets or internal server addresses from being committed.

---

## 📄 License

This project is open-source under the [MIT License](LICENSE).
