"""txt/md Dashboard：瀏覽、搜尋、預覽並開啟 .txt / .md 檔案。

需求：Python 3.10 以上，只使用標準函式庫。支援 Windows / macOS / Linux。

用法：
    python txtmd_dashboard.py [資料夾] [--no-exclude]

    沒有指定資料夾時，掃描本程式所在的資料夾。

快捷鍵：
    Ctrl+F（macOS 為 Cmd+F）  跳到搜尋框
    ↓（在搜尋框中）            移到檔案清單
    Enter / 雙擊               用預設程式開啟檔案
    Esc                        清空搜尋；搜尋框已空時停止掃描或內容搜尋
    F5                         重新掃描
    Ctrl + / Ctrl -            放大 / 縮小預覽文字（也可用 Ctrl + 滑鼠滾輪）
    Ctrl+0                     預覽文字回到預設大小

預覽字體可按「預覽字體…」從電腦已安裝的字體中選擇，設定會保存到下次開啟。
"""

import argparse
import codecs
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

EXTS = frozenset({".txt", ".md"})

# 掃描時略過的資料夾名稱（不分大小寫）。可用 --no-exclude 關閉。
EXCLUDED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn",
        "node_modules", "__pycache__",
        ".venv", "venv", ".tox",
        ".mypy_cache", ".pytest_cache", ".ruff_cache",
        ".idea",
        "$recycle.bin", "system volume information",
    }
)

PREVIEW_MAX_CHARS = 60_000
PREVIEW_MAX_BYTES = PREVIEW_MAX_CHARS * 4   # 每字最多 4 bytes，足以讀滿 6 萬字
DETECT_BYTES = 64 * 1024                    # 判斷編碼時看的開頭位元組數
SEARCH_CHUNK_BYTES = 1024 * 1024            # 大檔案逐段搜尋，每段 1 MB
CACHE_FILE_MAX_BYTES = 2 * 1024 * 1024      # 不超過此大小的檔案，內容會放進快取
CACHE_TOTAL_MAX_CHARS = 32 * 1024 * 1024    # 快取總字數上限（約 64–128 MB 記憶體）
MIN_CONTENT_QUERY_LEN = 2
MAX_HIGHLIGHTS = 2_000

FILTER_DEBOUNCE_MS = 250    # 停止打字多久後才開始篩選
POLL_MS = 50                # 主執行緒檢查背景工作訊息的間隔
PROGRESS_INTERVAL_S = 0.2   # 背景工作回報進度的間隔

ROOT_DIR_LABEL = "（根目錄）"

FONT_SIZE_MIN = 6
FONT_SIZE_MAX = 72
FONT_SAMPLE_TEXT = (
    "中文範例：本週會議討論預算與人事安排。\n"
    "简体范例：会议记录与测试编码。\n"
    "The quick brown fox jumps over the lazy dog.\n"
    "0123456789  Il1| O0o  {}[]() <= != -> ...\n"
)

# 下拉選單顯示名稱 -> Python codec 名稱；None 表示自動偵測。
ENCODING_OPTIONS: dict[str, str | None] = {
    "自動偵測": None,
    "UTF-8": "utf-8-sig",
    "Big5 (cp950)": "cp950",
    "Big5-HKSCS": "big5hkscs",
    "GB18030 / GBK": "gb18030",
    "UTF-16": "utf-16",
    "Shift_JIS": "shift_jis",
    "EUC-KR": "euc-kr",
    "西歐 (cp1252)": "cp1252",
}
CODEC_LABELS = {codec: label for label, codec in ENCODING_OPTIONS.items() if codec}
CODEC_LABELS.update({"utf-16-le": "UTF-16 LE", "utf-16-be": "UTF-16 BE", "utf-32": "UTF-32"})

# 自動偵測：先試 UTF-8，不是的話再從這些中文編碼裡挑（同分時優先 Big5）。
LEGACY_CANDIDATES = ("cp950", "big5hkscs", "gb18030")
AUTO_CANDIDATES = ("utf-8-sig",) + LEGACY_CANDIDATES
SCORE_BYTES = 16 * 1024     # 計算常用字比例時取樣的位元組數

