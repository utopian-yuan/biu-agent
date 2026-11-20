"""Discover named, read-only tools from a configured HTTP MCP service."""

import asyncio
import re

from agentscope.mcp import HttpMCPConfig, MCPClient

from ...identity import Problem


async def discover(row, token):
    client = MCPClient(
        name="ext_" + row["id"].replace("-", "")[:20],
        is_stateful=False,
        mcp_config=HttpMCPConfig(
            url=row["url"], headers={"Authorization": "Bearer " + token} if token else None, timeout=4
        ),
        execution_timeout=5,
    )
    try:
        async with asyncio.timeout(6):
            descriptors = await client.list_raw_tools()
    except Exception:
        raise Problem(503, "mcp_offline", "MCP 服务连接失败，请检查地址、凭据和协议。", True) from None
    safe = [
        t.name
        for t in descriptors
        if t.annotations
        and t.annotations.readOnlyHint is True
        and not t.annotations.destructiveHint
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", t.name)
    ]
    return client, safe
