import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from code_executor import (
    CodeAction,
    UnsafePathError,
    execute_python_file,
    parse_action,
    resolve_safe_path,
    write_action_file,
)


class TestParseAction(unittest.TestCase):
    def test_extracts_path_content_and_run_true(self):
        text = (
            "説明テキストです。\n"
            '<ACTION path="hello.py" run="true">\n'
            "```python\n"
            "print('Hello World')\n"
            "```\n"
            "</ACTION>\n"
        )
        action = parse_action(text)
        self.assertIsNotNone(action)
        self.assertEqual(action.path, "hello.py")
        self.assertEqual(action.content, "print('Hello World')\n")
        self.assertTrue(action.run)

    def test_run_false_is_parsed_as_false(self):
        text = (
            '<ACTION path="foo.py" run="false">\n'
            "```python\nx = 1\n```\n"
            "</ACTION>"
        )
        action = parse_action(text)
        self.assertFalse(action.run)

    def test_returns_none_when_no_action_block(self):
        text = "これは通常の説明文で、コードブロックはありません。"
        self.assertIsNone(parse_action(text))


class TestResolveSafePath(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_resolves_relative_path_within_workspace(self):
        resolved = resolve_safe_path("hello.py", self.workspace)
        self.assertEqual(resolved, (self.workspace / "hello.py").resolve())

    def test_resolves_nested_relative_path_within_workspace(self):
        resolved = resolve_safe_path("sub/hello.py", self.workspace)
        self.assertEqual(resolved, (self.workspace / "sub" / "hello.py").resolve())

    def test_blocks_parent_directory_traversal(self):
        with self.assertRaises(UnsafePathError):
            resolve_safe_path("../evil.py", self.workspace)

    def test_blocks_absolute_path_outside_workspace(self):
        with self.assertRaises(UnsafePathError):
            resolve_safe_path("C:/Windows/system.ini", self.workspace)


class TestWriteActionFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_writes_file_with_content(self):
        action = CodeAction(path="hello.py", content="print('Hello World')\n", run=True)
        written_path = write_action_file(action, self.workspace)
        self.assertTrue(written_path.exists())
        self.assertEqual(written_path.read_text(encoding="utf-8"), "print('Hello World')\n")

    def test_creates_nested_directories(self):
        action = CodeAction(path="sub/dir/hello.py", content="pass\n", run=False)
        written_path = write_action_file(action, self.workspace)
        self.assertTrue(written_path.exists())

    def test_refuses_unsafe_path(self):
        action = CodeAction(path="../evil.py", content="pass\n", run=False)
        with self.assertRaises(UnsafePathError):
            write_action_file(action, self.workspace)


class TestExecutePythonFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_captures_stdout(self):
        path = self.workspace / "hello.py"
        path.write_text("print('Hello World')\n", encoding="utf-8")
        result = execute_python_file(path)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Hello World", result.stdout)
        self.assertFalse(result.timed_out)

    def test_captures_stderr_on_error(self):
        path = self.workspace / "bad.py"
        path.write_text("raise ValueError('boom')\n", encoding="utf-8")
        result = execute_python_file(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ValueError", result.stderr)

    def test_times_out_long_running_script(self):
        path = self.workspace / "slow.py"
        path.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
        result = execute_python_file(path, timeout=1)
        self.assertTrue(result.timed_out)

    def test_default_timeout_is_120_seconds(self):
        # 14日目③: pandas/openpyxlのimport+xlsx書き出しが初回30秒に収まらないことが
        # あったための延長。デフォルト値そのものを直接見て回帰を防ぐ。
        import inspect

        sig = inspect.signature(execute_python_file)
        self.assertEqual(sig.parameters["timeout"].default, 120.0)


class TestExecutePythonFileArtifacts(unittest.TestCase):
    """14日目③: 実行前後のworkspace差分から生成物(artifacts)を検出する。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_detects_newly_created_file(self):
        path = self.workspace / "make_report.py"
        path.write_text(
            "open('report.txt', 'w', encoding='utf-8').write('ok')\n", encoding="utf-8"
        )
        result = execute_python_file(path)
        self.assertEqual(result.artifacts, ["report.txt"])

    def test_script_itself_is_excluded_from_artifacts(self):
        # 生成スクリプト自身(make_report.py)は「作られたもの」ではなく足場なので
        # artifactsに混ざってはいけない(ユーザーが開くべきファイルが分からなくなる)。
        path = self.workspace / "make_report.py"
        path.write_text("pass\n", encoding="utf-8")
        result = execute_python_file(path)
        self.assertEqual(result.artifacts, [])

    def test_no_artifacts_when_script_creates_nothing(self):
        path = self.workspace / "noop.py"
        path.write_text("x = 1\n", encoding="utf-8")
        result = execute_python_file(path)
        self.assertEqual(result.artifacts, [])

    def test_artifacts_detected_even_on_timeout(self):
        # タイムアウトしてプロセスが強制終了されても、途中まで書かれたファイルが
        # あればartifactsとして拾えること(ユーザーへ「途中経過」を見せられる)。
        path = self.workspace / "slow_write.py"
        path.write_text(
            "import time\n"
            "open('partial.txt', 'w', encoding='utf-8').write('x')\n"
            "time.sleep(5)\n",
            encoding="utf-8",
        )
        result = execute_python_file(path, timeout=1)
        self.assertTrue(result.timed_out)
        self.assertIn("partial.txt", result.artifacts)


if __name__ == "__main__":
    unittest.main()
