import argparse
import importlib
import logging
import os
from pathlib import Path
import subprocess
import sys

import yaml


BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("dynamic-mcp")


def load_config(config_path: Path) -> dict[str, dict]:
    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ValueError("Tools configuration must be a mapping of module names to settings")
    for name, settings in config.items():
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise ValueError(f"Invalid tool module name: {name!r}")
        if settings is None:
            config[name] = {}
        elif not isinstance(settings, dict):
            raise ValueError(f"Configuration for {name!r} must be a mapping")
    return config


def load_server_instructions(instructions_path: Path) -> str | None:
    try:
        instructions = instructions_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return instructions.strip() or None


def _install_tool_requirements(tools_dir: Path, tool_names: list[str], packages_dir: Path):
    """Resolve and install requirements for configured tools before importing them."""
    requirements = [
        tools_dir / "requirements" / f"{name}.txt"
        for name in tool_names
        if (tools_dir / "requirements" / f"{name}.txt").is_file()
    ]
    if not requirements:
        return

    packages_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check", "--no-cache-dir", "--upgrade",
        "--target", str(packages_dir),
    ]
    for requirement in requirements:
        command.extend(["-r", str(requirement)])
    logger.info("Installing dependencies for configured tools")
    subprocess.run(command, check=True, stdout=sys.stderr)

    if str(packages_dir) not in sys.path:
        sys.path.insert(0, str(packages_dir))
    importlib.invalidate_caches()


def register_tools(server, server_dir: Path | None = None):
    """Load multi-agent-compatible tool modules using register(mcp, config)."""
    server_dir = (server_dir or BASE_DIR / "servers" / "zurich-hackathon").resolve()
    tools_dir = server_dir / "tools"
    config = load_config(server_dir / "config" / "tools_config.yaml")
    for name in config:
        if not (tools_dir / f"{name}.py").is_file():
            raise FileNotFoundError(f"Configured tool module does not exist: tools/{name}.py")

    packages_dir = Path(os.environ.get("MCP_PACKAGES_DIR", str(server_dir / "tool_packages")))
    _install_tool_requirements(tools_dir, list(config), packages_dir.resolve())
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    importlib.invalidate_caches()
    for name, settings in config.items():
        module = importlib.import_module(f"tools.{name}")
        register = getattr(module, "register", None)
        if not callable(register):
            raise TypeError(f"Tool module {name!r} must define register(mcp, config)")
        register(server, settings)
        logger.info("Registered tool module: %s", name)


def main():
    parser = argparse.ArgumentParser(description="Load Python tools and expose them over MCP")
    parser.add_argument("--transport", choices=("streamable-http", "stdio"), default="streamable-http")
    parser.add_argument(
        "--server-dir",
        type=Path,
        default=Path(os.environ.get("MCP_SERVER_DIR", str(BASE_DIR / "servers" / "zurich-hackathon"))),
        help="Server folder containing tools/ and config/ (or set MCP_SERVER_DIR)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    from mcp.server.mcpserver import MCPServer

    server_dir = args.server_dir.resolve()
    server = MCPServer(
        os.environ.get("MCP_SERVER_NAME", server_dir.name),
        instructions=load_server_instructions(server_dir / "config" / "server_instructions.md"),
    )
    register_tools(server, server_dir)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8555")),
            stateless_http=True,
        )


if __name__ == "__main__":
    main()