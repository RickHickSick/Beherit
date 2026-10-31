"""Plane Project Management API Client (BEH-065)."""

from typing import Any
import httpx

from beherit.config import settings


class PlaneClient:
    """Async client for Plane REST API."""

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        workspace_slug: str | None = None,
        project_id: str | None = None,
    ):
        self.api_url = (api_url or settings.PLANE_API_URL).rstrip("/")
        self.api_key = api_key or settings.PLANE_API_KEY
        self.workspace_slug = workspace_slug or settings.PLANE_WORKSPACE_SLUG
        self.project_id = project_id or settings.PLANE_PROJECT_ID

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    async def get_states_info(self) -> tuple[dict[str, str], dict[str, str]]:
        """Fetch project states and return (name_to_uuid, uuid_to_name) mappings."""
        url = f"{self.api_url}/workspaces/{self.workspace_slug}/projects/{self.project_id}/states/"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                return {}, {}
            data = resp.json()
            results = data.get("results", [])
            name_to_id: dict[str, str] = {}
            id_to_name: dict[str, str] = {}
            for s in results:
                s_id = s.get("id")
                s_name = s.get("name", "").lower()
                s_slug = s.get("slug", "").lower()
                s_group = s.get("group", "").lower()
                if s_id:
                    name_to_id[s_name] = s_id
                    name_to_id[s_slug] = s_id
                    name_to_id[s_group] = s_id
                    name_to_id[s_name.replace(" ", "_")] = s_id
                    id_to_name[s_id] = s.get("name", "")
            return name_to_id, id_to_name

    async def get_states_map(self) -> dict[str, str]:
        """Fetch project states and return a normalized name -> uuid mapping."""
        name_to_id, _ = await self.get_states_info()
        return name_to_id

    async def list_issues(
        self,
        state: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """List issues from Plane project with optional state filter."""
        url = f"{self.api_url}/workspaces/{self.workspace_slug}/projects/{self.project_id}/issues/?per_page={limit}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                raise RuntimeError(f"Plane API Error ({resp.status_code}): {resp.text}")

            data = resp.json()
            results = data.get("results", [])

            # Filter by state if requested
            name_to_id, id_to_name = await self.get_states_info()
            target_state_uuid = name_to_id.get(state.lower().replace("-", "_").replace(" ", "_")) if state else None

            filtered = []
            for it in results:
                it_state_id = it.get("state")
                state_name = id_to_name.get(it_state_id, "unknown")
                if state:
                    if target_state_uuid and it_state_id != target_state_uuid:
                        continue
                    elif not target_state_uuid and state.lower() not in (state_name.lower(), it.get("state_detail", {}).get("name", "").lower()):
                        continue

                filtered.append({
                    "id": it.get("id"),
                    "sequence_id": it.get("sequence_id"),
                    "identifier": f"BEH-{it.get('sequence_id')}",
                    "name": it.get("name"),
                    "priority": it.get("priority"),
                    "state": state_name,
                    "state_id": it_state_id,
                    "created_at": it.get("created_at"),
                })
            return filtered

    async def create_issue(
        self,
        name: str,
        description: str | None = None,
        priority: str = "medium",
        state: str | None = None,
        module_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new issue in Plane project."""
        url = f"{self.api_url}/workspaces/{self.workspace_slug}/projects/{self.project_id}/issues/"

        payload: dict[str, Any] = {
            "name": name.strip(),
            "priority": priority.lower() if priority in ("urgent", "high", "medium", "low", "none") else "medium",
        }
        if description:
            payload["description_html"] = f"<p>{description.strip()}</p>"

        if state:
            states_map = await self.get_states_map()
            normalized = state.lower().replace("-", "_").replace(" ", "_")
            if normalized in states_map:
                payload["state"] = states_map[normalized]

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            if resp.status_code not in (200, 201):
                return {
                    "success": False,
                    "error": f"Failed to create Plane issue ({resp.status_code}): {resp.text}",
                }

            created = resp.json()
            issue_id = created.get("id")

            # Link to module if specified
            if module_id and issue_id:
                try:
                    mod_url = f"{self.api_url}/workspaces/{self.workspace_slug}/projects/{self.project_id}/modules/{module_id}/module-issues/"
                    await client.post(mod_url, headers=self._get_headers(), json={"issues": [issue_id]})
                except Exception:
                    pass

            return {
                "success": True,
                "issue_id": issue_id,
                "sequence_id": created.get("sequence_id"),
                "identifier": f"BEH-{created.get('sequence_id')}",
                "name": created.get("name"),
                "priority": created.get("priority"),
                "state_id": created.get("state"),
            }

    async def update_issue_state(
        self,
        issue_id_or_seq: str | int,
        state: str,
    ) -> dict[str, Any]:
        """Update an issue's state (e.g. 'todo', 'in_progress', 'done', 'cancelled')."""
        target_uuid = str(issue_id_or_seq)
        is_seq = False
        try:
            seq_num = int(issue_id_or_seq)
            is_seq = True
        except ValueError:
            pass

        # If sequence_id passed, find UUID
        if is_seq:
            issues = await self.list_issues(limit=100)
            found = next((i for i in issues if i["sequence_id"] == seq_num), None)
            if not found:
                return {
                    "success": False,
                    "error": f"Issue with sequence ID BEH-{seq_num} not found in project.",
                }
            target_uuid = found["id"]

        states_map = await self.get_states_map()
        normalized = state.lower().replace("-", "_").replace(" ", "_")
        if normalized not in states_map:
            return {
                "success": False,
                "error": f"Unknown state '{state}'. Available: {list(states_map.keys())}",
            }

        state_uuid = states_map[normalized]
        url = f"{self.api_url}/workspaces/{self.workspace_slug}/projects/{self.project_id}/issues/{target_uuid}/"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(url, headers=self._get_headers(), json={"state": state_uuid})
            if resp.status_code not in (200, 204):
                return {
                    "success": False,
                    "error": f"Failed to update issue state ({resp.status_code}): {resp.text}",
                }

            return {
                "success": True,
                "issue_id": target_uuid,
                "new_state": state,
                "new_state_id": state_uuid,
            }
