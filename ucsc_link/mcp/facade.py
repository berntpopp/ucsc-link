"""MCP facade for ucsc-link: assemble the FastMCP instance with all tools.

:func:`create_ucsc_mcp` is the single construction point for the server's MCP
plane. It builds the ``FastMCP`` instance (error details masked), optionally
injects a service via ``service_factory`` (tests pass a respx-backed service; the
running server registers a real ``UcscService`` in its startup), registers
every tool group + the ``ucsc://`` resource family, and installs the
arg-validation middleware.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from fastmcp import FastMCP
from fastmcp.server.transforms import GetToolNext, Transform

from ucsc_link import __version__
from ucsc_link.mcp.capabilities import register_capability_resources
from ucsc_link.mcp.middleware import ArgValidationMiddleware
from ucsc_link.mcp.notfound_guard import (
    NotFoundGuard,
    install_notfound_log_filter,
    install_protocol_error_handler,
)
from ucsc_link.mcp.resources import UCSC_SERVER_INSTRUCTIONS
from ucsc_link.mcp.service_adapters import set_ucsc_service
from ucsc_link.mcp.tools import register_all_tools

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastmcp.tools.base import Tool
    from fastmcp.utilities.versions import VersionSpec

    from ucsc_link.service import UcscService


class _CompactToolSchemas(Transform):
    """Drop redundant JSON-Schema defaults while preserving runtime defaults."""

    @staticmethod
    def _compact(tool: Tool) -> Tool:
        parameters = deepcopy(tool.parameters)
        for prop in parameters.get("properties", {}).values():
            if isinstance(prop, dict):
                prop.pop("default", None)
                # An enum already rejects every value of the wrong JSON type.
                # Omitting its redundant `type` preserves the exact contract.
                if "enum" in prop:
                    prop.pop("type", None)
        output_schema = deepcopy(tool.output_schema)
        branches = output_schema.get("oneOf") if isinstance(output_schema, dict) else None
        if (
            isinstance(output_schema, dict)
            and isinstance(branches, list)
            and branches
            and all(
                isinstance(branch, dict) and branch.get("additionalProperties") is False
                for branch in branches
            )
        ):
            # Each oneOf branch is already closed. The root pair is required on
            # the source constant for explicit contract inspection, but is
            # validation-redundant in the advertised schema.
            output_schema.pop("patternProperties", None)
            output_schema.pop("additionalProperties", None)
        return tool.model_copy(update={"parameters": parameters, "output_schema": output_schema})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._compact(tool) for tool in tools]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return self._compact(tool) if tool is not None else None


def create_ucsc_mcp(
    service_factory: Callable[[], UcscService] | None = None,
) -> FastMCP:
    """Build a FastMCP instance with all ucsc-link tools, resources, middleware.

    Args:
        service_factory: Optional zero-arg factory; if given, its result is
            registered as the process-wide ``UcscService`` via
            :func:`set_ucsc_service`. The running server omits this and
            registers the real service during startup instead.

    Returns:
        A configured :class:`FastMCP` instance ready to serve.
    """
    mcp: FastMCP = FastMCP(
        name="ucsc-link",
        version=__version__,
        instructions=UCSC_SERVER_INSTRUCTIONS,
        mask_error_details=True,
        # Tool-Surface Budget Standard v1: do not inline $defs/$ref at every use
        # site (the constructor default is True). Free and safe -- no input schema
        # contains a $ref -- and it trims the advertised surface.
        dereference_schemas=False,
    )

    if service_factory is not None:
        set_ucsc_service(service_factory())

    # Guard the FastMCP-core not-found reflection surface: core echoes the
    # caller's OWN requested tool name / resource URI / prompt name (with any
    # control/zero-width/bidi/NUL code points) to the caller and to logs BEFORE
    # backend middleware runs. NotFoundGuard preflights the tool NAME (unknown ->
    # fixed name-free envelope) and fixes the on_read_resource boundary; add it
    # FIRST so it is the OUTERMOST middleware. See notfound_guard.py.
    mcp.add_middleware(NotFoundGuard())

    register_all_tools(mcp)
    register_capability_resources(mcp)

    # Function defaults remain authoritative at invocation time. Omitting their
    # duplicate JSON-Schema `default` keywords keeps the canonical router surface
    # under B1/B2 without removing descriptions, examples, enums, or constraints.
    mcp.add_transform(_CompactToolSchemas())

    mcp.add_middleware(ArgValidationMiddleware())

    # Layer 3: install the protocol-handler backstop AFTER every tool/resource/
    # prompt is registered, so it is the outermost wrapper on the raw
    # CallTool/ReadResource/GetPrompt handlers. It catches the unknown-tool
    # *return* path and any resource/prompt dispatch error that would echo the
    # requested name/URI (the only layer covering the unknown-prompt surface).
    install_protocol_error_handler(mcp)
    # Layer 5: scrub FastMCP-core / MCP-SDK validation logs that would echo the
    # caller-supplied name/URI (idempotent; process-global).
    install_notfound_log_filter()

    return mcp