# 繁簡常用字與全形標點。Big5 與 GBK 的位元組範圍高度重疊，同一段資料常常
# 兩種都能「成功」解碼；用錯編碼時會變成一堆罕用字，所以看哪一種解出的常用字較多。
_COMMON_CJK = frozenset(
    "的一是不了在人有我他這個們中來上大為和國地到以說時要就出會可也你對生能而子那得於著下自之年過"
    "發後作裡用道行所然家種事成方多經麼去法學如都同現當沒動面起看定天分還進好小部其些主樣理心她本前"
    "開但因只從想實日軍者意無力它與長把機十民第公此已工使情明性知全三又關點正業外將兩高間由問很最重"
    "並物手應戰向頭文體政美相見被利什二等產或新己制身果加西斯月話合回特代內信表化老給世位次度門任常"
    "先海通教兒原東聲提立及比員解水名真論處走義各入幾口認條平系氣題活爾更別打女變四神總何電數安少報"
    "才結反受目太量再感建務做接必場件計管期市直德資命山金指克許統區保至隊形社便空決治展馬科司五基眼"
    "書非則聽白卻界達光放強即像難且權思王象完設式色路記南品住告類求據程北邊死張該交規萬取拉格望覺術"
    "領共確傳師觀清今切院讓識候帶導爭運笑飛風步改收根造言聯持組每濟車親極林服快辦議往元英士證近失轉"
    "夫令準布始怎呢存未遠叫台單影具羅字愛擊流備兵連調深商算質團集百需價花黨華城石級整府離況請技際約"
    "示復病息究線似官火斷精滿支視消越器容照須九增研寫稱企八功嗎包片史委乎查輕易早曾除農找裝廣顯吧阿"
    "李標談吃圖念六引歷首醫局突專費號盡另周較注語僅考落青隨選列武紅響雖推勢參希古眾構房半節土投某案"
    "黑維革劃敵致陳律足態護七興派孩驗責營星夠章音跟志底站嚴巴例防族供效續施留講型料終答緊黃絕奇察母"
    "京段依批群項故按河米圍江織害雙境客紀採舉殺攻父蘇密低朝友訴止細願千值仍男錢破網熱助倒育屬坐帝限"
    "船臉職速刻樂否剛威毛狀率甚獨球般普怕彈校苦創假久錯承印晚蘭試股拿腦預誰益陽若哪微尼繼送急血驚素"
    "藥適波夜省初喜衛源食險待述陸習置居勞財環排福納歡雷警獲模充負雲停木游龍樹疑層冷洲衝射略範竟句室"
    "異激漢村哈策演簡卡罪判擔州靜退既衣您宗積餘痛檢差富靈協角配征修皮揮勝降階審沉堅善媽劉讀啊超免壓"
    "銀買皇養懷執副亂抗犯追幫宣佛歲航優怪香田鐵控稅左右份穿藝背陣草腳概惡塊頓敢守酒島托央戶烈洋哥索"
    "胡款靠評版寶座釋景顧弟登貨互付伯慢歐換聞危忙核暗姐介壞討麗良序升監臨亮露永呼味野架域沙掉括魚雜"
    "誤湯絲額兄紙圓刀鬥錄紀會議測試編碼檔案資料夾內容搜尋預覽開啟"
    "这个们来为国说时会对过发后里么经开动实现样两长与进关点业间问机给几种声当还没从见变区边应战头队"
    "说员议体电数报写记论设设计证务务联导讨识论语读谈话请让讲词试诉调还进运选递远连适达过边华气汉"
    "们书买卖产亲亿仅从众优伤价众传伟体处备复头夹夺奋妇娱学宁宝实审宪宫对寻导将尔尘尝层岁岛岭币师帮"
    "带干并广庆库应废开异弃张弹归当录彻径忆忧怀态总恶悬惊惯战户扑执扩扫扬扰抚抢护报担拟拥择挂挡挤挥"
    "损换据掷搅携摄摆摇摊撑数无旧时显晋晒暂术机杀杂权条来杨极构枪柜标栏树样桥档梦检楼欢欧歼毁气汇汉"
    "沟没沪泪泽洁洒浅测济浏浑浓涂涛润涨渐渔游湾湿满滚滞灭灯灵灾炉点炼烂热焕爱爷牵犹状独狭猎献环现"
    "电画畅疗疯痒盖盘监睁码确础离种积称稳穷窃窍竞笔笼筑筹签简粮紧红纤约级纪纯纲纳纵纷纸线练组细织终"
    "经结绕绘给络绝统继绩续维综绿缓编缘缩网罗罚罢职联聪肃肠肤肿胁胆胜脉脏脑舰艺节芦苏苹范荐药莲获营"
    "萝蓝虑虽虾蚀补表视览觉触计订认讨让训议讯记讲许论设访证评识诉诊词译试诗诚话诞询该详语误说请诸"
    "读课谁调谈谊谋谓谢谱贝负贡财责贤败货质贩贪贫购贯贵费贴贸贺贼资赏赔赖赚赛赞赠赵赶趋跃践车轨轮软"
    "轻载较辅辆辈辉输辞边达迁过迈运还这进远违连迟适选递逻遗邓邮邻郑酱释针钟钢钱钻铁铃铜铭银铺链销锁"
    "锅锋错锻镇镜长门闪闭问闯闲间闹闻阀阅队阳阴阵阶际陆陈险随隐难雾静须顶项顺须顾顿预领频题颜额风飞"
    "饭饮饰饱馆马驱驶驻驾验骑骗骨鱼鲜鸟鸡鸣麦黄齐龄龙"
    "，。、；：？！「」『』（）《》〈〉…—　·“”‘’"
)


# ---------------------------------------------------------------------------
# 編碼偵測與讀檔
# ---------------------------------------------------------------------------

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),   # 必須在 UTF-16 LE 之前檢查（前兩個 byte 相同）
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _decodes_strictly(data: bytes, codec: str, final: bool) -> bool:
    # 用 incremental decoder：final=False 時，結尾被截斷的半個字不算錯誤。
    decoder = codecs.getincrementaldecoder(codec)(errors="strict")
    try:
        decoder.decode(data, final=final)
    except UnicodeDecodeError:
        return False
    return True


def _guess_bomless_utf16(data: bytes) -> str | None:
    """沒有 BOM 的 UTF-16：英數字元的另一半 byte 會是 0。"""
    sample = data[:4096]
    half = len(sample) // 2
    if half < 2:
        return None
    even_nul = sample[0::2].count(0)
    odd_nul = sample[1::2].count(0)
    if odd_nul > half * 0.3 and even_nul < half * 0.05:
        return "utf-16-le"
    if even_nul > half * 0.3 and odd_nul < half * 0.05:
        return "utf-16-be"
    return None


def _common_char_ratio(text: str) -> float:
    non_ascii = [ch for ch in text if ch > "\x7f"]
    if not non_ascii:
        return 0.0
    return sum(ch in _COMMON_CJK for ch in non_ascii) / len(non_ascii)


def detect_encoding(head: bytes, at_eof: bool, choice: str | None = None) -> str:
    """回傳用來解碼的 codec 名稱。

    head 是檔案開頭的位元組；at_eof 表示 head 是否已是整個檔案。
    choice 為使用者指定的 codec，None 代表自動偵測。
    """
    if choice:
        return choice
    for bom, codec in _BOMS:
        if head.startswith(bom):
            return codec
    utf16 = _guess_bomless_utf16(head)
    if utf16:
        return utf16
    if _decodes_strictly(head, "utf-8-sig", final=at_eof):
        return "utf-8-sig"
    valid = [codec for codec in LEGACY_CANDIDATES if _decodes_strictly(head, codec, final=at_eof)]
    if len(valid) == 1:
        return valid[0]
    if valid:
        sample = head[:SCORE_BYTES]
        # max() 遇到同分時回傳第一個，也就是較優先的編碼。
        return max(valid, key=lambda codec: _common_char_ratio(decode_bytes(sample, codec, final=False)))
    # 每種都有錯誤時，選亂碼最少的。
    return min(
        AUTO_CANDIDATES,
        key=lambda codec: head.decode(codec, errors="replace").count("�"),
    )


def codec_label(codec: str) -> str:
    return CODEC_LABELS.get(codec, codec)


def decode_bytes(data: bytes, codec: str, final: bool) -> str:
    return codecs.getincrementaldecoder(codec)(errors="replace").decode(data, final=final)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class Preview:
    text: str
    codec: str
    truncated: bool


def read_preview(path: Path, choice: str | None = None) -> Preview:
    """只讀取檔案開頭足夠預覽的部分，不會把大檔案整個載入記憶體。"""
    with path.open("rb") as fh:
        data = fh.read(PREVIEW_MAX_BYTES)
        at_eof = len(data) < PREVIEW_MAX_BYTES or not fh.read(1)
    codec = detect_encoding(data[:DETECT_BYTES], at_eof and len(data) <= DETECT_BYTES, choice)
    text = normalize_newlines(decode_bytes(data, codec, final=at_eof))
    truncated = not at_eof or len(text) > PREVIEW_MAX_CHARS
    return Preview(text[:PREVIEW_MAX_CHARS], codec, truncated)


