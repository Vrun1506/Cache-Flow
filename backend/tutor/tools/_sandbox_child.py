"""Child process for tutor.tools.sandbox. Standard library only; started with ``python -I -S``.

Read tutor/tools/sandbox.py first: this is defence in depth, not a security
boundary. The parent owns the process group, the wall clock and the memory
watchdog. This file does the in-process hardening and the test harness.

Protocol: every line this process writes to the real stdout starts with the
per-run nonce from the job file, then a JSON object. The user code only sees a
replaced ``sys.stdout``; lines without the nonce are ignored by the parent.
"""

import sys


def main() -> int:
    import ast
    import builtins
    import io
    import json
    import math
    import os
    import resource
    import traceback

    with open(sys.argv[1], encoding="utf-8") as fh:
        job = json.load(fh)
    nonce: str = job["nonce"]
    workdir: str = os.path.realpath(job["workdir"])
    max_output: int = job["max_output"]
    tests: list = job["tests"]
    code: str = job["code"]

    real_fd = os.dup(1)

    def emit(obj: dict) -> None:
        data = (nonce + " " + json.dumps(obj) + "\n").encode("utf-8")
        view = memoryview(data)
        while view:
            view = view[os.write(real_fd, view):]

    # -- resource limits (each applied only if the platform allows it) -------
    applied: list[str] = []

    def limit(name: str, soft: int, hard: int | None = None) -> None:
        res = getattr(resource, name, None)
        if res is None:
            return
        try:
            resource.setrlimit(res, (soft, soft if hard is None else hard))
            applied.append(name)
        except (ValueError, OSError):
            pass

    limit("RLIMIT_CORE", 0)
    limit("RLIMIT_CPU", int(job["cpu_s"]))
    limit("RLIMIT_FSIZE", 2 * 1024 * 1024)
    limit("RLIMIT_NOFILE", 64)
    limit("RLIMIT_NPROC", 0)  # no fork/exec; ignored for root
    if sys.platform.startswith("linux"):
        # Virtual size includes the interpreter's own mappings, hence the headroom.
        limit("RLIMIT_AS", (job["mem_mb"] + 256) * 1024 * 1024)
    emit({"t": "limits", "applied": applied})

    # -- everything the harness needs is imported BEFORE the modules are blocked
    import _io  # noqa: F401
    import posix

    original_import = builtins.__import__
    original_open = builtins.open
    original_eval = builtins.eval
    original_exec = builtins.exec

    def inside(path) -> bool:
        try:
            real = os.path.realpath(os.fsdecode(path))
        except (TypeError, ValueError):
            return False
        return real == workdir or real.startswith(workdir + os.sep)

    def deny(name: str):
        def blocked(*_a, **_k):
            raise PermissionError(f"{name} is not allowed in the sandbox")

        blocked.__name__ = name
        return blocked

    def guard_path(name: str, fn):
        def guarded(path=".", *a, **k):
            if not isinstance(path, int) and not inside(path):
                raise PermissionError(f"{name} outside the sandbox directory is not allowed")
            return fn(path, *a, **k)

        guarded.__name__ = name
        return guarded

    def guard_two(name: str, fn):
        def guarded(src, dst, *a, **k):
            if not (inside(src) and inside(dst)):
                raise PermissionError(f"{name} outside the sandbox directory is not allowed")
            return fn(src, dst, *a, **k)

        guarded.__name__ = name
        return guarded

    for mod in (os, posix):
        for name in ("system", "popen", "fork", "forkpty", "kill", "killpg", "abort", "setsid", "chroot",
                     "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
                     "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                     "posix_spawn", "posix_spawnp", "startfile", "putenv", "unsetenv"):
            if hasattr(mod, name):
                setattr(mod, name, deny(name))
        if mod is not os:
            continue  # importlib calls posix.* directly to find modules: guard only the os-level names
        for name in ("open", "listdir", "scandir", "remove", "unlink", "rmdir", "mkdir", "chmod", "chown",
                     "truncate", "utime", "mkfifo", "mknod", "chdir", "lchown"):  # not readlink: realpath() needs it
            if hasattr(mod, name):
                setattr(mod, name, guard_path(name, getattr(mod, name)))
        for name in ("rename", "replace", "link", "symlink"):
            if hasattr(mod, name):
                setattr(mod, name, guard_two(name, getattr(mod, name)))

    def safe_open(file, *a, **k):
        if not isinstance(file, int) and not inside(file):
            raise PermissionError("open() outside the sandbox directory is not allowed")
        return original_open(file, *a, **k)

    io.open = safe_open  # pathlib reads through io.open at call time
    _io_module = sys.modules.get("_io")
    if _io_module is not None:
        _io_module.open = safe_open

    denied = (
        "socket", "_socket", "ssl", "_ssl", "select", "selectors", "asyncio", "subprocess", "_posixsubprocess",
        "ctypes", "_ctypes", "multiprocessing", "_multiprocessing", "importlib", "imp", "pty", "tty", "fcntl",
        "resource", "signal", "_signal", "mmap", "http", "urllib", "ftplib", "smtplib", "telnetlib", "xmlrpc",
        "webbrowser", "shutil", "pickle", "marshal", "code", "codeop", "pdb", "gc", "threading", "_thread",
        "concurrent", "sqlite3", "ensurepip", "venv", "runpy", "site", "sysconfig", "pip", "setuptools",
    )
    for name in denied:
        sys.modules[name] = None  # `import name` now raises ImportError, even from other stdlib modules

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        top = name.split(".")[0]
        if level == 0 and top in denied:
            raise ImportError(f"import of {top!r} is not allowed in the sandbox")
        return original_import(name, globals, locals, fromlist, level)

    user_builtins = dict(vars(builtins))
    for name in ("eval", "exec", "compile", "breakpoint", "help", "exit", "quit", "memoryview", "open"):
        user_builtins.pop(name, None)
    user_builtins["open"] = safe_open
    user_builtins["__import__"] = guarded_import

    # -- bounded output capture ------------------------------------------------
    class Capped(io.TextIOBase):
        def __init__(self) -> None:
            self.parts: list[str] = []
            self.size = 0
            self.truncated = False

        def writable(self) -> bool:
            return True

        def write(self, s: str) -> int:
            room = max_output - self.size
            if len(s) > room:
                self.truncated = True
                s = s[: max(room, 0)]
            self.parts.append(s)
            self.size += len(s)
            return len(s)

        def getvalue(self) -> str:
            return "".join(self.parts)

    def new_namespace() -> dict:
        return {"__builtins__": user_builtins, "__name__": "__solution__"}

    def short(text, n: int = 200) -> str:
        text = str(text)
        return text if len(text) <= n else text[: n - 1] + "…"

    def describe(exc: BaseException) -> str:
        return short(f"{type(exc).__name__}: {exc}")

    def run_module(namespace: dict, stdin_text: str) -> tuple[Capped, BaseException | None]:
        out = Capped()
        old = sys.stdin, sys.stdout, sys.stderr
        sys.stdin, sys.stdout, sys.stderr = io.StringIO(stdin_text), out, Capped()
        try:
            original_exec(compiled, namespace)
            return out, None
        except BaseException as exc:  # SystemExit and KeyboardInterrupt included
            return out, exc
        finally:
            sys.stdin, sys.stdout, sys.stderr = old

    def values_equal(actual, expected) -> bool:
        if isinstance(expected, bool) or isinstance(actual, bool):
            return type(actual) is type(expected) and actual == expected
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if isinstance(expected, int) and isinstance(actual, int):
                return actual == expected
            return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-12)
        if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
            return type(actual) is type(expected) and len(actual) == len(expected) and all(
                values_equal(a, e) for a, e in zip(actual, expected)
            )
        if isinstance(expected, dict) and isinstance(actual, dict):
            return actual.keys() == expected.keys() and all(values_equal(actual[k], expected[k]) for k in expected)
        return actual == expected

    def classify(text: str, has_solve: bool, defined: dict) -> str:
        text = text.strip()
        if not text:
            return "stdin"
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError:
            return "stdin"
        if isinstance(tree.body, ast.Name) and tree.body.id not in defined:
            return "stdin"  # a bare word that names nothing is input text
        try:
            ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            return "expr"
        return "literal" if has_solve else "stdin"

    def compare_text(actual_text: str, expected: str) -> bool:
        a = [ln.rstrip() for ln in actual_text.strip().splitlines()]
        e = [ln.rstrip() for ln in expected.strip().splitlines()]
        return a == e

    # -- compile -----------------------------------------------------------------
    try:
        compiled = compile(code, "<solution>", "exec")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        emit({"t": "load", "ok": False, "error": describe(exc)})
        emit({"t": "done"})
        return 0
    emit({"t": "load", "ok": True})

    shared: dict = {}

    def shared_namespace():
        """Run the module once for the call/expression style of test."""
        if "ns" not in shared:
            ns = new_namespace()
            _, error = run_module(ns, "")
            shared["ns"], shared["error"] = ns, error
        return shared["ns"], shared["error"]

    def run_test(test: dict) -> dict:
        raw_in, raw_exp = str(test.get("input", "")), str(test.get("expected", ""))
        row = {"t": "test", "input": short(raw_in), "expected": short(raw_exp), "actual": "", "ok": False, "error": None}
        try:
            ns_probe, load_error = shared_namespace()
            kind = classify(raw_in, callable(ns_probe.get("solve")) and load_error is None, ns_probe)
            if kind != "stdin" and load_error is not None:
                row["error"] = describe(load_error)
                return row
            if kind in ("literal", "expr"):
                namespace = shared["ns"]
                try:
                    if kind == "literal":
                        args = ast.literal_eval(raw_in.strip())
                        call_args = args if isinstance(args, tuple) else (args,)
                        actual = namespace["solve"](*call_args)
                    else:
                        actual = original_eval(compile(raw_in.strip(), "<test>", "eval"), namespace)
                except NameError as exc:
                    missing = getattr(exc, "name", None)
                    if kind == "expr" and missing and missing in raw_in and missing not in namespace:
                        kind = "stdin"  # a bare word is stdin text, not an expression
                    else:
                        raise
                else:
                    try:
                        expected = ast.literal_eval(raw_exp.strip())
                        row["ok"] = bool(values_equal(actual, expected))
                    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                        row["ok"] = str(actual).strip() == raw_exp.strip() or repr(actual) == raw_exp.strip()
                    row["actual"] = short(repr(actual), 300)
                    return row
            out, error = run_module(new_namespace(), raw_in)
            text = out.getvalue()
            row["actual"] = short(text.strip(), 300)
            if out.truncated:
                row["error"] = "output too long"
            elif error is not None:
                row["error"] = describe(error)
            else:
                row["ok"] = compare_text(text, raw_exp)
            return row
        except BaseException as exc:
            row["error"] = describe(exc)
            return row

    for test in tests:
        try:
            emit(run_test(test))
        except BaseException:  # never let the harness itself die silently
            emit({"t": "test", "input": "", "expected": "", "actual": "", "ok": False,
                  "error": short(traceback.format_exc().splitlines()[-1])})
    emit({"t": "done"})
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        sys.stdout.flush()
    raise SystemExit(code)
