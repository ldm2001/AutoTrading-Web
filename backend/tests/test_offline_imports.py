import ast
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))


# 파일 하나가 임포트하는 모듈 이름 (import, from … import, importlib.import_module·__import__의 문자열 인자)
def modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            fn = node.func
            label = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if label in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
    return names


# 서비스 쪽 소스 — main.py, api/, service/
def sources() -> list[Path]:
    files = [_BACKEND / "main.py"]
    for folder in ("api", "service"):
        files += sorted((_BACKEND / folder).rglob("*.py"))
    return files


# 오프라인 하네스와 서비스 코드의 임포트 경계
class BoundaryTest(unittest.TestCase):
    # main.py·api/·service/는 offline을 임포트하지 않음
    def testservice(self):
        for path in sources():
            with self.subTest(path=str(path.relative_to(_BACKEND))):
                bad = {m for m in modules(path) if m == "offline" or m.startswith("offline.")}
                self.assertEqual(bad, set())

    # offline은 service.kis·main·api를 직접 임포트하지 않음 (service 패키지 재수출 `from service import …`도 금지)
    def testoffline(self):
        files = sorted((_BACKEND / "offline").rglob("*.py"))
        self.assertGreaterEqual(len(files), 5)
        for path in files:
            with self.subTest(path=str(path.relative_to(_BACKEND))):
                bad = {m for m in modules(path)
                       if m in ("service", "main", "api") or m.startswith(("service.kis", "api."))}
                self.assertEqual(bad, set())

    # 검사기 자체가 임포트를 찾아내는지 (빈 검사로 초록이 되지 않게)
    def testscanner(self):
        self.assertGreater(len(sources()), 30)
        self.assertIn("service.ai.predict", modules(_BACKEND / "offline" / "walk.py"))
        self.assertIn("service.kis", modules(_BACKEND / "service" / "__init__.py"))


if __name__ == "__main__":
    unittest.main()