# ---------------------------------------------------------------------------
# 檔案清單
# ---------------------------------------------------------------------------

def fmt_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def fmt_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


@dataclass(frozen=True, slots=True)
class FileItem:
    path: Path
    name: str
    rel_dir: str        # 相對於根目錄的資料夾；根目錄本身為 ""
    mtime: float
    size: int
    mtime_text: str
    size_text: str
    # 以下為預先算好的比對 / 排序用字串
    name_key: str
    dir_key: str
    rel_key: str        # 相對路徑（含檔名，分隔符號統一為 /），搜尋時比對這個

    @classmethod
    def from_entry(cls, root_prefix: str, entry_path: str, name: str, st: os.stat_result) -> "FileItem":
        rel = entry_path[len(root_prefix):]
        rel_dir = os.path.dirname(rel)
        return cls(
            path=Path(entry_path),
            name=name,
            rel_dir=rel_dir,
            mtime=st.st_mtime,
            size=st.st_size,
            mtime_text=fmt_time(st.st_mtime),
            size_text=fmt_size(st.st_size),
            name_key=name.casefold(),
            dir_key=rel_dir.casefold(),
            rel_key=rel.replace("\\", "/").casefold(),
        )


def scan_files(
    root: Path,
    cancel: threading.Event,
    on_progress: Callable[[int, int], None] | None = None,
    excluded_dirs: frozenset[str] = EXCLUDED_DIRS,
) -> list[FileItem] | None:
    """走訪 root 下所有 txt/md 檔，依修改時間新到舊排序。被取消時回傳 None。"""
    root_prefix = os.path.join(str(root), "")   # 確保結尾剛好一個分隔符號
    items: list[FileItem] = []
    pending = [str(root)]
    dirs_seen = 0
    last_report = time.monotonic()

    while pending:
        if cancel.is_set():
            return None
        current = pending.pop()
        dirs_seen += 1
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            # 不進入符號連結 / Windows junction，避免無限迴圈。
                            is_junction = getattr(entry, "is_junction", None)
                            if is_junction and is_junction():
                                continue
                            if entry.name.casefold() not in excluded_dirs:
                                pending.append(entry.path)
                        elif os.path.splitext(entry.name)[1].lower() in EXTS and entry.is_file():
                            items.append(
                                FileItem.from_entry(root_prefix, entry.path, entry.name, entry.stat())
                            )
                    except OSError:
                        continue
        except OSError:
            continue    # 沒有權限等情況，略過這個資料夾

        if on_progress and time.monotonic() - last_report >= PROGRESS_INTERVAL_S:
            on_progress(len(items), dirs_seen)
            last_report = time.monotonic()

    items.sort(key=lambda item: item.mtime, reverse=True)
    return items


# ---------------------------------------------------------------------------
# 內容搜尋
# ---------------------------------------------------------------------------

class ContentSearcher:
    """判斷檔案內容是否包含所有關鍵字（不分大小寫）。

    小檔案的內容會放進 LRU 快取，重複搜尋時不必再讀檔；
    大檔案則逐段串流搜尋，記憶體用量固定。可跨執行緒使用。
    """

    def __init__(self) -> None:
        self._cache: OrderedDict[tuple, str] = OrderedDict()
        self._cache_chars = 0
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_chars = 0

    def contains_all(
        self,
        item: FileItem,
        needles: tuple[str, ...],
        choice: str | None,
        cancel: threading.Event,
    ) -> bool:
        """needles 必須已經 casefold。"""
        key = (item.path, item.mtime, item.size, choice)
        text = self._cache_get(key)
        if text is None and item.size <= CACHE_FILE_MAX_BYTES:
            text = self._load_small(item.path, choice)
            if text is not None:
                self._cache_put(key, text)
        if text is not None:
            return all(needle in text for needle in needles)
        return self._stream_contains_all(item.path, needles, choice, cancel)

    def _cache_get(self, key: tuple) -> str | None:
        with self._lock:
            text = self._cache.get(key)
            if text is not None:
                self._cache.move_to_end(key)
            return text

    def _cache_put(self, key: tuple, text: str) -> None:
        with self._lock:
            if key in self._cache:
                return
            self._cache[key] = text
            self._cache_chars += len(text)
            while self._cache_chars > CACHE_TOTAL_MAX_CHARS and self._cache:
                _, old = self._cache.popitem(last=False)
                self._cache_chars -= len(old)

    @staticmethod
    def _load_small(path: Path, choice: str | None) -> str | None:
        try:
            with path.open("rb") as fh:
                data = fh.read(CACHE_FILE_MAX_BYTES + 1)
        except OSError:
            return None
        if len(data) > CACHE_FILE_MAX_BYTES:   # 掃描後檔案變大了，改走串流
            return None
        codec = detect_encoding(data[:DETECT_BYTES], len(data) <= DETECT_BYTES, choice)
        return decode_bytes(data, codec, final=True).casefold()

    @staticmethod
    def _stream_contains_all(
        path: Path,
        needles: tuple[str, ...],
        choice: str | None,
        cancel: threading.Event,
    ) -> bool:
        remaining = set(needles)
        overlap = max(map(len, remaining)) - 1   # 避免關鍵字剛好被切在兩段之間
        try:
            with path.open("rb") as fh:
                chunk = fh.read(SEARCH_CHUNK_BYTES)
                codec = detect_encoding(chunk[:DETECT_BYTES], len(chunk) <= DETECT_BYTES, choice)
                decoder = codecs.getincrementaldecoder(codec)(errors="replace")
                tail = ""
                while True:
                    if cancel.is_set():
                        return False
                    final = not chunk
                    text = tail + decoder.decode(chunk, final=final).casefold()
                    remaining = {needle for needle in remaining if needle not in text}
                    if not remaining:
                        return True
                    if final:
                        return False
                    tail = text[-overlap:] if overlap else ""
                    chunk = fh.read(SEARCH_CHUNK_BYTES)
        except OSError:
            return False


# ---------------------------------------------------------------------------
# 跨平台開啟檔案
# ---------------------------------------------------------------------------

