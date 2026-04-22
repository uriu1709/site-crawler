#!/usr/bin/env python3
"""
スライドライブラリチェッカー GUI版
スライドショー系JSライブラリの使用状況をサイト全体で検出する
"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading
import requests
import csv
import json
import re
import ssl
import sys
import time
import random
import os
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from collections import deque

# ========================================
# 検出対象ライブラリ定義
#   load_re : <script src> / <link href> に含まれるパターン（読み込み検出）
#   init_re : インライン <script> 内の初期化コード（使用検出）
#   html_re : HTML クラス名等による使用の痕跡（使用検出）
#   ver_re  : URL 文字列からバージョンを抽出
# ========================================
SLIDE_LIBS = [
    {
        'name':    'Swiper',
        'load_re': re.compile(r'swiper', re.I),
        'init_re': re.compile(r'new\s+Swiper\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*swiper-(?:container|wrapper|slide)\b', re.I),
        'ver_re':  re.compile(r'swiper[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'Slick',
        'load_re': re.compile(r'slick(?:\.min)?\.(?:js|css)|jquery\.slick', re.I),
        'init_re': re.compile(r'\.slick\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*slick-(?:slider|list|track)\b', re.I),
        'ver_re':  re.compile(r'slick[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'Owl Carousel',
        'load_re': re.compile(r'owl\.carousel', re.I),
        'init_re': re.compile(r'\.owlCarousel\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*owl-(?:carousel|stage|item)\b', re.I),
        'ver_re':  re.compile(r'owl[.\-]carousel[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'Splide',
        'load_re': re.compile(r'splide', re.I),
        'init_re': re.compile(r'new\s+Splide\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*\bsplide\b', re.I),
        'ver_re':  re.compile(r'splide[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'Glide.js',
        'load_re': re.compile(r'glidejs|glide(?:\.min)?\.js|glide@\d', re.I),
        'init_re': re.compile(r'new\s+Glide\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*\bglide\b', re.I),
        'ver_re':  re.compile(r'glide[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'bxSlider',
        'load_re': re.compile(r'bxslider', re.I),
        'init_re': re.compile(r'\.bxSlider\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*bx-(?:wrapper|viewport|pager)\b', re.I),
        'ver_re':  re.compile(r'bxslider[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'Flickity',
        'load_re': re.compile(r'flickity', re.I),
        'init_re': re.compile(r'new\s+Flickity\s*\(|\.flickity\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*flickity-(?:viewport|slider)\b', re.I),
        'ver_re':  re.compile(r'flickity[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'Tiny Slider',
        'load_re': re.compile(r'tiny-?slider', re.I),
        'init_re': re.compile(r'\btns\s*\(\s*\{', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*tns-(?:outer|inner|slider)\b', re.I),
        'ver_re':  re.compile(r'tiny-?slider[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'lightSlider',
        'load_re': re.compile(r'lightslider', re.I),
        'init_re': re.compile(r'\.lightSlider\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*lS(?:Slide|slider)\b', re.I),
        'ver_re':  re.compile(r'lightslider[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
    {
        'name':    'Keen Slider',
        'load_re': re.compile(r'keen-slider', re.I),
        'init_re': re.compile(r'new\s+KeenSlider\s*\(', re.I),
        'html_re': re.compile(r'class=["\'][^"\']*\bkeen-slider\b', re.I),
        'ver_re':  re.compile(r'keen-slider[@/.\-]v?(\d+\.\d+[\.\d]*)', re.I),
    },
]

SKIP_EXTENSIONS = {
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
    '.zip', '.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt',
    '.mp4', '.mp3', '.mov', '.avi', '.wmv',
    '.css', '.js', '.ico', '.woff', '.woff2', '.ttf', '.eot',
}

# JS/CSS ファイル内のバージョン文字列を探す正規表現
# 例: /*! Swiper v8.4.5  /  version:"8.4.5"  /  e.version="8.4.5"
VERSION_IN_CONTENT_RE = re.compile(
    r'(?:version|VERSION)\s*[:=]\s*["\']v?(\d+\.\d+[\.\d]*)["\']'
    r'|/\*!?\s*\S+\s+v?(\d+\.\d+[\.\d]*)',
    re.I,
)

# ========================================
# コアロジック（クローラー共通）
# ========================================
def extract_title(html):
    from html import unescape
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    return unescape(m.group(1).strip()) if m else ''

def fetch_lib_version(url, session, timeout_sec, cache):
    """JS/CSS ファイルの先頭 8KB からバージョン文字列を取得（キャッシュ付き）"""
    if url in cache:
        return cache[url]
    try:
        resp = session.get(url, timeout=timeout_sec, stream=True)
        if resp.status_code == 200:
            chunk = b''
            for c in resp.iter_content(8192):
                chunk = c
                break
            content = chunk.decode('utf-8', errors='ignore')
            m = VERSION_IN_CONTENT_RE.search(content)
            version = next((g for g in m.groups() if g), '') if m else ''
        else:
            version = ''
    except Exception:
        version = ''
    cache[url] = version
    return version

def normalize_url(url):
    parsed = urlparse(url)
    path = parsed.path
    filename = path.split('/')[-1]
    if filename and '.' not in filename and not path.endswith('/'):
        path = path + '/'
    return parsed._replace(path=path, query='', fragment='').geturl()

def is_skip_url(url):
    path = urlparse(url).path.lower()
    filename = path.split('/')[-1]
    if '.' in filename:
        ext = '.' + filename.rsplit('.', 1)[1]
        return ext in SKIP_EXTENSIONS
    return False

class _SSLAdapter(HTTPAdapter):
    """古いサーバー（DH鍵サイズ不足等）にも接続できるよう SSL セキュリティレベルを緩和"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

