"""Command Line Interface (CLI) for Beherit AI Agent Platform."""

import argparse
import asyncio
import sys
from beherit import __version__
from beherit.config import settings
from beherit.core.repository import AgentRepository
from beherit.core.scheduler import AgentScheduler


def cmd_serve(args: argparse.Namespace) -> None:
    """Start Beherit web server using Uvicorn."""
    import uvicorn

    host = args.host or settings.BEHERIT_HOST
    port = args.port or settings.BEHERIT_PORT
    reload = args.reload

    print(f"🧿 Starting Beherit v{__version__} server on http://{host}:{port}")
    print(f"📂 Agents storage directory: {settings.agents_dir}")
    uvicorn.run("beherit.main:app", host=host, port=port, reload=reload)


def cmd_list(args: argparse.Namespace) -> None:
    """List all configured agents and their status."""
    repo = AgentRepository()
    agents = repo.list_agents()

    print(f"\n🧿 Beherit Agents Catalog ({len(agents)} registered)\n" + "=" * 65)
    if not agents:
        print("No agents found. Create one using the Web UI or add a folder to ./data/agents/")
        return

    for agent in agents:
        auth_icon = "🟢" if agent.auth.status == "authenticated" else "🟡"
        valid_icon = "✅" if agent.is_valid else "⚠️ [INVALID]"
        print(f"• {agent.name} [{agent.slug}] {valid_icon}")
        print(f"  Engine: {agent.engine} ({agent.model}) | Auth: {auth_icon} {agent.auth.status}")
        if agent.description:
            print(f"  Description: {agent.description}")
        if agent.schedule.enabled:
            print(f"  Schedule: Cron '{agent.schedule.cron}'")
        print()


def cmd_run(args: argparse.Namespace) -> None:
    """Trigger an on-demand autonomous run for a given agent."""
    repo = AgentRepository()
    agent = repo.load_agent(args.slug)
    if not agent:
        print(f"❌ Error: Agent with slug '{args.slug}' not found.")
        sys.exit(1)

    print(f"⚡ Executing autonomous turn for {agent.name} [{agent.slug}]...")

    async def _run():
        scheduler = AgentScheduler(repository=repo)
        result = await scheduler.execute_agent_job(agent.slug)
        status_icon = "✅" if result["status"] == "success" else "❌"
        print(f"\n{status_icon} Execution completed in {result['duration']:.2f}s")
        print(f"Status: {result['status']}")
        if result.get("reporting"):
            print("Reporting dispatches:")
            for k, v in result["reporting"].items():
                print(f"  - {k}: {v}")
        print("\nSummary Output:\n" + "-" * 40)
        print(result.get("summary", "No output generated."))

    asyncio.run(_run())


def cmd_health(args: argparse.Namespace) -> None:
    """Display runtime health information and environment paths."""
    print(f"🧿 Beherit Runtime v{__version__}")
    print(f"Environment: {settings.BEHERIT_ENV}")
    print(f"Agents Directory: {settings.agents_dir}")
    print(f"Database Path: {settings.BEHERIT_DB_PATH.resolve()}")
    print(f"Secret Key: {'Configured' if settings.BEHERIT_SECRET_KEY else 'Missing'}")
    print(f"Telegram Alerts: {'Enabled' if settings.TELEGRAM_BOT_TOKEN else 'Disabled'}")


def main() -> None:
    """CLI Entrypoint parser."""
    parser = argparse.ArgumentParser(
        prog="beherit",
        description="Autonomous AI Agent Management Platform powered by Agent SDKs.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"Beherit {__version__}")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start the FastAPI web server and scheduler")
    serve_parser.add_argument("--host", type=str, default=None, help="Host to bind (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=None, help="Port to listen on (default: 8650)")
    serve_parser.add_argument("--reload", action="store_true", help="Enable live code reloading")
    serve_parser.set_defaults(func=cmd_serve)

    # list command
    list_parser = subparsers.add_parser("list", help="List all registered agents and auth status")
    list_parser.set_defaults(func=cmd_list)

    # run command
    run_parser = subparsers.add_parser("run", help="Run an autonomous turn for an agent")
    run_parser.add_argument("slug", type=str, help="Agent slug (directory name)")
    run_parser.set_defaults(func=cmd_run)

    # health command
    health_parser = subparsers.add_parser("health", help="Check runtime environment and configuration")
    health_parser.set_defaults(func=cmd_health)

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
