"""MCP 客户端：配置加载、连接管理与工具适配。"""

from endless_code.mcp.catalog import CatalogEntry, McpCatalog
from endless_code.mcp.config import Config, ServerConfig, load_config
from endless_code.mcp.manager import Manager, new_manager
from endless_code.mcp.tool import McpSearchTool, McpTool

__all__ = [
    "CatalogEntry",
    "Config",
    "Manager",
    "McpCatalog",
    "McpSearchTool",
    "McpTool",
    "ServerConfig",
    "load_config",
    "new_manager",
]
