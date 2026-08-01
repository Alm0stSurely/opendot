"""MCP client support — connect opendot to external MCP servers' tools."""

from opendot.mcp.manager import (
    AuthorizeResult,
    MCPManager,
    add_mcp_server,
    authorize_oauth_server,
    load_mcp_config,
    remove_mcp_server,
    save_mcp_config,
)

__all__ = [
    "MCPManager",
    "AuthorizeResult",
    "load_mcp_config",
    "save_mcp_config",
    "add_mcp_server",
    "authorize_oauth_server",
    "remove_mcp_server",
]