def open_file(path: Path) -> None:
    """用系統預設程式開啟檔案。失敗時丟出 OSError。"""
    if sys.platform == "win32":
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["notepad.exe", str(path)])
    elif sys.platform == "darwin":
        result = subprocess.run(["open", str(path)], capture_output=True)
        if result.returncode != 0:   # 沒有對應的程式時，改用預設文字編輯器
            subprocess.Popen(["open", "-t", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_in_file_manager(path: Path) -> None:
    """在檔案總管 / Finder 中顯示檔案。失敗時丟出 OSError。"""
    if sys.platform == "win32":
        # explorer 的 /select 參數需要這種寫法。沒有經過 shell，
        # 且 Windows 檔名不能含雙引號，所以沒有注入風險。
        subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


# ---------------------------------------------------------------------------
# 使用者設定（存成 JSON，下次開啟時套用）
# ---------------------------------------------------------------------------

def settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "txtmd_dashboard" / "settings.json"


def load_settings() -> dict:
    """讀取設定；檔案不存在或內容損壞時回傳空設定。"""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict) -> None:
    """先寫入暫存檔再替換，避免寫到一半當機造成設定檔損壞。失敗時丟出 OSError。"""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 字體
# ---------------------------------------------------------------------------

def installed_font_families(widget: tk.Misc) -> list[str]:
    """電腦上已安裝的字體名稱（去除重複，依名稱排序）。

    Windows 上以 @ 開頭的是直書用字體，不適合預覽，所以排除。
    """
    names = {name for name in tkfont.families(widget) if name and not name.startswith("@")}
    return sorted(names, key=str.casefold)


def clamp_font_size(size: int) -> int:
    return max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, size))


class FontDialog(tk.Toplevel):
    """選擇預覽字體的對話框。

    直接修改傳入的 Font 物件，所以預覽區會即時變化；按「取消」時改回原本的字體。
    關閉後 result 為 (字體, 大小)，取消則為 None。
    """

    _monospace_cache: frozenset[str] | None = None   # 整個程式共用，只計算一次

    def __init__(self, master: tk.Misc, font: tkfont.Font, default: tuple[str, int]) -> None:
        super().__init__(master)
        self.font = font
        self.default = default
        self.original = (str(font.cget("family")), int(font.cget("size")))
        self.result: tuple[str, int] | None = None
        self.families = installed_font_families(self)
        self.visible: list[str] = []

        self.filter_text = tk.StringVar()
        self.mono_only = tk.BooleanVar(value=False)
        self.size_var = tk.StringVar(value=str(self.original[1]))

        self.title("預覽字體")
        self.transient(master)
        self.minsize(560, 380)
        self._build()
        self._refill()

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())
        self.bind("<Return>", lambda _e: self.ok())
        self._center_over(master)
        self.filter_entry.focus_set()
        try:   # 設為強制回應視窗；部分視窗管理員需要等視窗顯示後才能 grab
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass

    def _build(self) -> None:
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        top = ttk.Frame(body)
        top.pack(fill="x")
        ttk.Label(top, text="搜尋字體:").pack(side="left")
        self.filter_entry = ttk.Entry(top, textvariable=self.filter_text)
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Checkbutton(
            top, text="只顯示等寬字體", variable=self.mono_only, command=self._refill
        ).pack(side="left")

        middle = ttk.Frame(body)
        middle.pack(fill="both", expand=True, pady=8)

        list_frame = ttk.Frame(middle)
        list_frame.pack(side="left", fill="y")
        self.listbox = tk.Listbox(list_frame, width=30, height=16, exportselection=False)
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.pack(side="left", fill="y")
        list_scroll.pack(side="left", fill="y")

        right = ttk.Frame(middle)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        size_row = ttk.Frame(right)
        size_row.pack(fill="x")
        ttk.Label(size_row, text="大小:").pack(side="left")
        self.size_box = ttk.Spinbox(
            size_row,
            from_=FONT_SIZE_MIN,
            to=FONT_SIZE_MAX,
            textvariable=self.size_var,
            width=5,
            command=self._apply_size,
        )
        self.size_box.pack(side="left", padx=(6, 4))
        ttk.Label(size_row, text="pt").pack(side="left")
        self.current_label = ttk.Label(right, foreground="#666666")
        self.current_label.pack(fill="x", pady=(8, 4))

        self.sample = tk.Text(right, width=44, height=8, wrap="word", font=self.font)
        self.sample.insert("1.0", FONT_SAMPLE_TEXT)
        self.sample.configure(state="disabled")
        self.sample.pack(fill="both", expand=True)

        bottom = ttk.Frame(body)
        bottom.pack(fill="x")
        self.count_label = ttk.Label(bottom, foreground="#666666")
        self.count_label.pack(side="left")
        ttk.Button(bottom, text="確定", command=self.ok).pack(side="right")
        ttk.Button(bottom, text="取消", command=self.cancel).pack(side="right", padx=8)
        ttk.Button(bottom, text="還原預設值", command=self.reset).pack(side="right")

        self.filter_text.trace_add("write", lambda *_: self._refill())
        self.filter_entry.bind("<Down>", self._focus_list)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._apply_family())
        self.listbox.bind("<Double-1>", lambda _e: self.ok())
        for sequence in ("<KeyRelease>", "<FocusOut>"):
            self.size_box.bind(sequence, lambda _e: self._apply_size())

    def _center_over(self, master: tk.Misc) -> None:
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _monospace_families(self) -> frozenset[str]:
        if FontDialog._monospace_cache is None:
            self.configure(cursor="watch")
            self.update_idletasks()
            FontDialog._monospace_cache = frozenset(
                name for name in self.families
                # 用字體描述（不建立具名字體）查詢 -fixed，速度較快
                if int(self.tk.call("font", "metrics", (name, 12), "-fixed"))
            )
            self.configure(cursor="")
        return FontDialog._monospace_cache

    def _refill(self) -> None:
        names = self.families
        if self.mono_only.get():
            monospace = self._monospace_families()
            names = [name for name in names if name in monospace]
        needle = self.filter_text.get().strip().casefold()
        if needle:
            names = [name for name in names if needle in name.casefold()]
        self.visible = names
        self.listbox.delete(0, "end")
        if names:
            self.listbox.insert("end", *names)
        self.count_label.configure(text=f"顯示 {len(names):,} / {len(self.families):,} 個字體")
        self._select_current()

    def _select_current(self) -> None:
        family = str(self.font.cget("family"))
        self.current_label.configure(text=f"目前字體: {family}")
        self.listbox.selection_clear(0, "end")
        if family in self.visible:
            index = self.visible.index(family)
            self.listbox.selection_set(index)
            self.listbox.activate(index)
            self.listbox.see(index)

    def _focus_list(self, _event=None) -> str:
        if self.visible:
            self.listbox.focus_set()
            if not self.listbox.curselection():
                self.listbox.selection_set(0)
                self.listbox.activate(0)
                self._apply_family()
        return "break"

    def _apply_family(self) -> None:
        selection = self.listbox.curselection()
        if selection:
            self.font.configure(family=self.visible[selection[0]])
            self.current_label.configure(text=f"目前字體: {self.visible[selection[0]]}")

    def _apply_size(self) -> int:
        try:
            size = clamp_font_size(int(self.size_var.get()))
        except ValueError:   # 正在輸入（例如欄位暫時是空的）時先不套用
            return int(self.font.cget("size"))
        self.font.configure(size=size)
        return size

    def reset(self) -> None:
        family, size = self.default
        self.font.configure(family=family, size=size)
        self.size_var.set(str(size))
        self.mono_only.set(False)
        self.filter_text.set("")   # 會觸發 _refill 並選取目前字體

    def ok(self) -> None:
        size = self._apply_size()
        self.size_var.set(str(size))
        self.result = (str(self.font.cget("family")), size)
        self.destroy()

    def cancel(self) -> None:
        family, size = self.original
        self.font.configure(family=family, size=size)
        self.result = None
        self.destroy()


