"""MCP（Model Context Protocol）接入层（B522）。"""

from .client import MCPClient, MCPError, MCPTool

__all__ = ["MCPClient", "MCPError", "MCPTool"]
