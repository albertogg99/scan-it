import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("registry", Path(__file__).resolve().parents[1] / "main.py")
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        project_root = Path(self.temporary_directory.name)
        self.root = project_root / "servers" / "zurich-hackathon"
        self.tools_dir = self.root / "tools"
        self.tools_dir.mkdir(parents=True)
        self.requirements_dir = self.tools_dir / "requirements"
        self.requirements_dir.mkdir()
        (self.root / "config").mkdir()
        self.config_path = self.root / "config" / "tools_config.yaml"
        self.packages_dir = self.root / "tool_packages"
        self.base_patch = patch.object(registry, "BASE_DIR", project_root)
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)
        path_patch = patch.object(sys, "path", sys.path.copy())
        path_patch.start()
        self.addCleanup(path_patch.stop)

    def test_empty_configuration_registers_no_tools(self):
        self.config_path.write_text("", encoding="utf-8")
        with patch.object(registry.importlib, "import_module") as import_module:
            registry.register_tools(Mock())
        import_module.assert_not_called()

    def test_null_settings_become_an_empty_mapping(self):
        self.config_path.write_text("echo:\n", encoding="utf-8")
        self.assertEqual(registry.load_config(self.config_path), {"echo": {}})

    def test_invalid_configuration_is_rejected(self):
        for config in ("- echo", "echo: true", "../echo: {}", "__init__: {}", "true: {}"):
            with self.subTest(config=config):
                self.config_path.write_text(config, encoding="utf-8")
                with self.assertRaises(ValueError):
                    registry.load_config(self.config_path)

    def test_missing_server_instructions_are_optional(self):
        instructions_path = self.root / "config" / "server_instructions.md"
        self.assertIsNone(registry.load_server_instructions(instructions_path))

    def test_blank_server_instructions_are_ignored(self):
        instructions_path = self.root / "config" / "server_instructions.md"
        for content in ("", " \n\t "):
            with self.subTest(content=content):
                instructions_path.write_text(content, encoding="utf-8")
                self.assertIsNone(registry.load_server_instructions(instructions_path))

    def test_server_instructions_preserve_multiline_utf8_text(self):
        instructions_path = self.root / "config" / "server_instructions.md"
        instructions = "Keep the user's language.\nPreserve accents such as \u00e9."
        instructions_path.write_text(f"\n{instructions}\n", encoding="utf-8")
        self.assertEqual(registry.load_server_instructions(instructions_path), instructions)

    def test_unreadable_server_instructions_are_not_ignored(self):
        instructions_path = self.root / "config" / "server_instructions.md"
        with patch.object(Path, "read_text", side_effect=PermissionError("Access denied")):
            with self.assertRaises(PermissionError):
                registry.load_server_instructions(instructions_path)

    def test_invalid_utf8_server_instructions_are_rejected(self):
        instructions_path = self.root / "config" / "server_instructions.md"
        instructions_path.write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            registry.load_server_instructions(instructions_path)

    def test_main_passes_server_instructions_for_both_transports(self):
        instructions = "Use the configured tools when appropriate."
        instructions_path = self.root / "config" / "server_instructions.md"
        instructions_path.write_text(instructions, encoding="utf-8")
        for transport in ("stdio", "streamable-http"):
            with (
                self.subTest(transport=transport),
                patch.object(sys, "argv", ["main.py", "--transport", transport]),
                patch.object(registry.logging, "basicConfig"),
                patch.dict(registry.os.environ, {"MCP_HOST": "127.0.0.1", "MCP_PORT": "8555"}),
                patch.object(registry, "register_tools") as register_tools,
                patch("mcp.server.mcpserver.MCPServer") as server_class,
            ):
                registry.main()
                server_class.assert_called_once_with("zurich-hackathon", instructions=instructions)
                register_tools.assert_called_once_with(server_class.return_value, self.root)
                self.assertEqual(server_class.return_value.run.call_args.kwargs["transport"], transport)

    def test_main_selects_server_directory_from_cli_or_environment(self):
        server_dir = self.root / "another-server"
        (server_dir / "config").mkdir(parents=True)
        (server_dir / "config" / "server_instructions.md").write_text("Server-specific guidance", encoding="utf-8")
        for use_cli in (False, True):
            arguments = ["main.py", "--transport", "stdio"]
            if use_cli:
                arguments.extend(["--server-dir", str(server_dir)])
            with (
                self.subTest(use_cli=use_cli),
                patch.object(sys, "argv", arguments),
                patch.object(registry.logging, "basicConfig"),
                patch.dict(registry.os.environ, {
                    "MCP_SERVER_DIR": str(self.root if use_cli else server_dir),
                    "MCP_SERVER_NAME": "Another MCP",
                }),
                patch.object(registry, "register_tools") as register_tools,
                patch("mcp.server.mcpserver.MCPServer") as server_class,
            ):
                registry.main()
                server_class.assert_called_once_with("Another MCP", instructions="Server-specific guidance")
                register_tools.assert_called_once_with(server_class.return_value, server_dir)

    def test_selected_server_controls_tools_configuration_and_packages(self):
        server_dir = self.root / "another-server"
        tools_dir = server_dir / "tools"
        tools_dir.mkdir(parents=True)
        (server_dir / "config").mkdir()
        (server_dir / "config" / "tools_config.yaml").write_text("enabled:\n  prefix: other\n", encoding="utf-8")
        (tools_dir / "enabled.py").touch()
        module = SimpleNamespace(register=Mock())
        server = Mock()
        with (
            patch.object(registry, "_install_tool_requirements") as install,
            patch.object(registry.importlib, "import_module", return_value=module) as import_module,
        ):
            registry.register_tools(server, server_dir)
        install.assert_called_once_with(tools_dir, ["enabled"], server_dir / "tool_packages")
        import_module.assert_called_once_with("tools.enabled")
        module.register.assert_called_once_with(server, {"prefix": "other"})
        self.assertEqual(sys.path[0], str(server_dir))

    def test_packages_directory_can_be_outside_read_only_server(self):
        self.config_path.write_text("{}", encoding="utf-8")
        packages_dir = self.root / "writable-packages"
        with (
            patch.dict(registry.os.environ, {"MCP_PACKAGES_DIR": str(packages_dir)}),
            patch.object(registry, "_install_tool_requirements") as install,
        ):
            registry.register_tools(Mock())
        install.assert_called_once_with(self.tools_dir, [], packages_dir)

    def test_separate_processes_load_same_named_modules_from_their_own_servers(self):
        for name in ("first", "second"):
            server_dir = self.root / name
            tools_dir = server_dir / "tools"
            tools_dir.mkdir(parents=True)
            (server_dir / "config").mkdir()
            (server_dir / "config" / "tools_config.yaml").write_text("echo: {}", encoding="utf-8")
            (tools_dir / "__init__.py").write_text("", encoding="utf-8")
            (tools_dir / "echo.py").write_text(
                f"def register(mcp, config):\n    mcp.identity = {name!r}\n", encoding="utf-8",
            )
            with self.subTest(server=name):
                result = subprocess.run(
                    [sys.executable, "-c", (
                        "from pathlib import Path; from types import SimpleNamespace; "
                        "import sys; import main; server = SimpleNamespace(); "
                        "main.register_tools(server, Path(sys.argv[1])); "
                        "assert server.identity == sys.argv[2]"
                    ), str(server_dir), name],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_only_configured_modules_are_installed_and_registered(self):
        self.config_path.write_text("enabled:\n  prefix: hello\n", encoding="utf-8")
        (self.tools_dir / "enabled.py").touch()
        (self.tools_dir / "disabled.py").touch()
        module = SimpleNamespace(register=Mock())
        server = Mock()
        with (
            patch.object(registry, "_install_tool_requirements") as install,
            patch.object(registry.importlib, "import_module", return_value=module) as import_module,
        ):
            registry.register_tools(server)
        install.assert_called_once_with(self.tools_dir, ["enabled"], self.packages_dir)
        import_module.assert_called_once_with("tools.enabled")
        module.register.assert_called_once_with(server, {"prefix": "hello"})

    def test_missing_module_fails_before_installing_requirements(self):
        self.config_path.write_text("missing: {}", encoding="utf-8")
        with patch.object(registry, "_install_tool_requirements") as install:
            with self.assertRaises(FileNotFoundError):
                registry.register_tools(Mock())
        install.assert_not_called()

    def test_missing_registration_function_fails_startup(self):
        self.config_path.write_text("invalid: {}", encoding="utf-8")
        (self.tools_dir / "invalid.py").touch()
        with patch.object(registry.importlib, "import_module", return_value=SimpleNamespace()):
            with self.assertRaises(TypeError):
                registry.register_tools(Mock())

    def test_enabled_requirements_are_resolved_together(self):
        for name in ("first", "second", "disabled"):
            (self.requirements_dir / f"{name}.txt").touch()
        with (
            patch.object(registry.subprocess, "run") as run,
            patch.object(sys, "path", sys.path.copy()),
        ):
            registry._install_tool_requirements(self.tools_dir, ["first", "second"], self.packages_dir)
            self.assertEqual(sys.path[0], str(self.packages_dir))
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("--upgrade", command)
        self.assertIn(str(self.requirements_dir / "first.txt"), command)
        self.assertIn(str(self.requirements_dir / "second.txt"), command)
        self.assertNotIn(str(self.requirements_dir / "disabled.txt"), command)
        self.assertEqual(command.count("-r"), 2)
        self.assertEqual(run.call_args.kwargs, {"check": True, "stdout": sys.stderr})

    def test_no_requirements_skips_pip(self):
        with patch.object(registry.subprocess, "run") as run:
            registry._install_tool_requirements(self.tools_dir, ["echo"], self.packages_dir)
        run.assert_not_called()

    def test_failed_dependency_install_stops_startup(self):
        (self.requirements_dir / "broken.txt").touch()
        with patch.object(registry.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")):
            with self.assertRaises(subprocess.CalledProcessError):
                registry._install_tool_requirements(self.tools_dir, ["broken"], self.packages_dir)


if __name__ == "__main__":
    unittest.main()