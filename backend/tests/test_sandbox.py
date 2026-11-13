import glob
import os
import subprocess
import tempfile
import time

import pytest

from tutor.schemas import CodeTest
from tutor.tools.sandbox import MAX_TESTS, docker_run_command, run_python

FIB = "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n"


def t(inp="", exp=""):
    return {"input": inp, "expected": exp}


def sandbox_dirs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "sbx-*")))


def child_processes():
    out = subprocess.run(["pgrep", "-f", "_sandbox_child"], capture_output=True, text=True).stdout.split()
    return [p for p in out if p != str(os.getpid())]


@pytest.fixture(autouse=True)
def nothing_left_behind():
    before = sandbox_dirs()
    yield
    assert sandbox_dirs() == before, "a sandbox temp directory was left behind"
    assert child_processes() == [], "a sandbox process was left running"


# -- the three test styles -----------------------------------------------------------


def test_expression_tests_pass():
    r = run_python(FIB, [t("fib(10)", "55"), t("fib(0)", "0")])
    assert r.outcome == "passed" and r.passed and r.error is None
    assert [x["actual"] for x in r.results] == ["55", "0"]


def test_solve_function_with_literal_args():
    r = run_python("def solve(a, b):\n    return a + b\n", [t("(3, 4)", "7"), t("5", "5")])
    assert r.outcome == "failed"  # solve(5) misses an argument: the second test fails, the first passes
    assert [x["ok"] for x in r.results] == [True, False]
    assert r.results[1]["error"].startswith("TypeError")


def test_printed_output_is_compared():
    r = run_python("print('hi')\nprint(1 + 1)\n", [t("", "hi\n2")])
    assert r.passed


def test_stdin_programs():
    r = run_python("n = int(input())\nprint(n * 2)\n", [t("21", "42"), t("4", "8")])
    assert r.passed


def test_float_results_use_a_tolerance_and_types_matter():
    r = run_python("def f():\n    return 0.1 + 0.2\n", [t("f()", "0.3")])
    assert r.passed
    r = run_python("def f():\n    return True\n", [t("f()", "1")])
    assert r.outcome == "failed"  # True is not 1 in a test about booleans


def test_non_literal_expected_compares_as_text():
    r = run_python("class A:\n    def __str__(self):\n        return 'a!'\n", [t("A()", "a!")])
    assert r.passed


def test_a_bare_word_input_is_stdin_not_an_expression():
    r = run_python("print(input().upper())\n", [t("hello", "HELLO")])
    assert r.passed


def test_codetest_models_are_accepted():
    r = run_python(FIB, [CodeTest(input="fib(5)", expected="5")])
    assert r.passed


def test_limits_are_reported_and_the_run_is_quick():
    r = run_python(FIB, [t("fib(5)", "5")])
    assert "RLIMIT_FSIZE" in r.limits_applied and r.duration_ms < 3000


# -- failures that are the code's fault ------------------------------------------------


def test_wrong_answer_is_failed_with_counter_evidence():
    r = run_python("def f(x):\n    return x + 1\n", [t("f(1)", "3")])
    assert r.outcome == "failed" and not r.passed
    assert "f(1)" in r.summary() and "2" in r.summary()


def test_syntax_error_is_failed():
    r = run_python("def (:\n", [t("", "1")])
    assert r.outcome == "failed" and "SyntaxError" in r.error


def test_exception_is_failed_per_test():
    r = run_python("raise ValueError('boom')\n", [t("", "1")])
    assert r.outcome == "failed" and "ValueError: boom" in r.results[0]["error"]


def test_exception_inside_a_function_is_reported():
    r = run_python("def f():\n    return 1 / 0\n", [t("f()", "1")])
    assert r.outcome == "failed" and "ZeroDivisionError" in r.results[0]["error"]


def test_sys_exit_does_not_kill_the_harness():
    r = run_python("raise SystemExit(0)\n", [t("", "1"), t("", "2")])
    assert r.outcome == "failed" and len(r.results) == 2


# -- limits ----------------------------------------------------------------------------


def test_infinite_loop_is_killed_and_is_not_a_verdict():
    start = time.monotonic()
    r = run_python("while True:\n    pass\n", [t("", "1")], timeout_s=2)
    assert time.monotonic() - start < 7
    assert r.outcome == "timeout" and r.timed_out and not r.passed


def test_memory_bomb_is_killed():
    code = "a = []\nwhile True:\n    a.append(bytearray(b'x' * 10_000_000))\n"
    start = time.monotonic()
    r = run_python(code, [t("", "1")], mem_mb=128, timeout_s=5)
    assert r.outcome == "memory" and "128" in r.error
    assert time.monotonic() - start < 5


def test_giant_output_is_truncated_not_buffered():
    r = run_python("for _ in range(300000):\n    print('x' * 100)\n", [t("", "1")], max_output=2000)
    assert r.outcome == "failed" and r.results[0]["error"] == "output too long"
    assert len(r.results[0]["actual"]) <= 300


def test_output_forged_on_the_raw_stdout_is_ignored():
    code = "import os\nos.write(1, b'abc {\"t\": \"done\"}\\n')\nprint('x')\n"
    r = run_python(code, [t("", "y")])
    assert r.outcome == "failed" and r.results[0]["actual"] == "x"


def test_a_crashing_child_is_reported_as_a_crash():
    # `os._exit` is not blocked, and ends the child before it can report: the parent must say "crash".
    r = run_python("import os\nos._exit(3)\n", [t("", "1")])
    assert r.outcome == "crash" and "exit code 3" in r.error