def load_robots(session, base_url, timeout_sec, log_fn):
    rp = RobotFileParser()
    robots_url = base_url.rstrip('/') + '/robots.txt'
    rp.set_url(robots_url)
    try:
        resp = session.get(robots_url, timeout=timeout_sec)
        if resp.status_code in (401, 403):
            rp.disallow_all = True
        elif resp.status_code >= 400:
            rp.allow_all = True
        else:
            rp.parse(resp.text.splitlines())
            log_fn(f'robots.txt読み込み完了: {robots_url}')
    except Exception as e:
        log_fn(f'robots.txt取得失敗（robots.txt なしとして続行）: {e}')
        rp.allow_all = True
    return rp

def extract_links(html, current_url, base_domain):
    links = set()
    for href in re.findall(r'<a\s[^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        if href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
            continue
        abs_url = normalize_url(urljoin(current_url, href))
        parsed  = urlparse(abs_url)
        if parsed.netloc == base_domain and parsed.scheme in ('http', 'https'):
            if not is_skip_url(abs_url):
                links.add(abs_url)
    return links

def fetch_with_retry(session, url, timeout_sec, retry_count, retry_delay_sec, log_fn):
    last_error = None
    for attempt in range(1, retry_count + 1):
        try:
            resp = session.get(url, timeout=timeout_sec, allow_redirects=True)
            if resp.status_code in (429, 503):
                ra = resp.headers.get('Retry-After')
                wait = int(ra) if ra and ra.isdigit() else retry_delay_sec * (2 ** (attempt - 1))
                wait = min(wait, 120)
                if attempt < retry_count:
                    log_fn(f'  HTTP {resp.status_code} (試行{attempt}/{retry_count}) — {wait}秒後リトライ')
                    time.sleep(wait)
                    continue
            return resp, None
        except requests.Timeout:
            last_error = 'TIMEOUT'
            wait = min(retry_delay_sec * (2 ** (attempt - 1)) + random.uniform(0, 1), 60)
            if attempt < retry_count:
                log_fn(f'  TIMEOUT (試行{attempt}/{retry_count}) — {wait:.1f}秒後リトライ')
                time.sleep(wait)
            else:
                log_fn(f'  TIMEOUT (試行{attempt}/{retry_count}、リトライ上限)')
        except Exception as e:
            last_error = f'ERROR: {e}'
            wait = min(retry_delay_sec * (2 ** (attempt - 1)) + random.uniform(0, 1), 60)
            if attempt < retry_count:
                log_fn(f'  ERROR (試行{attempt}/{retry_count}) {e} — {wait:.1f}秒後リトライ')
                time.sleep(wait)
            else:
                log_fn(f'  ERROR (試行{attempt}/{retry_count}、リトライ上限) {e}')
    return None, last_error

# ========================================
# スライドライブラリ検出
# ========================================
def detect_slide_libs(html, page_url, session, timeout_sec, version_cache):
    """
    HTMLからスライドショーライブラリを検出する。

    Returns: list of dict
        name     : ライブラリ名
        version  : バージョン文字列（不明の場合は空文字）
        status   : '使用中' | '初期化のみ（HTML構造なし）' | '読み込みのみ'
        load_url : 検出した script src / link href の URL
    """
    # <script src> と <link href> を収集（絶対URLに変換）
    raw_urls = (
        re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I) +
        re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', html, re.I)
    )
    load_urls = [urljoin(page_url, u) for u in raw_urls]

    # インライン <script> の内容を結合（src 属性のないものだけ）
    inline_js = '\n'.join(
        re.findall(r'<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>', html, re.I | re.DOTALL)
    )

    results = []
    for lib in SLIDE_LIBS:
        # 読み込みチェック
        matched_url = next((u for u in load_urls if lib['load_re'].search(u)), None)
        if not matched_url:
            continue

        # バージョン抽出: まず URL から、なければファイルをフェッチ
        m = lib['ver_re'].search(matched_url)
        version = m.group(1) if m else ''
        if not version:
            version = fetch_lib_version(matched_url, session, timeout_sec, version_cache)

        # 使用チェック（二段階）
        has_init = bool(lib['init_re'].search(inline_js))
        has_html = bool(lib['html_re'].search(html))

        if has_html:
            status = '使用中'
        elif has_init:
            status = '初期化のみ（HTML構造なし）'
        else:
            status = '読み込みのみ'

        results.append({
            'name':     lib['name'],
            'version':  version,
            'status':   status,
            'load_url': matched_url,
        })

    return results