# ---------------------------------------------------------------------------
# 介面
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    id: str
    label: str
    width: int
    anchor: str
    descending_first: bool   # 第一次點標題時是否由大到小


COLUMNS = (
    Column("name", "檔名", 260, "w", False),
    Column("mtime", "最後修改", 135, "w", True),
    Column("size", "大小", 80, "e", True),
    Column("dir", "資料夾", 360, "w", False),
)
COLUMN_BY_ID = {column.id: column for column in COLUMNS}

SORT_KEYS: dict[str, Callable[[FileItem], tuple]] = {
    "name": lambda item: (item.name_key, item.rel_key),
    "mtime": lambda item: (item.mtime, item.rel_key),
    "size": lambda item: (item.size, item.rel_key),
    "dir": lambda item: (item.dir_key, item.name_key),
}


class App(tk.Tk):
    def __init__(self, root_path: Path, excluded_dirs: frozenset[str] = EXCLUDED_DIRS) -> None:
        super().__init__()
        self.root_path = root_path
        self.excluded_dirs = excluded_dirs
        self.all_items: list[FileItem] = []
        self.filtered: list[FileItem] = []      # 永遠和畫面上的順序一致
        self.sort_column = "mtime"
        self.sort_reverse = True
        self.searcher = ContentSearcher()
        self.settings = load_settings()

        self._row_items: dict[str, FileItem] = {}
        self._next_iid = 0
        self._preview_path: Path | None = None

        # 背景工作：worker 執行緒只把訊息放進佇列，所有 Tk 操作都在主執行緒進行。
        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self._scan_job = 0
        self._scan_cancel = threading.Event()
        self._search_job = 0
        self._search_cancel = threading.Event()
        self._search_progress = (0, 0)
        self._scanning = False
        self._searching = False
        self._filter_after: str | None = None
        self._progress_shown = False

        self.geometry("1200x700")
        self.minsize(850, 500)

        self.query = tk.StringVar()
        self.content_search = tk.BooleanVar(value=False)
        self.encoding_name = tk.StringVar(value=next(iter(ENCODING_OPTIONS)))
        self.root_text = tk.StringVar()
        self.status = tk.StringVar(value="準備中...")

        self._build_ui()
        self._bind_events()
        self._update_headings()
        self._update_root_display()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_after = self.after(POLL_MS, self._poll_events)
        self.reload()

    # ----- 建立介面 ---------------------------------------------------------

    def _build_ui(self) -> None:
        if sys.platform not in ("win32", "darwin") and "clam" in ttk.Style(self).theme_names():
            ttk.Style(self).theme_use("clam")

        folder_row = ttk.Frame(self)
        folder_row.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(folder_row, text="資料夾:").pack(side="left")
        # 右側按鈕先 pack，路徑太長時才不會被擠掉。
        self.stop_button = ttk.Button(folder_row, text="停止", command=self.stop_jobs, state="disabled")
        self.stop_button.pack(side="right", padx=(8, 0))
        ttk.Button(folder_row, text="重新掃描 (F5)", command=self.reload).pack(side="right", padx=(8, 0))
        ttk.Button(folder_row, text="選擇資料夾", command=self.choose_folder).pack(side="right")
        ttk.Label(folder_row, textvariable=self.root_text).pack(side="left", fill="x", expand=True, padx=(6, 12))

        search_row = ttk.Frame(self)
        search_row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(search_row, text="搜尋:").pack(side="left")
        self.query_entry = ttk.Entry(search_row, textvariable=self.query)
        self.query_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Checkbutton(
            search_row, text="也搜內容", variable=self.content_search, command=self.run_filter
        ).pack(side="left")
        ttk.Label(search_row, text="編碼:").pack(side="left", padx=(12, 4))
        self.encoding_box = ttk.Combobox(
            search_row,
            textvariable=self.encoding_name,
            values=list(ENCODING_OPTIONS),
            state="readonly",
            width=15,
        )
        self.encoding_box.pack(side="left")
        ttk.Button(search_row, text="預覽字體…", command=self.choose_preview_font).pack(
            side="left", padx=(8, 0)
        )

        # 狀態列用 side="bottom" 先 pack，視窗縮小時才不會被擠出畫面。
        status_bar = ttk.Frame(self)
        status_bar.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        ttk.Label(status_bar, textvariable=self.status).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(status_bar, length=180, maximum=100)

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=3)
        paned.add(right, weight=4)

        self.tree = ttk.Treeview(
            left, columns=[c.id for c in COLUMNS], show="headings", selectmode="browse"
        )
        for column in COLUMNS:
            self.tree.heading(column.id, command=lambda cid=column.id: self.toggle_sort(cid))
            self.tree.column(column.id, width=column.width, anchor=column.anchor)
        yscroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        # 預覽區設為唯讀（仍可選取、複製文字）。
        self.preview_font = self._create_preview_font()
        self.preview = tk.Text(
            right, wrap="word", undo=False, state="disabled", font=self.preview_font
        )
        self.preview.tag_configure("header", foreground="#666666")
        self.preview.tag_configure("warning", foreground="#b35900")
        self.preview.tag_configure("match", background="#ffe066", foreground="#000000")
        preview_scroll = ttk.Scrollbar(right, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=preview_scroll.set)
        self.preview.grid(row=0, column=0, sticky="nsew")
        preview_scroll.grid(row=0, column=1, sticky="ns")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="開啟", command=self.open_selected)
        self.menu.add_command(label="開啟所在資料夾", command=self.reveal_selected)
        self.menu.add_command(label="複製完整路徑", command=self.copy_selected_path)

    def _bind_events(self) -> None:
        self.query.trace_add("write", lambda *_: self.schedule_filter())
        self.encoding_box.bind("<<ComboboxSelected>>", lambda _e: self._on_encoding_changed())

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda _e: self.open_selected())
        menu_buttons = ["<Button-3>"]
        if sys.platform == "darwin":
            menu_buttons += ["<Button-2>", "<Control-Button-1>"]
        for sequence in menu_buttons:
            self.tree.bind(sequence, self._show_context_menu)

        self.query_entry.bind("<Return>", lambda _e: self.run_filter())
        self.query_entry.bind("<Escape>", self._on_entry_escape)
        self.query_entry.bind("<Down>", self._focus_tree)

        find_key = "<Command-f>" if sys.platform == "darwin" else "<Control-f>"
        self.bind_all(find_key, self._focus_search)
        self.bind_all("<F5>", lambda _e: self.reload())
        self.bind("<Escape>", lambda _e: self.stop_jobs())

        # 預覽文字縮放：Ctrl（macOS 為 Cmd）+ / - / 0，或按住 Ctrl 捲動滑鼠滾輪。
        mod = "Command" if sys.platform == "darwin" else "Control"
        for key in ("plus", "equal", "KP_Add"):
            self.bind_all(f"<{mod}-{key}>", lambda _e: self._zoom_preview(+1))
        for key in ("minus", "KP_Subtract"):
            self.bind_all(f"<{mod}-{key}>", lambda _e: self._zoom_preview(-1))
        self.bind_all(f"<{mod}-0>", lambda _e: self._zoom_preview(None))
        self.preview.bind(
            f"<{mod}-MouseWheel>", lambda e: self._zoom_preview(+1 if e.delta > 0 else -1)
        )
        self.preview.bind("<Control-Button-4>", lambda _e: self._zoom_preview(+1))   # Linux
        self.preview.bind("<Control-Button-5>", lambda _e: self._zoom_preview(-1))
        self.query_entry.focus_set()

    # ----- 資料夾與掃描 -----------------------------------------------------

    def _update_root_display(self) -> None:
        self.root_text.set(str(self.root_path))
        self.title(f"txt/md Dashboard - {self.root_path.name or self.root_path}")

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self.root_path), mustexist=True)
        if not selected:
            return
        self.root_path = Path(selected)
        self._update_root_display()
        # 換資料夾時先清掉舊清單，避免誤以為舊資料屬於新資料夾。
        self.all_items = []
        self.filtered = []
        self.searcher.clear()
        self._render()
        self.reload()

    def reload(self) -> None:
        self._cancel_scan()
        self._cancel_search()
        if not self.root_path.is_dir():
            self.all_items = []
            self.filtered = []
            self._render()
            self.status.set(f"資料夾不存在: {self.root_path}")
            messagebox.showerror("資料夾不存在", f"找不到資料夾:\n{self.root_path}")
            return

        self._scan_job += 1
        job = self._scan_job
        cancel = self._scan_cancel = threading.Event()
        self._scanning = True
        self._set_busy()
        self.status.set("掃描檔案中...（舊清單仍可使用）" if self.all_items else "掃描檔案中...")
        threading.Thread(
            target=self._scan_worker, args=(job, self.root_path, cancel), daemon=True
        ).start()

    def _scan_worker(self, job: int, root: Path, cancel: threading.Event) -> None:
        try:
            items = scan_files(
                root,
                cancel,
                lambda files, dirs: self._post("scan_progress", job, (files, dirs)),
                self.excluded_dirs,
            )
        except Exception as exc:  # noqa: BLE001 - 回報給使用者
            self._post("scan_error", job, exc)
            return
        if items is not None:
            self._post("scan_done", job, items)

    def _cancel_scan(self) -> None:
        self._scan_cancel.set()
        self._scan_job += 1      # 讓還在路上的舊訊息全部失效
        self._scanning = False
        self._set_busy()

    # ----- 篩選與內容搜尋 ---------------------------------------------------

    def _query_tokens(self) -> tuple[str, ...]:
        """以空白分隔的多個關鍵字（去除重複），全部符合才算命中。"""
        return tuple(dict.fromkeys(self.query.get().casefold().split()))

    def _content_search_wanted(self) -> bool:
        return self.content_search.get() and len(self.query.get().strip()) >= MIN_CONTENT_QUERY_LEN

    def _encoding_choice(self) -> str | None:
        return ENCODING_OPTIONS.get(self.encoding_name.get())

    def schedule_filter(self) -> None:
        """打字時延遲執行，避免每按一個鍵就重新篩選。"""
        if self._filter_after is not None:
            self.after_cancel(self._filter_after)
        self._filter_after = self.after(FILTER_DEBOUNCE_MS, self.run_filter)

    def run_filter(self) -> None:
        if self._filter_after is not None:
            self.after_cancel(self._filter_after)
            self._filter_after = None
        self._cancel_search()

        tokens = self._query_tokens()
        if not tokens:
            self.filtered = list(self.all_items)
        else:
            # 每個關鍵字可以出現在路徑或內容中；路徑已經全部符合的直接列出。
            want_content = self._content_search_wanted()
            path_tokens = [(token, token.replace("\\", "/")) for token in tokens]
            self.filtered = []
            candidates: list[tuple[FileItem, tuple[str, ...]]] = []
            for item in self.all_items:
                remaining = tuple(raw for raw, norm in path_tokens if norm not in item.rel_key)
                if not remaining:
                    self.filtered.append(item)
                elif want_content:
                    candidates.append((item, remaining))
            if candidates:
                self._start_content_search(candidates)

        self._render()
        self._highlight_matches()
        self._update_status()

    def _start_content_search(self, candidates: list[tuple[FileItem, tuple[str, ...]]]) -> None:
        self._search_job += 1
        job = self._search_job
        cancel = self._search_cancel = threading.Event()
        self._searching = True
        self._search_progress = (0, len(candidates))
        self._set_busy()
        threading.Thread(
            target=self._search_worker,
            args=(job, candidates, self._encoding_choice(), cancel),
            daemon=True,
        ).start()

    def _search_worker(
        self,
        job: int,
        candidates: list[tuple[FileItem, tuple[str, ...]]],
        choice: str | None,
        cancel: threading.Event,
    ) -> None:
        total = len(candidates)
        batch: list[FileItem] = []
        last_report = time.monotonic()
        for done, (item, needles) in enumerate(candidates, 1):
            if cancel.is_set():
                return
            try:
                if self.searcher.contains_all(item, needles, choice, cancel):
                    batch.append(item)
            except Exception:  # noqa: BLE001 - 單一檔案出錯不影響整體搜尋
                pass
            if time.monotonic() - last_report >= PROGRESS_INTERVAL_S:
                self._post("search_progress", job, (done, total, batch))
                batch = []
                last_report = time.monotonic()
        if not cancel.is_set():
            self._post("search_progress", job, (total, total, batch))
            self._post("search_done", job, None)

    def _cancel_search(self) -> None:
        self._search_cancel.set()
        self._search_job += 1
        self._searching = False
        self._set_busy()

    def stop_jobs(self) -> None:
        was_scanning, was_searching = self._scanning, self._searching
        self._cancel_scan()
        self._cancel_search()
        if was_scanning:
            self.status.set(f"已停止掃描。目前清單共 {len(self.all_items):,} 個檔案（可能是舊資料）。")
        elif was_searching:
            self.status.set(f"已停止內容搜尋，目前找到 {len(self.filtered):,} 個符合的檔案。")

    # ----- 背景工作訊息 -----------------------------------------------------

    def _post(self, kind: str, job: int, payload: object) -> None:
        """可從任何執行緒呼叫。"""
        self._events.put((kind, job, payload))

    def _poll_events(self) -> None:
        try:
            for _ in range(200):
                kind, job, payload = self._events.get_nowait()
                self._handle_event(kind, job, payload)
        except queue.Empty:
            pass
        finally:
            self._poll_after = self.after(POLL_MS, self._poll_events)

    def _handle_event(self, kind: str, job: int, payload) -> None:
        if kind.startswith("scan_"):
            if job != self._scan_job:
                return
            if kind == "scan_progress":
                files, dirs = payload
                self.status.set(f"掃描中... 已找到 {files:,} 個檔案（已看過 {dirs:,} 個資料夾）")
            elif kind == "scan_done":
                self._scanning = False
                self.all_items = payload
                self._set_busy()
                self.run_filter()
            elif kind == "scan_error":
                self._scanning = False
                self._set_busy()
                self.status.set(f"掃描失敗: {payload}")
            return

        if job != self._search_job:
            return
        if kind == "search_progress":
            done, total, matches = payload
            self._search_progress = (done, total)
            self._insert_rows(matches)
            self._update_status()
        elif kind == "search_done":
            self._searching = False
            self._set_busy()
            self._update_status()

    # ----- 清單顯示 ---------------------------------------------------------

    def toggle_sort(self, column: str) -> None:
        """點同一個標題切換方向；換欄位時用該欄位的預設方向。"""
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = COLUMN_BY_ID[column].descending_first
        self._update_headings()
        self._render()    # 只重新排序，不重新搜尋

    def _update_headings(self) -> None:
        for column in COLUMNS:
            arrow = ""
            if column.id == self.sort_column:
                arrow = " ▼" if self.sort_reverse else " ▲"
            self.tree.heading(column.id, text=f"{column.label}{arrow}")

    @staticmethod
    def _row_values(item: FileItem) -> tuple[str, str, str, str]:
        return (item.name, item.mtime_text, item.size_text, item.rel_dir or ROOT_DIR_LABEL)

    def _new_iid(self, item: FileItem) -> str:
        self._next_iid += 1
        iid = f"r{self._next_iid}"
        self._row_items[iid] = item
        return iid

    def _render(self) -> None:
        """依目前排序重畫整個清單，並盡量保留原本選取的檔案。"""
        self.filtered.sort(key=SORT_KEYS[self.sort_column], reverse=self.sort_reverse)
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._row_items.clear()

        selected_iid = None
        for item in self.filtered:
            iid = self._new_iid(item)
            self.tree.insert("", "end", iid=iid, values=self._row_values(item))
            if item.path == self._preview_path:
                selected_iid = iid

        if selected_iid is not None:
            self.tree.selection_set(selected_iid)
            self.tree.focus(selected_iid)
            self.tree.see(selected_iid)
        else:
            self._clear_preview()
            self.tree.yview_moveto(0)

    def _insert_rows(self, items: list[FileItem]) -> None:
        """內容搜尋的結果陸續到達時，插入到正確的排序位置，不重畫整個清單。"""
        key = SORT_KEYS[self.sort_column]
        for item in items:
            position = self._sorted_position(key(item), key)
            self.filtered.insert(position, item)
            self.tree.insert("", position, iid=self._new_iid(item), values=self._row_values(item))

    def _sorted_position(self, new_key: tuple, key: Callable[[FileItem], tuple]) -> int:
        lo, hi = 0, len(self.filtered)
        while lo < hi:
            mid = (lo + hi) // 2
            mid_key = key(self.filtered[mid])
            before = mid_key >= new_key if self.sort_reverse else mid_key <= new_key
            if before:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _update_status(self) -> None:
        message = f"共 {len(self.all_items):,} 個 txt/md 檔，符合 {len(self.filtered):,} 個。"
        query_len = len(self.query.get().strip())
        if self._searching:
            done, total = self._search_progress
            message += f" 搜尋內容中... {done:,}/{total:,}（Esc 或「停止」可中斷）"
            self.progress.configure(value=done * 100 / total if total else 0)
        elif self.content_search.get() and 0 < query_len < MIN_CONTENT_QUERY_LEN:
            message += f" 內容搜尋需輸入至少 {MIN_CONTENT_QUERY_LEN} 個字。"
        else:
            message += " 點選預覽，雙擊或 Enter 開啟，右鍵有更多選項。"
        self.status.set(message)

    def _set_busy(self) -> None:
        busy = self._scanning or self._searching
        self.stop_button.configure(state="normal" if busy else "disabled")
        if busy and not self._progress_shown:
            self.progress.pack(side="right", padx=(8, 0))
            self._progress_shown = True
        if self._scanning:
            self.progress.configure(mode="indeterminate")
            self.progress.start(15)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
        if not busy and self._progress_shown:
            self.progress.pack_forget()
            self._progress_shown = False

    # ----- 選取與預覽 -------------------------------------------------------

    def selected_item(self) -> FileItem | None:
        selection = self.tree.selection()
        return self._row_items.get(selection[0]) if selection else None

    def on_select(self, _event=None) -> None:
        item = self.selected_item()
        if item is not None and item.path != self._preview_path:
            self._show_preview(item)

    def _show_preview(self, item: FileItem) -> None:
        header = f"{item.path}\n"
        warning = ""
        try:
            preview = read_preview(item.path, self._encoding_choice())
        except OSError as exc:
            body = f"[無法讀取]\n{exc}"
        else:
            body = preview.text
            header += (
                f"編碼: {codec_label(preview.codec)}　大小: {item.size_text}"
                f"　最後修改: {item.mtime_text}\n"
            )
            if preview.truncated:
                warning = f"⚠ 檔案較大，只顯示前 {PREVIEW_MAX_CHARS:,} 字。\n"

        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("end", header, "header")
        self.preview.insert("end", warning, "warning")
        self.preview.insert("end", "\n")
        self.preview.mark_set("body", "end-1c")
        self.preview.mark_gravity("body", "left")
        self.preview.insert("end", body)
        self.preview.configure(state="disabled")
        self.preview.yview_moveto(0)
        self._preview_path = item.path
        self._highlight_matches(scroll=True)

    def _clear_preview(self) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.configure(state="disabled")
        self._preview_path = None

    def _highlight_matches(self, scroll: bool = False) -> None:
        """在預覽內文中標示搜尋關鍵字；scroll=True 時捲到第一個符合處。"""
        self.preview.tag_remove("match", "1.0", "end")
        if self._preview_path is None:
            return
        count = tk.IntVar()
        first = None
        found = 0
        for token in dict.fromkeys(self.query.get().split()):
            index = "body"
            while found < MAX_HIGHLIGHTS:
                index = self.preview.search(token, index, stopindex="end", nocase=True, count=count)
                if not index or count.get() == 0:
                    break
                end = f"{index}+{count.get()}c"
                self.preview.tag_add("match", index, end)
                if first is None or self.preview.compare(index, "<", first):
                    first = index
                found += 1
                index = end
        if scroll and first is not None:
            self.preview.see(first)

    def _on_encoding_changed(self) -> None:
        item = self.selected_item()
        if item is not None:
            self._show_preview(item)
        if self._content_search_wanted():
            self.run_filter()

    # ----- 開啟檔案與右鍵選單 -----------------------------------------------

    def _run_file_action(self, action: Callable[[Path], None], title: str) -> None:
        item = self.selected_item()
        if item is None:
            return
        if not item.path.exists():
            messagebox.showwarning(title, f"檔案已不存在，請重新掃描:\n{item.path}")
            return
        try:
            action(item.path)
        except (OSError, subprocess.SubprocessError) as exc:
            messagebox.showerror(title, f"{item.path}\n\n{exc}")

    def open_selected(self) -> None:
        self._run_file_action(open_file, "無法開啟檔案")

    def reveal_selected(self) -> None:
        self._run_file_action(reveal_in_file_manager, "無法開啟資料夾")

    def copy_selected_path(self) -> None:
        item = self.selected_item()
        if item is not None:
            self.clipboard_clear()
            self.clipboard_append(str(item.path))
            self.status.set(f"已複製路徑: {item.path}")

    def _on_double_click(self, event) -> str | None:
        # 只在資料列上雙擊才開檔；雙擊標題列不應開啟檔案。
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        self.open_selected()
        return "break"

    def _show_context_menu(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # ----- 鍵盤操作 ---------------------------------------------------------

    def _focus_search(self, _event=None) -> str:
        self.query_entry.focus_set()
        self.query_entry.select_range(0, "end")
        return "break"

    def _focus_tree(self, _event=None) -> str:
        children = self.tree.get_children()
        if children:
            target = (self.tree.selection() or children)[0]
            self.tree.focus_set()
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
        return "break"

    def _on_entry_escape(self, _event=None) -> str:
        if self.query.get():
            self.query.set("")
        else:
            self.stop_jobs()
        return "break"

    # ----- 預覽字體 ---------------------------------------------------------

    def _create_preview_font(self) -> tkfont.Font:
        """建立預覽區字體：預設為系統等寬字體，有儲存的設定就套用。"""
        fixed = tkfont.nametofont("TkFixedFont").actual()
        size = int(fixed["size"])
        if size <= 0:   # 負數代表像素，換算成點數
            size = round(-size * 72 / self.winfo_fpixels("1i"))
        self._default_font = (str(fixed["family"]), clamp_font_size(size or 10))

        family, size = self._default_font
        saved = self.settings.get("preview_font")
        if isinstance(saved, dict):
            # 設定裡的字體可能已被移除，所以要確認還在才套用。
            if isinstance(saved.get("family"), str) and saved["family"] in tkfont.families(self):
                family = saved["family"]
            if isinstance(saved.get("size"), int):
                size = clamp_font_size(saved["size"])
        return tkfont.Font(self, family=family, size=size)

    def choose_preview_font(self) -> None:
        dialog = FontDialog(self, self.preview_font, self._default_font)
        self.wait_window(dialog)
        if dialog.result is not None:
            family, size = dialog.result
            self._save_preview_font(f"預覽字體已設為 {family} {size} pt。")

    def _zoom_preview(self, delta: int | None) -> str:
        """delta 為 None 時回到預設大小。"""
        current = int(self.preview_font.cget("size"))
        size = self._default_font[1] if delta is None else clamp_font_size(current + delta)
        if size != current:
            self.preview_font.configure(size=size)
            self._save_preview_font(f"預覽文字大小: {size} pt")
        return "break"

    def _save_preview_font(self, message: str) -> None:
        self.settings["preview_font"] = {
            "family": str(self.preview_font.cget("family")),
            "size": int(self.preview_font.cget("size")),
        }
        try:
            save_settings(self.settings)
        except OSError as exc:
            message += f"（無法儲存設定: {exc}）"
        self.status.set(message)

    # ----- 其他 -------------------------------------------------------------

    def report_callback_exception(self, exc, val, tb) -> None:
        """未預期的錯誤除了印在終端機，也跳出視窗讓使用者看到。"""
        traceback.print_exception(exc, val, tb)
        messagebox.showerror("發生未預期的錯誤", f"{exc.__name__}: {val}")

    def _on_close(self) -> None:
        self._scan_cancel.set()
        self._search_cancel.set()
        # 取消排程中的計時器，避免視窗關閉後還被呼叫。
        for after_id in (self._poll_after, self._filter_after):
            if after_id is not None:
                self.after_cancel(after_id)
        self.destroy()


def program_dir() -> Path:
    """程式本身所在的資料夾。

    不用「目前工作資料夾」當預設，因為在 Windows 上雙擊或從捷徑開啟時，
    工作資料夾常常是 C:\\Windows\\System32。
    """
    if getattr(sys, "frozen", False):   # 用 PyInstaller 等打包成 .exe 時
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Browse, search, preview, and open .txt/.md files."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=str(program_dir()),
        help="Folder to scan. Defaults to the folder containing this program.",
    )
    parser.add_argument(
        "--no-exclude",
        action="store_true",
        help="Also scan folders that are skipped by default (.git, node_modules, ...).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root_path = Path(args.root).expanduser().resolve()
    excluded = frozenset() if args.no_exclude else EXCLUDED_DIRS
    App(root_path, excluded).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
