"""Register MCP server tools into a ``ToolRegistry`` for direct LLM invocation."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict

from seed.core.env_access import MCP_REGISTER_TOOLS, env_truthy
from seed.core.models import Tool
from seed.core.tool_runtime import ToolRegistry
from seed.integrations.mcp_client import MCPError, MCPToolInfo, get_mcp_manager, mcp_globally_enabled
from seed.integrations.mcp_config import list_server_configs

logger = logging.getLogger(__name__)

_MCP_PREFIX = "mcp__"
_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_]+")


def mcp_register_enabled() -> bool:
    return mcp_globally_enabled() and env_truthy(*MCP_REGISTER_TOOLS, default="1")


def mcp_dynamic_deny_prefixes() -> tuple[str, ...]:
    """Innate tool policy: dynamic MCP tools use this prefix."""
    return (_MCP_PREFIX,)


def sanitize_mcp_segment(value: str) -> str:
    s = _SEGMENT_RE.sub("_", (value or "").strip())
    s = s.strip("_") or "x"
    return s[:48]


def mcp_registry_tool_name(server_id: str, tool_name: str) -> str:
    return f"{_MCP_PREFIX}{sanitize_mcp_segment(server_id)}__{sanitize_mcp_segment(tool_name)}"


def parse_mcp_registry_tool_name(registry_name: str) -> tuple[str, str] | None:
    """Return ``(server_id, tool_name)`` if ``registry_name`` is a dynamic MCP tool."""
    if not registry_name.startswith(_MCP_PREFIX):
        return None
    rest = registry_name[len(_MCP_PREFIX) :]
    if "__" not in rest:
        return None
    sid_seg, t_seg = rest.split("__", 1)
    return sid_seg, t_seg


def input_schema_to_parameters(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MCP ``inputSchema`` JSON Schema object to Seed ``Tool.parameters``."""
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return {}
    required = set(schema.get("required") or [])
    out: Dict[str, Any] = {}
    for pname, pdef in props.items():
        if not isinstance(pdef, dict):
            continue
        ptype = pdef.get("type") or "string"
        if isinstance(ptype, list):
            ptype = ptype[0] if ptype else "string"
        out[str(pname)] = {
            "type": str(ptype),
            "required": pname in required,
            "description": str(pdef.get("description") or ""),
        }
    return out


def _resolve_server_id(segment: str, configs: list) -> str | None:
    """Match sanitized segment back to configured server id."""
    for cfg in configs:
        if sanitize_mcp_segment(cfg.server_id) == segment:
            return cfg.server_id
    return None


def make_mcp_tool_handler(server_id: str, tool_name: str) -> Callable[..., str]:
    def handler(**kwargs: Any) -> str:
        from seed.core.env_access import MCP_CALL_TIMEOUT, pick_default

        args = {k: v for k, v in kwargs.items() if v is not None}
        timeout = float(pick_default("120", *MCP_CALL_TIMEOUT) or "120")
        try:
            return get_mcp_manager().get_session(server_id).call_tool(
                tool_name, args, timeout=timeout
            )
        except MCPError as e:
            return str(e)

    return handler


def _tool_from_mcp(server_id: str, info: MCPToolInfo) -> Tool:
    reg_name = mcp_registry_tool_name(server_id, info.name)
    desc = (info.description or "").strip()
    if not desc:
        desc = f"MCP tool {info.name!r} on server {server_id!r}"
    else:
        desc = f"[MCP:{server_id}] {desc}"
    return Tool(
        name=reg_name,
        description=desc,
        parameters=input_schema_to_parameters(info.input_schema),
        returns="string",
        category="mcp",
        version="1.0",
    )


def register_mcp_tools_into_registry(registry: ToolRegistry) -> int:
    """
    Discover tools from enabled MCP servers and register as ``mcp__<server>__<tool>``.

    Returns the number of tools registered. Failures per server are logged and skipped.
    """
    if not mcp_register_enabled():
        return 0

    configs = [c for c in list_server_configs() if c.enabled and c.transport == "stdio"]
    if not configs:
        return 0

    registered = 0
    manager = get_mcp_manager()
    for cfg in configs:
        try:
            tools = manager.get_session(cfg.server_id).list_tools()
        except MCPError as e:
            logger.warning("MCP register skip server %s: %s", cfg.server_id, e)
            continue
        except Exception:
            logger.exception("MCP register failed for server %s", cfg.server_id)
            continue
        for info in tools:
            try:
                tool = _tool_from_mcp(cfg.server_id, info)
                registry.register(tool, make_mcp_tool_handler(cfg.server_id, info.name))
                registered += 1
            except Exception:
                logger.exception(
                    "MCP register tool %s on %s", info.name, cfg.server_id
                )
    if registered:
        logger.info("Registered %d dynamic MCP tools", registered)
    return registered
