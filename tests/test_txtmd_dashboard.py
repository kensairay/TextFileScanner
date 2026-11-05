"""txtmd_dashboard 的單元測試：編碼偵測、預覽、掃描與內容搜尋。"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import txtmd_dashboard as td  # noqa: E402


def make_tree(base: Path) -> None:
    (base / "notes").mkdir()
    (base / "notes" / "sub").mkdir()
    (base / "node_modules" / "pkg").mkdir(parents=True)
    (base / ".git").mkdir()
    (base / "utf8.md").write_text("# 標題\nHello 世界 needle_utf8\n", encoding="utf-8")
    (base / "bom.txt").write_bytes(b"\xef\xbb\xbf" + "有 BOM 的檔案".encode("utf-8"))
    (base / "notes" / "big5.txt").write_bytes("繁體中文會議紀錄 needle_big5".encode("cp950"))
    (base / "notes" / "gbk.txt").write_bytes("简体中文会议记录，测试编码".encode("gb18030"))
    (base / "notes" / "u16.txt").write_bytes("UTF-16 檔案 needle16".encode("utf-16"))
    (base / "notes" / "u16le_nobom.txt").write_bytes("plain ascii text in utf16 needle_le".encode("utf-16-le"))
    (base / "notes" / "sub" / "crlf.txt").write_bytes(b"line1\r\nline2\rline3\n")
    (base / "node_modules" / "pkg" / "README.md").write_text("should be excluded")
    (base / ".git" / "x.md").write_text("excluded too")
    (base / "image.png").write_bytes(b"\x89PNG")
    (base / "UPPER.TXT").write_text("upper ext")


class EncodingTests(unittest.TestCase):
    def test_detect(self):
        cases = {
            "utf-8-sig": "中文 utf8".encode("utf-8"),
            "cp950": "繁體中文會議紀錄".encode("cp950"),
            "utf-16": "x".encode("utf-16"),
            "utf-32": "x".encode("utf-32"),
            "utf-16-le": "ascii only text".encode("utf-16-le"),
            "utf-16-be": "ascii only text".encode("utf-16-be"),
        }
        for expected, data in cases.items():
            self.assertEqual(td.detect_encoding(data, True), expected, expected)
        self.assertEqual(td.detect_encoding(b"", True), "utf-8-sig")
        # 手動指定時直接採用
        self.assertEqual(td.detect_encoding(b"abc", True, "shift_jis"), "shift_jis")

    def test_gbk_decodes_correctly(self):
        text = "简体中文会议记录，测试编码"
        codec = td.detect_encoding(text.encode("gb18030"), True)
        self.assertEqual(text.encode("gb18030").decode(codec), text)

    def test_truncated_multibyte_is_still_utf8(self):
        data = ("中" * 100).encode("utf-8")[:-1]   # 最後一個字被切一半
        self.assertEqual(td.detect_encoding(data, at_eof=False), "utf-8-sig")


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_big5_and_bom_and_newlines(self):
        p = self.base / "a.txt"
        p.write_bytes("繁體中文".encode("cp950"))
        self.assertEqual(td.read_preview(p).text, "繁體中文")
        p.write_bytes(b"\xef\xbb\xbfabc")
        self.assertEqual(td.read_preview(p).text, "abc")
        p.write_bytes(b"a\r\nb\rc")
        self.assertEqual(td.read_preview(p).text, "a\nb\nc")

    def test_truncation_flag_and_limit(self):
        p = self.base / "big.txt"
        p.write_text("字" * (td.PREVIEW_MAX_CHARS + 10), encoding="utf-8")
        pv = td.read_preview(p)
        self.assertTrue(pv.truncated)
        self.assertEqual(len(pv.text), td.PREVIEW_MAX_CHARS)
        self.assertNotIn("�", pv.text)
        p.write_text("短", encoding="utf-8")
        self.assertFalse(td.read_preview(p).truncated)


class ScanAndSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        make_tree(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_excludes_and_relpaths(self):
        items = td.scan_files(self.base, threading.Event())
        names = sorted(i.name for i in items)
        self.assertNotIn("README.md", names)
        self.assertNotIn("x.md", names)
        self.assertIn("UPPER.TXT", names)
        self.assertEqual(len(items), 8)
        crlf = next(i for i in items if i.name == "crlf.txt")
        self.assertEqual(crlf.rel_key, "notes/sub/crlf.txt")
        root_item = next(i for i in items if i.name == "utf8.md")
        self.assertEqual(root_item.rel_dir, "")
        # 根目錄名稱不應出現在比對字串中（B1）
        self.assertNotIn(self.base.name.casefold(), root_item.rel_key)

    def test_scan_no_exclude_and_cancel(self):
        items = td.scan_files(self.base, threading.Event(), excluded_dirs=frozenset())
        self.assertEqual(len(items), 10)
        cancelled = threading.Event()
        cancelled.set()
        self.assertIsNone(td.scan_files(self.base, cancelled))

    def test_content_search_all_encodings(self):
        items = {i.name: i for i in td.scan_files(self.base, threading.Event())}
        s = td.ContentSearcher()
        ev = threading.Event()
        self.assertTrue(s.contains_all(items["big5.txt"], ("needle_big5", "會議"), None, ev))
        self.assertTrue(s.contains_all(items["gbk.txt"], ("会议",), None, ev))
        self.assertTrue(s.contains_all(items["u16.txt"], ("needle16",), None, ev))
        self.assertTrue(s.contains_all(items["u16le_nobom.txt"], ("needle_le",), None, ev))
        self.assertTrue(s.contains_all(items["utf8.md"], ("hello", "世界"), None, ev))  # 不分大小寫
        self.assertFalse(s.contains_all(items["utf8.md"], ("hello", "不存在"), None, ev))

    def test_stream_search_across_chunk_boundary(self):
        old = td.SEARCH_CHUNK_BYTES, td.CACHE_FILE_MAX_BYTES, td.DETECT_BYTES
        td.SEARCH_CHUNK_BYTES, td.CACHE_FILE_MAX_BYTES, td.DETECT_BYTES = 64, 10, 16
        try:
            p = self.base / "stream.txt"
            # 關鍵字放在第 64 byte 附近（跨段），且中文字也會被切開
            content = "甲" * 20 + "ABCneedle" + "乙" * 50 + "尾巴END"
            p.write_text(content, encoding="utf-8")
            item = td.scan_files(self.base, threading.Event())
            item = next(i for i in item if i.name == "stream.txt")
            s = td.ContentSearcher()
            ev = threading.Event()
            self.assertTrue(s.contains_all(item, ("abcneedle",), None, ev))
            self.assertTrue(s.contains_all(item, ("尾巴end", "甲甲"), None, ev))
            self.assertFalse(s.contains_all(item, ("zzz",), None, ev))
            p.write_text("偏" * 100 + "needle", encoding="cp950")
            item = td.FileItem.from_entry(str(self.base) + os.sep, str(p), p.name, p.stat())
            self.assertTrue(s.contains_all(item, ("偏偏needle",), None, ev))
        finally:
            td.SEARCH_CHUNK_BYTES, td.CACHE_FILE_MAX_BYTES, td.DETECT_BYTES = old

    def test_cache_budget(self):
        old = td.CACHE_TOTAL_MAX_CHARS
        td.CACHE_TOTAL_MAX_CHARS = 30
        try:
            s = td.ContentSearcher()
            items = td.scan_files(self.base, threading.Event())
            for it in items:
                s.contains_all(it, ("x",), None, threading.Event())
            self.assertLessEqual(s._cache_chars, 30 + 40)
            self.assertEqual(s._cache_chars, sum(map(len, s._cache.values())))
        finally:
            td.CACHE_TOTAL_MAX_CHARS = old


if __name__ == "__main__":
    unittest.main(verbosity=1)
