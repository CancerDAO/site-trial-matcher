from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class McpError(RuntimeError):
    pass


class McpClient:
    def __init__(self, url: str, api_key: str, timeout: float = 150):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpError("WHO_MCP_URL必须是HTTP(S)绝对地址")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and os.environ.get("WHO_MCP_ALLOW_INSECURE_HTTP") != "1":
            raise McpError("公网明文HTTP必须显式设置WHO_MCP_ALLOW_INSECURE_HTTP=1")
        if not api_key:
            raise McpError("缺少WHO_MCP_API_KEY")
        self.url = url; self.api_key = api_key; self.timeout = timeout
        self.protocol_version = "2024-11-05"; self.session_id: str | None = None; self.request_id = 0
        self.lock = threading.Lock()

    @staticmethod
    def _sse(body: str, expected_id: int) -> dict[str, Any]:
        for event in body.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data:
                try: message = json.loads(data)
                except json.JSONDecodeError: continue
                if message.get("id") == expected_id: return message
        raise McpError("SSE响应缺少预期JSON-RPC消息")

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self.lock:
            self.request_id += 1; request_id = self.request_id; session_id = self.session_id
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": self.protocol_version}
        if session_id: headers["Mcp-Session-Id"] = session_id
        data = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}, separators=(",", ":")).encode()
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                with urlopen(Request(self.url, data=data, headers=headers, method="POST"), timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", errors="replace"); content_type = response.headers.get("Content-Type", "")
                    returned_session = response.headers.get("Mcp-Session-Id")
                    if returned_session:
                        with self.lock: self.session_id = returned_session
                message = self._sse(body, request_id) if "text/event-stream" in content_type else json.loads(body)
                if "error" in message: raise McpError(f"MCP {method}错误：{message['error']}")
                return message.get("result")
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
                last_error = error
                if isinstance(error, HTTPError) and error.code not in {502, 503, 504}: break
                time.sleep(min(attempt + 1, 5))
        raise McpError(f"MCP {method}请求失败：{last_error}")

    def notify(self, method: str) -> None:
        # FastMCP accepts initialized notification as a normal POST; ignore its empty result.
        try: self.request(method)
        except McpError: pass

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError("; ".join(item.get("text", "") for item in result.get("content") or []))
        if result.get("structuredContent") is not None: return result["structuredContent"]
        text = "\n".join(item.get("text", "") for item in result.get("content") or [] if item.get("type") == "text")
        return json.loads(text)

    def call_tool_retry(self, name: str, arguments: dict[str, Any], attempts: int = 5) -> Any:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try: return self.call_tool(name, arguments)
            except McpError as error:
                last_error = error
                if attempt + 1 < attempts: time.sleep(min(2 ** attempt, 10))
        raise McpError(f"MCP工具{name}连续{attempts}次失败：{last_error}")


def _date_key(value: Any) -> datetime:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%B %d, %Y", "%Y/%m/%d"):
        try: return datetime.strptime(text, pattern)
        except ValueError: pass
    return datetime.min


def fetch_recent_china_trials(output_path: str | Path, *, limit: int = 300, workers: int = 16,
                              scan_limit: int = 5000, require_complete: bool = True) -> dict[str, Any]:
    if limit < 1 or scan_limit < limit:
        raise ValueError("limit必须大于0，且scan_limit不得小于limit")
    url = os.environ.get("WHO_MCP_URL", ""); api_key = os.environ.get("WHO_MCP_API_KEY", "")
    client = McpClient(url, api_key, float(os.environ.get("MCP_REQUEST_TIMEOUT_SECONDS", "150")))
    started = time.perf_counter()
    initialized = client.request("initialize", {"protocolVersion": client.protocol_version, "capabilities": {},
        "clientInfo": {"name": "china-trial-partner-demo", "version": "0.2"}})
    client.protocol_version = initialized.get("protocolVersion") or client.protocol_version
    client.notify("notifications/initialized")
    tools = client.request("tools/list"); tool_names = {item["name"] for item in tools.get("tools") or []}
    required = {"database_metadata", "search_trials_multidimensional", "get_trial"}
    if missing := sorted(required - tool_names): raise McpError(f"MCP缺少工具：{missing}")
    metadata = client.call_tool_retry("database_metadata", {})
    search_rows: list[dict[str, Any]] = []; pagination_warning: str | None = None; reached_end = False; pages_scanned = 0
    # Pages are independent. Read them in small parallel windows because the
    # remote service is high-latency and its textual date ORDER BY is not a
    # reliable global chronological order across heterogeneous registries.
    page_workers = max(1, min(workers, 8))
    with ThreadPoolExecutor(max_workers=page_workers) as pool:
        for window_start in range(0, scan_limit, 1000):
            offsets = list(range(window_start, min(window_start + 1000, scan_limit), 100))
            futures = {}
            for offset in offsets:
                arguments = {"country": "China", "recruitment_statuses": ["recruiting"], "interventional_only": True, "limit": 100, "offset": offset}
                futures[pool.submit(client.call_tool_retry, "search_trials_multidimensional", arguments)] = offset
            pages: dict[int, list[dict[str, Any]]] = {}
            for future in as_completed(futures):
                offset = futures[future]
                try: pages[offset] = future.result().get("results") or []
                except McpError as error:
                    pagination_warning = f"offset={offset}: {error}"
            for offset in offsets:
                if pagination_warning and offset not in pages:
                    break
                rows = pages.get(offset, []); pages_scanned += 1; search_rows.extend(rows)
                if len(rows) < 100:
                    reached_end = True
                    break
            if reached_end or pagination_warning:
                break
    # Registry IDs are the only safe identity at this stage. Search pages may
    # overlap while the upstream database is refreshed.
    unique_rows: dict[str, dict[str, Any]] = {}
    for row in search_rows:
        registry_id = str(row.get("primary_registry_id") or "").strip()
        if registry_id and registry_id not in unique_rows:
            unique_rows[registry_id] = row
    search_rows = list(unique_rows.values()); scanned_count = len(search_rows)
    search_rows.sort(key=lambda row: (_date_key(row.get("registration_date")), _date_key(row.get("last_update_date"))), reverse=True)
    search_rows = search_rows[:limit]
    selection_complete = reached_end and pagination_warning is None
    output = Path(output_path); output.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if output.is_file():
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
            existing = {str(item.get("primary_registry_id")): item for item in previous.get("details") or [] if item.get("found")}
        except (json.JSONDecodeError, OSError): pass
    selected_ids = [str(row.get("primary_registry_id")) for row in search_rows]
    completed = {key: existing[key] for key in selected_ids if key in existing}
    pending = [row for row in search_rows if str(row.get("primary_registry_id")) not in completed]
    failures: dict[str, str] = {}
    def save() -> None:
        payload = {"schema_version": "who-mcp-china-demo-v1", "metadata": metadata, "server_info": initialized.get("serverInfo"),
            "protocol_version": client.protocol_version, "selection": {"country": "China", "recruitment_statuses": ["recruiting"],
            "interventional_only": True, "limit": limit, "scan_limit": scan_limit, "scanned": scanned_count,
            "pages_scanned": pages_scanned, "reached_end": reached_end,
            "order": "client_parsed_registration_date_desc_then_last_update", "pagination_warning": pagination_warning,
            "selection_complete": selection_complete}, "search_results": search_rows,
            "details": [completed[key] for key in selected_ids if key in completed], "failures": failures}
        temporary = output.with_suffix(output.suffix+".tmp"); temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); temporary.replace(output)
    if require_complete and not selection_complete:
        save()
        raise McpError(f"无法证明最新试验选择完整：{pagination_warning or '扫描达到scan_limit但未到结果末尾'}")
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        futures = {pool.submit(client.call_tool_retry, "get_trial", {"registry_id": row["primary_registry_id"]}): row["primary_registry_id"] for row in pending}
        for index, future in enumerate(as_completed(futures), start=1):
            registry_id = futures[future]
            try: completed[registry_id] = future.result()
            except Exception as error: failures[registry_id] = str(error)
            if index % 20 == 0: save()
    save()
    if require_complete and failures:
        raise McpError(f"最新试验详情下载不完整：{len(failures)} 条失败；可用同一命令断点续传")
    return {"output": str(output.resolve()), "database_as_of": metadata.get("database_as_of"), "scanned": scanned_count, "search_results": len(search_rows),
        "details": len(completed), "failed": len(failures), "selection_complete": selection_complete,
        "pagination_warning": pagination_warning, "elapsed_s": round(time.perf_counter()-started, 3)}