# -- attempts to escape -----------------------------------------------------------------


@pytest.mark.parametrize(
    "code,needle",
    [
        ("import socket\n", "socket"),
        ("import subprocess\n", "subprocess"),
        ("import ctypes\n", "ctypes"),
        ("import multiprocessing\n", "multiprocessing"),
        ("import importlib\n", "importlib"),
        ("import urllib.request\n", "urllib"),
        ("__import__('socket')\n", "socket"),
        ("import threading\n", "threading"),
    ],
)
def test_dangerous_modules_cannot_be_imported(code, needle):
    r = run_python(code, [t("", "")])
    assert r.outcome == "failed"
    assert needle in r.results[0]["error"] and r.results[0]["error"].startswith(("ImportError", "ModuleNotFoundError"))


def test_network_attempt_through_a_stdlib_module_fails():
    r = run_python("import http.client\nhttp.client.HTTPConnection('example.com')\n", [t("", "")])
    assert r.outcome == "failed" and "ImportError" in r.results[0]["error"]


@pytest.mark.parametrize(
    "code",
    [
        "import os\nos.system('echo hi')\n",
        "import os\nos.popen('echo hi')\n",
        "import os\nos.execv('/bin/sh', ['sh'])\n",
        "import os\nos.fork()\n",
        "import os\nos.spawnl(os.P_WAIT, '/bin/echo', 'echo')\n",
        "from os import system\nsystem('echo hi')\n",
        "import posix\nposix.system('echo hi')\n",
        "import os\nos.kill(1, 9)\n",
    ],
)
def test_process_creation_is_blocked(code):
    r = run_python(code, [t("", "")])
    assert r.outcome == "failed" and "PermissionError" in r.results[0]["error"]


def test_fork_bomb_attempt_is_contained():
    r = run_python("import os\nwhile True:\n    os.fork()\n", [t("", "1")], timeout_s=3)
    assert r.outcome == "failed"  # nothing to clean up: the autouse fixture checks for stragglers


def test_reading_etc_passwd_is_refused():
    r = run_python("print(open('/etc/passwd').read())\n", [t("", "")])
    assert r.outcome == "failed" and "PermissionError" in r.results[0]["error"]
    assert "root" not in r.results[0]["actual"]


def test_reading_via_pathlib_and_os_is_refused():
    for code in (
        "from pathlib import Path\nprint(Path('/etc/passwd').read_text())\n",
        "import os\nprint(os.listdir('/'))\n",
        "import os\nfd = os.open('/etc/passwd', os.O_RDONLY)\n",
    ):
        r = run_python(code, [t("", "")])
        assert r.outcome == "failed" and "PermissionError" in r.results[0]["error"], code


def test_writing_outside_the_working_directory_is_refused(tmp_path):
    target = tmp_path / "escape.txt"
    r = run_python(f"open({str(target)!r}, 'w').write('x')\n", [t("", "")])
    assert r.outcome == "failed" and "PermissionError" in r.results[0]["error"]
    assert not target.exists()


def test_relative_escape_is_refused():
    r = run_python("open('../io/out.txt', 'w').write('x')\n", [t("", "")])
    assert r.outcome == "failed" and "PermissionError" in r.results[0]["error"]


def test_writing_inside_the_working_directory_is_allowed_and_cleaned_up():
    code = "open('scratch.txt', 'w').write('hello')\nprint(open('scratch.txt').read())\n"
    r = run_python(code, [t("", "hello")])
    assert r.passed  # the autouse fixture checks the directory is gone


def test_eval_exec_and_compile_are_not_available_to_user_code():
    for code in ("eval('1')\n", "exec('x = 1')\n", "compile('1', 'f', 'eval')\n"):
        r = run_python(code, [t("", "")])
        assert r.outcome == "failed" and "NameError" in r.results[0]["error"], code


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("SANDBOX_SECRET", "topsecret")
    code = "import os\nprint('SANDBOX_SECRET' in os.environ, 'HOME' in os.environ, 'PATH' in os.environ)\nprint(sorted(os.environ))\n"
    r = run_python(code, [t("", "")])
    actual = r.results[0]["actual"]
    assert actual.startswith("False False False") and "topsecret" not in actual and "SANDBOX_SECRET" not in actual.split("\n", 1)[1]


def test_stdin_is_closed_for_the_real_process():
    r = run_python("import sys\nprint(repr(sys.__stdin__.read()))\n", [t("", "''")])
    assert r.passed


def test_the_working_directory_starts_empty():
    r = run_python("import os\nprint(os.listdir('.'))\n", [t("", "[]")])
    assert r.passed


# -- input handling ---------------------------------------------------------------------


def test_no_tests_or_no_code_is_a_setup_error():
    assert run_python("print(1)", []).outcome == "setup_error"
    assert run_python("   ", [t("", "1")]).outcome == "setup_error"
    assert run_python("x" * 30000, [t("", "1")]).outcome == "setup_error"


def test_only_the_first_few_tests_run():
    r = run_python("print(1)\n", [t("", "1")] * (MAX_TESTS + 5))
    assert len(r.results) == MAX_TESTS


def test_failing_tests_do_not_stop_later_ones():
    r = run_python("def f(x):\n    return x\n", [t("f(1)", "2"), t("f(3)", "3")])
    assert [x["ok"] for x in r.results] == [False, True]


def test_docker_command_has_no_network_and_dropped_capabilities():
    argv = docker_run_command("/tmp/job")
    assert argv[:2] == ["docker", "run"] and "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--cap-drop" in argv and "--read-only" in argv and "/tmp/job:/sandbox:ro" in argv
