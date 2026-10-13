"""MCP 2.3 stdio server over the same typed read-only local tool registry."""
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from functools import wraps
from ..tools import IntelligenceStore, MatchLensTools, ToolSession, TOOL_METHODS, ToolError

def _mcp_callable(session: ToolSession, name: str):
    local = session.callable(name)
    @wraps(local)
    def invoke(**kwargs):
        try:
            return local(**kwargs)
        except ToolError as error:
            raise MCPToolError(f'{error.code}: {error}') from None
    return invoke

def create_server(store: IntelligenceStore) -> MCPServer:
    server = MCPServer('MatchLens',version='0.4.0',
        instructions='Read-only synthetic football intelligence. Values are supplied by Phase 2/3; never infer missing metrics.')
    session = ToolSession(MatchLensTools(store),retain_trace=False)
    for name in TOOL_METHODS:
        server.tool(name=name,description=getattr(session.tools,TOOL_METHODS[name]).__doc__,
            annotations=ToolAnnotations(read_only_hint=True,destructive_hint=False,open_world_hint=False),
            structured_output=True)(_mcp_callable(session,name))
    return server

def main(argv: list[str] | None = None) -> None:
    import argparse
    import sys
    from ..io import load_store
    parser = argparse.ArgumentParser(description='Serve existing MatchLens intelligence over MCP stdio.')
    parser.add_argument('--intelligence',required=True)
    parser.add_argument('--explanations',required=True)
    parser.add_argument('--events',required=True)
    parser.add_argument('--current-time',type=float,default=None)
    args = parser.parse_args(argv)
    try:
        store = load_store(args.intelligence,args.explanations,args.events,args.current_time)
    except Exception:
        print('MatchLens MCP: input files are missing or inconsistent.',file=sys.stderr)
        raise SystemExit(2) from None
    create_server(store).run(transport='stdio')

if __name__=='__main__':
    main()
