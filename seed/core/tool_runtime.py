"""Builtin tool registry and executor — kernel contracts (seed core)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional

from seed.core.models import Tool

logger = logging.getLogger(__name__)


class ToolExecutionError(Exception):
    """Exception raised for tool execution errors."""

    def __init__(self, tool_name: str, message: str, original_error: Optional[Exception] = None):
        self.tool_name = tool_name
        self.message = message
        self.original_error = original_error
        super().__init__(f"Tool '{tool_name}' execution error: {message}")


class ToolRegistry:
    """Registry for managing available tools."""

    def __init__(self):
        self.tools: Dict[str, Tool] = {}
        self.handlers: Dict[str, Callable[..., Any]] = {}

    def register(self, tool: Tool, handler: Callable[..., Any]) -> None:
        if tool.name in self.tools:
            logger.warning(f"Tool '{tool.name}' already registered, overwriting")

        self.tools[tool.name] = tool
        self.handlers[tool.name] = handler

    def unregister(self, name: str) -> bool:
        if name in self.tools:
            del self.tools[name]
            if name in self.handlers:
                del self.handlers[name]
            return True
        return False

    def get(self, name: str) -> Optional[Tool]:
        return self.tools.get(name)

    def list_all(self) -> List[Tool]:
        return list(self.tools.values())

    def count(self) -> int:
        return len(self.tools)

    def exists(self, name: str) -> bool:
        return name in self.tools

    def get_available_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "returns": tool.returns,
                "category": getattr(tool, "category", "builtin"),
                "version": getattr(tool, "version", "1.0"),
            }
            for tool in self.tools.values()
        ]


class ToolExecutor:
    """Executes tools based on their metadata and arguments."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def execute(self, tool_name: str, **kwargs: Any) -> Any:
        if not self.registry.exists(tool_name):
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found",
            )

        handler = self.registry.handlers.get(tool_name)

        if not handler:
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Handler for tool '{tool_name}' not found",
            )

        logger.info(f"Executing tool: {tool_name} with args: {kwargs}")

        try:
            result = handler(**kwargs)
            if inspect.isawaitable(result):
                try:
                    asyncio.get_running_loop()
                    raise ToolExecutionError(
                        tool_name=tool_name,
                        message="Async tool called from sync context; use execute_async()",
                    )
                except RuntimeError:
                    result = asyncio.run(result)  # type: ignore[arg-type]
            logger.info(f"Tool '{tool_name}' executed successfully")
            return result
        except Exception as e:
            raise ToolExecutionError(
                tool_name=tool_name,
                message=str(e),
                original_error=e,
            )

    async def execute_async(self, tool_name: str, **kwargs: Any) -> Any:
        if not self.registry.exists(tool_name):
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found",
            )
        handler = self.registry.handlers.get(tool_name)
        if not handler:
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Handler for tool '{tool_name}' not found",
            )
        logger.info(f"Executing tool (async): {tool_name} with args: {kwargs}")
        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(**kwargs)
            else:
                result = await asyncio.to_thread(handler, **kwargs)
            logger.info(f"Tool '{tool_name}' executed successfully")
            return result
        except Exception as e:
            raise ToolExecutionError(
                tool_name=tool_name,
                message=str(e),
                original_error=e,
            )

    def execute_with_validation(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if not self.registry.exists(tool_name):
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found",
            )

        tool = self.registry.get(tool_name)

        if tool is None:
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found",
            )

        if tool.parameters:
            self._validate_parameters(tool, args)

        return self.execute(tool_name, **args)

    async def execute_with_validation_async(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if not self.registry.exists(tool_name):
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found",
            )
        tool = self.registry.get(tool_name)
        if tool is None:
            raise ToolExecutionError(
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found",
            )
        if tool.parameters:
            self._validate_parameters(tool, args)
        return await self.execute_async(tool_name, **args)

    def _validate_parameters(self, tool: Tool, args: Dict[str, Any]) -> None:
        required = [
            name for name, params in tool.parameters.items() if "required" in params and params["required"]
        ]

        for param_name in required:
            if param_name not in args:
                raise ToolExecutionError(
                    tool_name=tool.name,
                    message=f"Missing required parameter: {param_name}",
                )

        for param_name in list(args.keys()):
            arg_value = args[param_name]
            if param_name in tool.parameters:
                param_def = tool.parameters[param_name]
                expected_type = param_def.get("type", "any")

                if expected_type == "string":
                    allow_empty = bool(param_def.get("allow_empty"))
                    is_required = bool(param_def.get("required"))
                    if arg_value is None:
                        if allow_empty or not is_required:
                            args[param_name] = ""
                            continue
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be a string (got null)",
                        )
                    if not isinstance(arg_value, str):
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be a string",
                        )
                    if not allow_empty and arg_value.strip() == "" and is_required:
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be a non-empty string",
                        )
                elif expected_type == "integer":
                    is_required_int = bool(param_def.get("required"))
                    if arg_value is None:
                        if not is_required_int:
                            del args[param_name]
                            continue
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be an integer (got null)",
                        )
                    if isinstance(arg_value, bool):
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be an integer (got boolean)",
                        )
                    if isinstance(arg_value, float) and arg_value.is_integer():
                        args[param_name] = int(arg_value)
                        continue
                    if isinstance(arg_value, str):
                        stripped = arg_value.strip()
                        if stripped:
                            try:
                                args[param_name] = int(stripped, 10)
                                continue
                            except ValueError:
                                pass
                    if not isinstance(arg_value, int):
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be an integer",
                        )
                elif expected_type == "boolean":
                    is_required_bool = bool(param_def.get("required"))
                    if arg_value is None:
                        if not is_required_bool:
                            del args[param_name]
                            continue
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be a boolean (got null)",
                        )
                    if isinstance(arg_value, bool):
                        continue
                    if isinstance(arg_value, str):
                        s = arg_value.strip().lower()
                        if s in ("1", "true", "yes", "on"):
                            args[param_name] = True
                            continue
                        if s in ("0", "false", "no", "off", ""):
                            args[param_name] = False
                            continue
                        raise ToolExecutionError(
                            tool_name=tool.name,
                            message=f"Parameter '{param_name}' must be a boolean or yes/no string",
                        )
                    if isinstance(arg_value, int) and arg_value in (0, 1):
                        args[param_name] = bool(arg_value)
                        continue
                    raise ToolExecutionError(
                        tool_name=tool.name,
                        message=f"Parameter '{param_name}' must be a boolean",
                    )