def hydrate_snapshot(snapshot_path: str | Path, *, workers: int = 16) -> dict[str, Any]:
    """Download missing details for an already audited selection without rescanning pages."""
    path = Path(snapshot_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected_ids = [str(row.get("primary_registry_id") or "").strip() for row in payload.get("search_results") or []]
    selected_ids = [item for item in selected_ids if item]
    completed = {str(item.get("primary_registry_id")): item for item in payload.get("details") or [] if item.get("found")}
    pending = [registry_id for registry_id in selected_ids if registry_id not in completed]
    client = McpClient(os.environ.get("WHO_MCP_URL", ""), os.environ.get("WHO_MCP_API_KEY", ""),
                       float(os.environ.get("MCP_REQUEST_TIMEOUT_SECONDS", "150")))
    initialized = client.request("initialize", {"protocolVersion": client.protocol_version, "capabilities": {},
        "clientInfo": {"name": "china-trial-partner-demo", "version": "0.2"}})
    client.protocol_version = initialized.get("protocolVersion") or client.protocol_version
    client.notify("notifications/initialized")
    failures: dict[str, str] = {}
    started = time.perf_counter()
    def save() -> None:
        payload["details"] = [completed[key] for key in selected_ids if key in completed]
        payload["failures"] = failures
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        futures = {pool.submit(client.call_tool_retry, "get_trial", {"registry_id": registry_id}): registry_id for registry_id in pending}
        for index, future in enumerate(as_completed(futures), start=1):
            registry_id = futures[future]
            try: completed[registry_id] = future.result()
            except Exception as error: failures[registry_id] = str(error)
            if index % 20 == 0: save()
    save()
    if failures:
        raise McpError(f"试验详情下载不完整：{len(failures)} 条失败；可重试同一命令")
    return {"snapshot": str(path.resolve()), "selected": len(selected_ids), "details": len(completed),
            "failed": 0, "elapsed_s": round(time.perf_counter() - started, 3)}
