"""Standalone stdlib-only process runner for exactly one Plugin API v4 plugin."""

from __future__ import annotations

import argparse
import importlib
import importlib.abc
import os
import sys
import threading
import types
from pathlib import Path
from typing import Any, Mapping, Sequence

_PRIVATE_RUNTIME_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PRIVATE_RUNTIME_ROOT))
from sakura_plugin_sdk import PluginApiError, PluginContext, RpcPeer
sys.modules.pop("sakura_plugin_sdk", None)


_WINDOWS_DEPENDENCY_PATHS = (
    ("win32",),
    ("win32", "lib"),
    ("pythonwin",),
)


def _dependency_import_paths(
    dependency_root: Path,
    *,
    windows: bool,
) -> list[Path]:
    """Return explicit private import roots without executing dependency .pth files."""

    paths = [dependency_root]
    if windows:
        paths.extend(
            candidate
            for parts in _WINDOWS_DEPENDENCY_PATHS
            if (candidate := dependency_root.joinpath(*parts)).is_dir()
        )
    return paths


class _CoreImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname == "app" or fullname.startswith("app."):
            raise ModuleNotFoundError(
                "Plugin API v4 processes cannot import Sakura Core private modules"
            )
        return None


class PluginRunner:
    def __init__(
        self,
        *,
        plugin_id: str,
        generation_id: str,
        plugin_root: Path,
        dependency_root: Path | None,
        data_dir: Path,
        entry: str,
    ) -> None:
        self.plugin_id = plugin_id
        self.generation_id = generation_id
        self.plugin_root = plugin_root
        self.dependency_root = dependency_root
        self.data_dir = data_dir
        self.entry = entry
        self._context: PluginContext | None = None
        self._initialized = False
        self._close_lock = threading.Lock()
        self._windows_dll_handles: list[object] = []
        input_stream = sys.stdin.buffer
        output_stream = sys.stdout.buffer
        sys.stdout = sys.stderr
        self._peer = RpcPeer(
            input_stream,
            output_stream,
            generation_id=generation_id,
            plugin_id=plugin_id,
            request_handler=self._handle_request,
        )

    def run(self) -> int:
        self._prepare_import_path()
        self._peer.start(thread_name=f"sakura-plugin-{self.plugin_id}-reader")
        self._peer.wait()
        self._close_context()
        return 0

    def validate_entry(self) -> None:
        self._prepare_import_path()
        module_name, separator, class_name = self.entry.partition(":")
        if separator != ":":
            raise PluginApiError("PLUGIN_ENTRY_INVALID", plugin_id=self.plugin_id)
        module = importlib.import_module(module_name)
        plugin_type = getattr(module, class_name, None)
        if not callable(plugin_type):
            raise PluginApiError("PLUGIN_ENTRY_INVALID", plugin_id=self.plugin_id)

    def _prepare_import_path(self) -> None:
        sdk_root = str(Path(__file__).resolve().parents[1] / "plugin_sdk")
        roots = [sdk_root, str(self.plugin_root)]
        if self.dependency_root is not None:
            roots.extend(
                str(path)
                for path in _dependency_import_paths(
                    self.dependency_root,
                    windows=os.name == "nt",
                )
            )
        stdlib = [
            item
            for item in sys.path
            if item
            and "site-packages" not in item
            and "dist-packages" not in item
            and Path(item).resolve(strict=False) != Path(__file__).resolve().parents[2]
            and Path(item).resolve(strict=False) != _PRIVATE_RUNTIME_ROOT
        ]
        sys.path[:] = list(dict.fromkeys([*roots, *stdlib]))
        self._prepare_windows_dependency_dlls()
        sys.meta_path.insert(0, _CoreImportBlocker())
        sys.modules["__main__"] = types.ModuleType("__main__")

    def _prepare_windows_dependency_dlls(self) -> None:
        """Keep private pywin32 DLL lookup active under ``python -S`` on Windows."""

        if (
            os.name != "nt"
            or self.dependency_root is None
            or self._windows_dll_handles
            or not hasattr(os, "add_dll_directory")
        ):
            return
        system32 = self.dependency_root / "pywin32_system32"
        if system32.is_dir():
            self._windows_dll_handles.append(os.add_dll_directory(str(system32)))

    def _handle_request(self, name: str, payload: Mapping[str, Any]) -> object:
        if name == "runtime.initialize":
            return self._initialize()
        if name == "service.call":
            context = self._require_context()
            service_key = payload.get("serviceKey")
            method = payload.get("method")
            args = payload.get("args")
            if (
                not isinstance(service_key, str)
                or not isinstance(method, str)
                or not isinstance(args, list)
            ):
                raise PluginApiError("PLUGIN_PROTOCOL_INVALID")
            return context.call_local(service_key, method, args)
        if name == "event.emit":
            context = self._require_context()
            event_name = payload.get("name")
            if not isinstance(event_name, str) or not event_name.startswith("sakura.host."):
                raise PluginApiError("HOST_EVENT_NAME_INVALID")
            context.emit(event_name, payload.get("payload"))
            return None
        if name == "callback.invoke":
            context = self._require_context()
            handle = payload.get("handle")
            shape = payload.get("shape")
            args = payload.get("args")
            if not isinstance(handle, str) or not isinstance(shape, str) or not isinstance(args, list):
                raise PluginApiError("PLUGIN_PROTOCOL_INVALID")
            return context.invoke_callback(handle, shape, args)
        if name == "config.apply":
            context = self._require_context()
            values = payload.get("values")
            if not isinstance(values, Mapping):
                raise PluginApiError("CONFIG_VALUE_INVALID", plugin_id=self.plugin_id)
            return {"applicationState": context.config.update(dict(values))}
        if name == "runtime.close":
            self._close_context()
            # The Core owns the transport lifetime. Returning first guarantees
            # the close response is written before Core closes stdin; that EOF
            # then releases ``run()`` without racing the response writer.
            return None
        raise PluginApiError("PLUGIN_REQUEST_UNKNOWN")

    def _initialize(self) -> object:
        if self._initialized:
            raise PluginApiError("PLUGIN_ALREADY_INITIALIZED", plugin_id=self.plugin_id)
        module_name, separator, class_name = self.entry.partition(":")
        if separator != ":":
            raise PluginApiError("PLUGIN_ENTRY_INVALID", plugin_id=self.plugin_id)
        context = PluginContext(
            self.plugin_id,
            self.plugin_root,
            self.data_dir,
            self._call_remote_service,
            self._call_remote_request,
        )
        try:
            module = importlib.import_module(module_name)
            plugin_type = getattr(module, class_name)
            plugin = plugin_type()
            setup = getattr(plugin, "setup", None)
            if not callable(setup):
                raise PluginApiError("PLUGIN_ENTRY_INVALID", plugin_id=self.plugin_id)
            setup(context)
            context.commit()
        except Exception:
            context.close()
            raise
        self._context = context
        self._initialized = True
        return {
            "pid": os.getpid(),
            "provides": context.service_exports(),
        }

    def _call_remote_service(
        self,
        service_key: str,
        method: str,
        args: Sequence[Any],
    ) -> object:
        return self._peer.request(
            "service.call",
            {
                "serviceKey": service_key,
                "method": method,
                "args": list(args),
            },
        )

    def _call_remote_request(
        self,
        name: str,
        payload: Mapping[str, Any],
    ) -> object:
        return self._peer.request(name, payload)

    def _require_context(self) -> PluginContext:
        if self._context is None:
            raise PluginApiError("PLUGIN_NOT_ACTIVE", plugin_id=self.plugin_id)
        return self._context

    def _close_context(self) -> None:
        with self._close_lock:
            context = self._context
            self._context = None
        if context is not None:
            context.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--dependency-root", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--validate-entry", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = PluginRunner(
        plugin_id=args.plugin_id,
        generation_id=args.generation_id,
        plugin_root=args.plugin_root.resolve(),
        dependency_root=(
            args.dependency_root.resolve()
            if args.dependency_root is not None
            else None
        ),
        data_dir=args.data_dir.resolve(),
        entry=args.entry,
    )
    if args.validate_entry:
        runner.validate_entry()
        return 0
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