# ========================================
# チェッカー本体
# ========================================
def run_checker(config, log_fn, done_fn, stop_event):
    """
    クローラー本体。別スレッドで実行。
    config: dict, log_fn: ログコールバック, done_fn: 完了コールバック, stop_event: threading.Event
    """
    start_url       = normalize_url(config['start_url'])
    output_csv      = config['output_csv']
    max_pages       = config['max_pages']
    delay_sec       = config['delay_sec']
    timeout_sec     = config['timeout_sec']
    retry_count     = config['retry_count']
    retry_delay_sec = config['retry_delay_sec']
    respect_robots  = config['respect_robots']
    exclude_dirs    = config.get('exclude_dirs', [])

    # ログファイル
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(app_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_filename = datetime.now().strftime('slidecheck_%Y%m%d_%H%M%S.log')
    log_path = os.path.join(log_dir, log_filename)
    log_file = open(log_path, 'w', encoding='utf-8')

    _gui_log = log_fn
    def log_fn(text):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_file.write(f'{ts} {text}\n')
        log_file.flush()
        _gui_log(text)

    parsed      = urlparse(start_url)
    base_domain = parsed.netloc
    base_url    = f'{parsed.scheme}://{parsed.netloc}'

    log_fn(f'チェック開始: {start_url}')
    log_fn(f'ドメイン: {base_domain} / 最大ページ数: {max_pages}')
    if exclude_dirs:
        log_fn(f'除外ディレクトリ: {", ".join(exclude_dirs)}')
    log_fn('-' * 60)

    def is_filtered_url(url):
        path = urlparse(url).path
        return any(path.startswith(d) for d in exclude_dirs)

    # 注意: 同時接続は意図的に1本に制限している。
    session = requests.Session()
    session.headers.update({'User-Agent': 'SiteCrawlerBot/1.0 (+https://github.com/uriu1709/site-crawler)'})
    session.mount('https://', _SSLAdapter())

    rp = load_robots(session, base_url, timeout_sec, log_fn) if respect_robots else None

    effective_delay = delay_sec
    if rp:
        try:
            crawl_delay = rp.crawl_delay('*')
            if crawl_delay and float(crawl_delay) > delay_sec:
                effective_delay = float(crawl_delay)
                log_fn(f'robots.txt の Crawl-delay={crawl_delay}秒を採用')
        except Exception:
            pass

    visited      = set()
    queue        = deque([start_url])
    queued       = {start_url}
    version_cache = {}   # JS/CSS ファイルのバージョンキャッシュ（URL → バージョン文字列）
    results      = []    # list of dict: url, title, library, version, status, load_url
    fetch_count  = 0     # リダイレクト元を除いた実フェッチ数（ページ番号・上限管理に使用）

    while queue and fetch_count < max_pages:
        if stop_event.is_set():
            log_fn('\n⛔ 中断されました')
            break

        url = queue.popleft()
        if url in visited:
            continue

        if is_filtered_url(url):
            visited.add(url)
            log_fn(f'[SKIP:フィルタ] {url}')
            continue

        if rp and not rp.can_fetch('*', url):
            log_fn(f'[SKIP:robots] {url}')
            visited.add(url)
            continue

        visited.add(url)

        resp, error = fetch_with_retry(session, url, timeout_sec, retry_count, retry_delay_sec, log_fn)

        if resp is None:
            fetch_count += 1
            log_fn(f'[{fetch_count:4d}] {"TIMEOUT" if error == "TIMEOUT" else "ERROR"} {url}')
            time.sleep(effective_delay)
            continue

        final_url = normalize_url(resp.url)
        if final_url != url:
            if final_url in visited:
                # リダイレクト先が処理済み → カウントせずスキップ
                log_fn(f'       SKIP:リダイレクト先処理済 {url} → {final_url}')
                time.sleep(effective_delay)
                continue
            visited.add(final_url)

        # リダイレクト元はカウントせず、ここで初めてカウント
        fetch_count += 1
        count = fetch_count

        if urlparse(final_url).netloc != base_domain:
            log_fn(f'[{count:4d}] SKIP:外部リダイレクト {url}')
            time.sleep(effective_delay)
            continue

        if resp.status_code != 200:
            log_fn(f'[{count:4d}] HTTP_{resp.status_code} {url}')
            time.sleep(effective_delay)
            continue

        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            log_fn(f'[{count:4d}] SKIP:非HTML {url}')
            time.sleep(effective_delay)
            continue

        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding or 'utf-8'
        html  = resp.text
        title = extract_title(html)

        # ライブラリ検出
        detected = detect_slide_libs(html, final_url, session, timeout_sec, version_cache)
        if detected:
            for d in detected:
                ver_str = d['version'] or '不明'
                log_fn(f'[{count:4d}] ✅ {d["name"]} v{ver_str} [{d["status"]}] {final_url}')
                results.append({
                    'url':      final_url,
                    'title':    title,
                    'library':  d['name'],
                    'version':  d['version'],
                    'status':   d['status'],
                    'load_url': d['load_url'],
                })
        else:
            log_fn(f'[{count:4d}] — {final_url}')

        # リンク抽出・キュー追加
        new_links = extract_links(html, final_url, base_domain)
        for link in sorted(new_links):
            if link not in visited and link not in queued and not is_filtered_url(link):
                queue.append(link)
                queued.add(link)

        time.sleep(effective_delay)

    # CSV 出力
    fieldnames = ['url', 'title', 'library', 'version', 'status', 'load_url']
    with open(output_csv, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    log_fn('=' * 60)
    log_fn(f'✅ チェック完了: {fetch_count}ページ確認')
    if results:
        lib_counts = {}
        for r in results:
            lib_counts[r['library']] = lib_counts.get(r['library'], 0) + 1
        for lib, cnt in sorted(lib_counts.items()):
            log_fn(f'   {lib}: {cnt}ページで検出')
    else:
        log_fn('   スライドライブラリ: 検出なし')
    log_fn(f'   保存先: {output_csv}')
    log_fn(f'   ログ: {log_path}')

    log_file.close()
    done_fn(output_csv)

# ========================================
# GUI アプリ
# ========================================
class CheckerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('スライドライブラリチェッカー')
        self.resizable(True, True)
        self.minsize(640, 660)

        self._stop_event   = threading.Event()
        self._check_thread = None

        if getattr(sys, 'frozen', False):
            self._app_dir = os.path.dirname(sys.executable)
        else:
            self._app_dir = os.path.dirname(os.path.abspath(__file__))
        self._config_path = os.path.join(self._app_dir, 'slidecheck_settings.json')

        self._build_ui()
        self._load_settings()

    def _load_settings(self):
        try:
            with open(self._config_path, 'r', encoding='utf-8') as f:
                s = json.load(f)
            if s.get('start_url'):     self.var_url.set(s['start_url'])
            if s.get('output_csv'):    self.var_csv.set(s['output_csv'])
            if 'max_pages'      in s:  self.var_max.set(s['max_pages'])
            if 'delay_sec'      in s:  self.var_delay.set(s['delay_sec'])
            if 'timeout_sec'    in s:  self.var_timeout.set(s['timeout_sec'])
            if 'retry_count'    in s:  self.var_retry.set(s['retry_count'])
            if 'retry_delay_sec'in s:  self.var_retry_delay.set(s['retry_delay_sec'])
            if 'respect_robots' in s:  self.var_robots.set(s['respect_robots'])
            if s.get('exclude_dirs'):  self.txt_exclude.insert('1.0', s['exclude_dirs'])
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    def _save_settings(self, config):
        s = {k: config[k] for k in config}
        s['exclude_dirs'] = self.txt_exclude.get('1.0', 'end').strip()
        try:
            with open(self._config_path, 'w', encoding='utf-8') as f:
                json.dump(s, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _build_ui(self):
        cfg = ttk.LabelFrame(self, text='設定', padding=10)
        cfg.pack(fill='x', padx=12, pady=(12, 4))
        cfg.columnconfigure(1, weight=1)

        def lbl(text, r):
            ttk.Label(cfg, text=text).grid(row=r, column=0, sticky='w', padx=(0, 8), pady=3)

        lbl('チェック開始URL', 0)
        self.var_url = tk.StringVar()
        ttk.Entry(cfg, textvariable=self.var_url).grid(row=0, column=1, columnspan=2, sticky='ew')

        lbl('出力CSVファイル', 1)
        self.var_csv = tk.StringVar(value=os.path.join(self._app_dir, 'slidelib_result.csv'))
        ttk.Entry(cfg, textvariable=self.var_csv).grid(row=1, column=1, sticky='ew')
        ttk.Button(cfg, text='参照…', command=self._browse_csv, width=7).grid(row=1, column=2, padx=(4, 0))

        lbl('最大ページ数', 2)
        self.var_max = tk.IntVar(value=2000)
        ttk.Spinbox(cfg, textvariable=self.var_max, from_=1, to=99999, width=8).grid(row=2, column=1, sticky='w')

        lbl('リクエスト間隔（秒）', 3)
        self.var_delay = tk.DoubleVar(value=1.5)
        ttk.Spinbox(cfg, textvariable=self.var_delay, from_=0.5, to=30.0, increment=0.1, format='%.1f', width=8).grid(row=3, column=1, sticky='w')

        lbl('タイムアウト（秒）', 4)
        self.var_timeout = tk.IntVar(value=20)
        ttk.Spinbox(cfg, textvariable=self.var_timeout, from_=1, to=120, width=8).grid(row=4, column=1, sticky='w')

        lbl('リトライ回数', 5)
        self.var_retry = tk.IntVar(value=3)
        ttk.Spinbox(cfg, textvariable=self.var_retry, from_=0, to=10, width=8).grid(row=5, column=1, sticky='w')

        lbl('リトライ待機（秒）', 6)
        self.var_retry_delay = tk.DoubleVar(value=3.0)
        ttk.Spinbox(cfg, textvariable=self.var_retry_delay, from_=0.0, to=60.0, increment=0.5, format='%.1f', width=8).grid(row=6, column=1, sticky='w')

        lbl('robots.txt を尊重', 7)
        self.var_robots = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg, variable=self.var_robots).grid(row=7, column=1, sticky='w')

        filter_frame = ttk.LabelFrame(self, text='フィルタ設定', padding=10)
        filter_frame.pack(fill='x', padx=12, pady=(4, 4))
        filter_frame.columnconfigure(0, weight=1)

        ttk.Label(filter_frame, text='除外ディレクトリ（1行1パス、例: /news/）',
                  font=('', 8)).grid(row=0, column=0, sticky='w')
        self.txt_exclude = tk.Text(filter_frame, height=3, font=('Consolas', 9))
        self.txt_exclude.grid(row=1, column=0, sticky='ew', pady=(0, 4))

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill='x', padx=12, pady=4)
        self.btn_start = ttk.Button(btn_frame, text='▶ チェック開始', command=self._start, width=18)
        self.btn_start.pack(side='left')
        self.btn_stop = ttk.Button(btn_frame, text='⛔ 中断', command=self._stop, width=10, state='disabled')
        self.btn_stop.pack(side='left', padx=(8, 0))
        self.btn_open = ttk.Button(btn_frame, text='📂 CSVを開く', command=self._open_csv, width=14, state='disabled')
        self.btn_open.pack(side='right')

        log_frame = ttk.LabelFrame(self, text='ログ', padding=6)
        log_frame.pack(fill='both', expand=True, padx=12, pady=(4, 12))
        self.log_area = scrolledtext.ScrolledText(
            log_frame, state='disabled', wrap='none',
            font=('Consolas', 9) if os.name == 'nt' else ('Menlo', 10),
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white',
        )
        self.log_area.pack(fill='both', expand=True)

    def _browse_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension='.csv',
            filetypes=[('CSV ファイル', '*.csv'), ('すべてのファイル', '*.*')],
            initialfile='slidelib_result.csv',
        )
        if path:
            self.var_csv.set(path)

    def _log(self, text):
        def _write():
            self.log_area.config(state='normal')
            self.log_area.insert('end', text + '\n')
            self.log_area.see('end')
            self.log_area.config(state='disabled')
        self.after(0, _write)

    def _start(self):
        url = self.var_url.get().strip()
        if not url.startswith(('http://', 'https://')):
            messagebox.showerror('入力エラー', 'URLは http:// または https:// で始めてください')
            return
        csv_path = self.var_csv.get().strip()
        if not csv_path:
            messagebox.showerror('入力エラー', 'CSVの保存先を指定してください')
            return

        exclude_text = self.txt_exclude.get('1.0', 'end').strip()
        exclude_dirs = [line.strip() for line in exclude_text.splitlines() if line.strip()]
        exclude_dirs = [d if d.endswith('/') else d + '/' for d in exclude_dirs]

        config = {
            'start_url':       url,
            'output_csv':      csv_path,
            'max_pages':       self.var_max.get(),
            'delay_sec':       self.var_delay.get(),
            'timeout_sec':     self.var_timeout.get(),
            'retry_count':     self.var_retry.get(),
            'retry_delay_sec': self.var_retry_delay.get(),
            'respect_robots':  self.var_robots.get(),
            'exclude_dirs':    exclude_dirs,
        }
        self._save_settings(config)
        self._stop_event.clear()
        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.btn_open.config(state='disabled')
        self._last_csv = None

        self.log_area.config(state='normal')
        self.log_area.delete('1.0', 'end')
        self.log_area.config(state='disabled')

        self._check_thread = threading.Thread(
            target=run_checker,
            args=(config, self._log, self._on_done, self._stop_event),
            daemon=True,
        )
        self._check_thread.start()

    def _stop(self):
        self._stop_event.set()
        self.btn_stop.config(state='disabled')

    def _on_done(self, csv_path):
        self._last_csv = csv_path
        def _update():
            self.btn_start.config(state='normal')
            self.btn_stop.config(state='disabled')
            self.btn_open.config(state='normal')
        self.after(0, _update)

    def _open_csv(self):
        if self._last_csv and os.path.exists(self._last_csv):
            os.startfile(self._last_csv) if os.name == 'nt' else os.system(f'open "{self._last_csv}"')

# ========================================
# エントリーポイント
# ========================================
if __name__ == '__main__':
    import traceback

    def _handle_exception(exc_type, exc_value, exc_tb):
        msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            import tkinter.messagebox as mb
            mb.showerror('起動エラー', msg)
        except Exception:
            pass

    sys.excepthook = _handle_exception

    try:
        app = CheckerApp()
        app.report_callback_exception = lambda *args: _handle_exception(*args)
        app.mainloop()
    except Exception:
        _handle_exception(*sys.exc_info())
