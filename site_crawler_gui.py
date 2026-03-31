#!/usr/bin/env python3
"""
サイトクローラー GUI版
tkinterでURL・各種設定を入力し、クロール結果をCSV出力
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
import os
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from html import unescape
from collections import deque

# ========================================
# スキップする拡張子
# ========================================
SKIP_EXTENSIONS = {
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
    '.zip', '.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt',
    '.mp4', '.mp3', '.mov', '.avi', '.wmv',
    '.css', '.js', '.ico', '.woff', '.woff2', '.ttf', '.eot',
}

# ========================================
# クローラーロジック
# ========================================
def normalize_url(url):
    parsed = urlparse(url)
    path = parsed.path
    # 拡張子のないパスは末尾スラッシュに統一（例: /international → /international/）
    # 拡張子ありのパスはそのまま（例: /page.html はスラッシュ不要）
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
    """requests セッションで robots.txt を取得し解析する"""
    rp = RobotFileParser()
    robots_url = base_url.rstrip('/') + '/robots.txt'
    rp.set_url(robots_url)
    try:
        resp = session.get(robots_url, timeout=timeout_sec)
        if resp.status_code in (401, 403):
            log_fn(f'robots.txt: HTTP {resp.status_code} — 全URLが禁止として扱われます')
            rp.disallow_all = True
        elif resp.status_code >= 400:
            log_fn(f'robots.txt: HTTP {resp.status_code} — robots.txt なしとして続行')
            rp.allow_all = True
        else:
            rp.parse(resp.text.splitlines())
            log_fn(f'robots.txt読み込み完了: {robots_url}')
    except Exception as e:
        log_fn(f'robots.txt取得失敗（robots.txt なしとして続行）: {e}')
        rp.allow_all = True
    return rp

def extract_title(html):
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    return unescape(m.group(1).strip()) if m else ''

def extract_description(html):
    m = re.search(r'<meta\s[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    if m: return unescape(m.group(1).strip())
    m = re.search(r'<meta\s[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']description["\']', html, re.IGNORECASE)
    return unescape(m.group(1).strip()) if m else ''

def extract_h1s(html):
    matches = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
    cleaned = []
    for m in matches:
        text = re.sub(r'<[^>]+>', '', m).strip()
        text = unescape(re.sub(r'\s+', ' ', text))
        if text:
            cleaned.append(text)
    return cleaned

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

def detect_js_includes(html):
    """HTMLからJSインクルードパターン（.load(), fetch()等）のパスを検出"""
    paths = set()
    # jQuery .load("path") パターン
    for m in re.findall(r'\.load\(\s*["\']([^"\']+)["\']', html):
        if m.endswith(('.html', '.htm', '.php', '.shtml')):
            paths.add(m)
    # fetch("path") パターン
    for m in re.findall(r'fetch\(\s*["\']([^"\']+)["\']', html):
        if m.endswith(('.html', '.htm', '.php', '.shtml')):
            paths.add(m)
    return paths


def fetch_js_includes(session, html, current_url, base_domain, timeout_sec, delay_sec, cache, log_fn):
    """JSインクルードファイルを取得し、追加リンクを抽出して返す"""
    include_paths = detect_js_includes(html)
    if not include_paths:
        return set()

    extra_links = set()
    for path in include_paths:
        abs_url = urljoin(current_url, path)
        if abs_url in cache:
            # キャッシュ済みのリンクを再利用
            extra_links |= cache[abs_url]
            continue
        try:
            time.sleep(delay_sec)
            resp = session.get(abs_url, timeout=timeout_sec,
                               headers={'X-Requested-With': 'XMLHttpRequest',
                                        'Referer': current_url})
            if resp.status_code == 200 and 'text/html' in resp.headers.get('Content-Type', ''):
                # リダイレクトで元ページに戻された場合はスキップ
                if normalize_url(resp.url) == normalize_url(current_url):
                    cache[abs_url] = set()
                    continue
                links = extract_links(resp.text, current_url, base_domain)
                cache[abs_url] = links
                extra_links |= links
                log_fn(f'  JSインクルード検出: {path} → リンク{len(links)}件')
            else:
                cache[abs_url] = set()
        except Exception:
            cache[abs_url] = set()
    return extra_links


def get_path_segments(url):
    path = urlparse(url).path
    return [s for s in path.strip('/').split('/') if s]

def fetch_with_retry(session, url, timeout_sec, retry_count, retry_delay_sec, log_fn):
    last_error = None
    for attempt in range(1, retry_count + 1):
        try:
            resp = session.get(url, timeout=timeout_sec, allow_redirects=True)
            return resp, None
        except requests.Timeout:
            last_error = 'TIMEOUT'
            if attempt < retry_count:
                log_fn(f'  TIMEOUT (試行{attempt}/{retry_count}) — {retry_delay_sec}秒後リトライ')
                time.sleep(retry_delay_sec)
            else:
                log_fn(f'  TIMEOUT (試行{attempt}/{retry_count}、リトライ上限)')
        except Exception as e:
            last_error = f'ERROR: {e}'
            if attempt < retry_count:
                log_fn(f'  ERROR (試行{attempt}/{retry_count}) {e} — {retry_delay_sec}秒後リトライ')
                time.sleep(retry_delay_sec)
            else:
                log_fn(f'  ERROR (試行{attempt}/{retry_count}、リトライ上限) {e}')
    return None, last_error


def run_crawler(config, log_fn, done_fn, stop_event):
    """
    クローラー本体。別スレッドで実行。
    config: dict, log_fn: ログ出力コールバック, done_fn: 完了コールバック, stop_event: threading.Event
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
    collapse_dirs   = config.get('collapse_dirs', [])
    wp_auto_detect  = config.get('wp_auto_detect', False)
    skip_pagination = config.get('skip_pagination', False)

    # ログファイル設定（exeと同じ場所の logs/ に日時付きで保存）
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(app_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_filename = datetime.now().strftime('crawl_%Y%m%d_%H%M%S.log')
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

    log_fn(f'クロール開始: {start_url}')
    log_fn(f'ドメイン: {base_domain} / 最大ページ数: {max_pages}')
    log_fn(f'リトライ: {retry_count}回 / 待機: {retry_delay_sec}秒')
    if exclude_dirs:
        log_fn(f'除外ディレクトリ: {", ".join(exclude_dirs)}')
    if collapse_dirs:
        log_fn(f'まとめるディレクトリ: {", ".join(collapse_dirs)}')
    if wp_auto_detect:
        log_fn('WordPress投稿自動まとめ: ON')
    if skip_pagination:
        log_fn('ページネーションスキップ: ON')
    log_fn('-' * 60)

    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; SiteCrawler/1.0)'})
    session.mount('https://', _SSLAdapter())

    rp = load_robots(session, base_url, timeout_sec, log_fn) if respect_robots else None

    visited = set()
    queue   = deque([start_url])
    results = []
    js_include_cache = {}  # JSインクルードファイルのキャッシュ

    # --- フィルタ用ヘルパー ---
    collapse_seen = set()       # まとめ済みグループ
    skip_counts = {'filter': 0, 'collapse': 0}  # スキップカウンター

    def is_filtered_url(url):
        """除外ディレクトリ・ページネーションの静的フィルタ"""
        path = urlparse(url).path
        for d in exclude_dirs:
            if path.startswith(d):
                return True
        if skip_pagination and re.search(r'/page/\d+(/|$)', path):
            return True
        return False

    def check_collapse(url):
        """
        まとめ対象か判定（状態あり）。
        戻り値: (skip: bool, is_representative: bool)
        """
        path = urlparse(url).path
        group = None
        # 手動指定ディレクトリ
        for d in collapse_dirs:
            if path.startswith(d) and path.rstrip('/') != d.rstrip('/'):
                group = f'manual:{d}'
                break
        # WordPress日付パーマリンク自動検出
        if group is None and wp_auto_detect:
            m = re.search(r'/\d{4}/\d{2}/', path)
            if m:
                prefix = path[:m.start()] + '/' if m.start() > 0 else '/'
                group = f'wp:{prefix}'
        if group:
            if group in collapse_seen:
                return True, False
            collapse_seen.add(group)
            return False, True
        return False, False

    def is_collapse_skip(url):
        """キュー追加時の静的まとめチェック（既にまとめ済みグループはスキップ）"""
        path = urlparse(url).path
        for d in collapse_dirs:
            if path.startswith(d) and path.rstrip('/') != d.rstrip('/'):
                if f'manual:{d}' in collapse_seen:
                    return True
        if wp_auto_detect:
            m = re.search(r'/\d{4}/\d{2}/', path)
            if m:
                prefix = path[:m.start()] + '/' if m.start() > 0 else '/'
                if f'wp:{prefix}' in collapse_seen:
                    return True
        return False

    while queue and len(visited) < max_pages:
        if stop_event.is_set():
            log_fn('\n⛔ 中断されました')
            break

        url = queue.popleft()
        if url in visited:
            continue

        # フィルタチェック（除外ディレクトリ、ページネーション）
        if is_filtered_url(url):
            log_fn(f'[SKIP:フィルタ] {url}')
            visited.add(url)
            skip_counts['filter'] += 1
            continue

        # まとめチェック
        collapse_skip, is_representative = check_collapse(url)
        if collapse_skip:
            log_fn(f'[SKIP:まとめ] {url}')
            visited.add(url)
            skip_counts['collapse'] += 1
            continue

        if rp and not rp.can_fetch('*', url):
            log_fn(f'[SKIP:robots] {url}')
            visited.add(url)
            continue

        visited.add(url)
        count = len(visited)

        resp, error = fetch_with_retry(session, url, timeout_sec, retry_count, retry_delay_sec, log_fn)

        if resp is None:
            status = 'TIMEOUT' if error == 'TIMEOUT' else 'ERROR'
            log_fn(f'[{count:4d}] {status} {url}')
            results.append({'url': url, 'status': status, 'title': '', 'description': '', 'h1': ''})
            time.sleep(delay_sec)
            continue

        final_url = normalize_url(resp.url)
        # リダイレクト先URLも visited に追加（重複記録を防止）
        if final_url != url:
            visited.add(final_url)

        if urlparse(final_url).netloc != base_domain:
            log_fn(f'[{count:4d}] SKIP:外部リダイレクト {url}')
            time.sleep(delay_sec)
            continue

        if resp.status_code != 200:
            log_fn(f'[{count:4d}] HTTP_{resp.status_code} {url}')
            results.append({'url': url, 'status': resp.status_code, 'title': '', 'description': '', 'h1': ''})
            time.sleep(delay_sec)
            continue

        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            log_fn(f'[{count:4d}] SKIP:非HTML ({content_type.split(";")[0].strip()}) {url}')
            time.sleep(delay_sec)
            continue

        # ヘッダーに文字コード指定がない場合はUTF-8として処理
        # （requestsのデフォルトフォールバック ISO-8859-1 による文字化けを防ぐ）
        if resp.encoding is None or resp.encoding.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding or 'utf-8'
        html  = resp.text
        title = extract_title(html)
        desc  = extract_description(html)
        h1s   = extract_h1s(html)

        # まとめ代表ページはメタデータを空にする（ツリー構造の代表として残すだけ）
        if is_representative:
            title = ''
            desc = ''
            h1s = []

        results.append({
            'url':         final_url,
            'status':      200,
            'title':       title,
            'description': desc,
            'h1':          ', '.join(h1s),
        })
        if is_representative:
            log_fn(f'[{count:4d}] OK (まとめ代表) {url}')
        else:
            log_fn(f'[{count:4d}] OK {url}')

        new_links = extract_links(html, final_url, base_domain)
        # JSインクルードファイル（.load()等で読み込まれるヘッダー/フッター）からもリンク抽出
        new_links |= fetch_js_includes(session, html, final_url, base_domain,
                                       timeout_sec, delay_sec, js_include_cache, log_fn)
        for link in sorted(new_links):
            if link not in visited and not is_filtered_url(link) and not is_collapse_skip(link):
                queue.append(link)

        time.sleep(delay_sec)

    # CSV出力
    # seg0=ルート, seg1=第1階層, ... segN=最深階層（タイトルを最下層セルに配置）
    max_depth  = max((len(get_path_segments(row['url'])) for row in results), default=0)
    seg_count  = max_depth + 1  # seg0（ルート用）を含む
    seg_fields = [f'seg{i}' for i in range(seg_count)]
    fieldnames = seg_fields + ['url', 'status', 'title', 'description', 'h1']

    with open(output_csv, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            depth    = len(get_path_segments(row['url']))
            seg_dict = {f'seg{i}': '' for i in range(seg_count)}
            seg_dict[f'seg{depth}'] = row['title']
            writer.writerow({**seg_dict, **row})

    log_fn('=' * 60)
    log_fn(f'✅ クロール完了: {len(results)}ページ')
    if skip_counts['filter'] > 0:
        log_fn(f'   フィルタでスキップ: {skip_counts["filter"]}件')
    if skip_counts['collapse'] > 0:
        log_fn(f'   まとめでスキップ: {skip_counts["collapse"]}件')
    log_fn(f'   パス最大深度: {max_depth}階層 → seg0〜seg{max_depth} 列')
    log_fn(f'   保存先: {output_csv}')
    log_fn(f'   ログ: {log_path}')

    log_file.close()
    done_fn(output_csv)


# ========================================
# GUIアプリ
# ========================================
class CrawlerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('サイトクローラー')
        self.resizable(True, True)
        self.minsize(700, 780)

        self._stop_event   = threading.Event()
        self._crawl_thread = None

        # アプリケーションディレクトリ（exeまたはスクリプトと同じ場所）
        if getattr(sys, 'frozen', False):
            self._app_dir = os.path.dirname(sys.executable)
        else:
            self._app_dir = os.path.dirname(os.path.abspath(__file__))
        self._config_path = os.path.join(self._app_dir, 'crawler_settings.json')

        self._build_ui()
        self._load_settings()

    # ---------- 設定の保存・復元 ----------
    def _load_settings(self):
        """前回の設定を復元"""
        try:
            with open(self._config_path, 'r', encoding='utf-8') as f:
                s = json.load(f)
            if s.get('start_url'):       self.var_url.set(s['start_url'])
            if s.get('output_csv'):      self.var_csv.set(s['output_csv'])
            if 'max_pages' in s:         self.var_max.set(s['max_pages'])
            if 'delay_sec' in s:         self.var_delay.set(s['delay_sec'])
            if 'timeout_sec' in s:       self.var_timeout.set(s['timeout_sec'])
            if 'retry_count' in s:       self.var_retry.set(s['retry_count'])
            if 'retry_delay_sec' in s:   self.var_retry_delay.set(s['retry_delay_sec'])
            if 'respect_robots' in s:    self.var_robots.set(s['respect_robots'])
            if 'wp_auto_detect' in s:    self.var_wp_auto.set(s['wp_auto_detect'])
            if 'skip_pagination' in s:   self.var_skip_page.set(s['skip_pagination'])
            if s.get('exclude_dirs'):
                self.txt_exclude.insert('1.0', s['exclude_dirs'])
            if s.get('collapse_dirs'):
                self.txt_collapse.insert('1.0', s['collapse_dirs'])
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    def _save_settings(self, config):
        """現在の設定を保存"""
        s = {
            'start_url':       config['start_url'],
            'output_csv':      config['output_csv'],
            'max_pages':       config['max_pages'],
            'delay_sec':       config['delay_sec'],
            'timeout_sec':     config['timeout_sec'],
            'retry_count':     config['retry_count'],
            'retry_delay_sec': config['retry_delay_sec'],
            'respect_robots':  config['respect_robots'],
            'wp_auto_detect':  config.get('wp_auto_detect', False),
            'skip_pagination': config.get('skip_pagination', False),
            'exclude_dirs':    self.txt_exclude.get('1.0', 'end').strip(),
            'collapse_dirs':   self.txt_collapse.get('1.0', 'end').strip(),
        }
        try:
            with open(self._config_path, 'w', encoding='utf-8') as f:
                json.dump(s, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- UI構築 ----------
    def _build_ui(self):
        # --- 設定フレーム ---
        cfg_frame = ttk.LabelFrame(self, text='設定', padding=10)
        cfg_frame.pack(fill='x', padx=12, pady=(12, 4))
        cfg_frame.columnconfigure(1, weight=1)

        def row_label(parent, text, r):
            ttk.Label(parent, text=text).grid(row=r, column=0, sticky='w', padx=(0, 8), pady=3)

        # START_URL
        row_label(cfg_frame, 'クロール開始URL', 0)
        self.var_url = tk.StringVar(value='')
        ttk.Entry(cfg_frame, textvariable=self.var_url).grid(row=0, column=1, columnspan=2, sticky='ew')

        # OUTPUT_CSV
        row_label(cfg_frame, '出力CSVファイル', 1)
        self.var_csv = tk.StringVar(value=os.path.join(self._app_dir, 'site_crawl_result.csv'))
        ttk.Entry(cfg_frame, textvariable=self.var_csv).grid(row=1, column=1, sticky='ew')
        ttk.Button(cfg_frame, text='参照…', command=self._browse_csv, width=7).grid(row=1, column=2, padx=(4, 0))

        # MAX_PAGES / DELAY_SEC
        row_label(cfg_frame, '最大ページ数', 2)
        self.var_max  = tk.IntVar(value=2000)
        ttk.Spinbox(cfg_frame, textvariable=self.var_max, from_=1, to=99999, width=8).grid(row=2, column=1, sticky='w')

        row_label(cfg_frame, 'リクエスト間隔（秒）', 3)
        self.var_delay = tk.DoubleVar(value=0.5)
        ttk.Spinbox(cfg_frame, textvariable=self.var_delay, from_=0.0, to=30.0, increment=0.1, format='%.1f', width=8).grid(row=3, column=1, sticky='w')

        # TIMEOUT_SEC
        row_label(cfg_frame, 'タイムアウト（秒）', 4)
        self.var_timeout = tk.IntVar(value=20)
        ttk.Spinbox(cfg_frame, textvariable=self.var_timeout, from_=1, to=120, width=8).grid(row=4, column=1, sticky='w')

        # RETRY_COUNT / RETRY_DELAY
        row_label(cfg_frame, 'リトライ回数', 5)
        self.var_retry = tk.IntVar(value=3)
        ttk.Spinbox(cfg_frame, textvariable=self.var_retry, from_=0, to=10, width=8).grid(row=5, column=1, sticky='w')

        row_label(cfg_frame, 'リトライ待機（秒）', 6)
        self.var_retry_delay = tk.DoubleVar(value=3.0)
        ttk.Spinbox(cfg_frame, textvariable=self.var_retry_delay, from_=0.0, to=60.0, increment=0.5, format='%.1f', width=8).grid(row=6, column=1, sticky='w')

        # robots.txt
        row_label(cfg_frame, 'robots.txt を尊重', 7)
        self.var_robots = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg_frame, variable=self.var_robots).grid(row=7, column=1, sticky='w')

        # --- フィルタ設定フレーム ---
        filter_frame = ttk.LabelFrame(self, text='フィルタ設定', padding=10)
        filter_frame.pack(fill='x', padx=12, pady=(4, 4))
        filter_frame.columnconfigure(0, weight=1)
        filter_frame.columnconfigure(1, weight=1)

        # チェックボックス行
        chk_frame = ttk.Frame(filter_frame)
        chk_frame.grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))

        self.var_wp_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(chk_frame, text='WordPress投稿を自動まとめ（日付パーマリンク検出）',
                         variable=self.var_wp_auto).pack(side='left', padx=(0, 16))

        self.var_skip_page = tk.BooleanVar(value=False)
        ttk.Checkbutton(chk_frame, text='ページネーションをスキップ（/page/N/）',
                         variable=self.var_skip_page).pack(side='left')

        # 除外ディレクトリ
        ttk.Label(filter_frame, text='除外ディレクトリ（1行1パス、例: /wp-content/uploads/）',
                  font=('', 8)).grid(row=1, column=0, sticky='w')
        self.txt_exclude = tk.Text(filter_frame, height=3, width=40, font=('Consolas', 9))
        self.txt_exclude.grid(row=2, column=0, sticky='ew', padx=(0, 6), pady=(0, 4))

        # まとめるディレクトリ
        ttk.Label(filter_frame, text='まとめるディレクトリ（1行1パス、例: /news/）',
                  font=('', 8)).grid(row=1, column=1, sticky='w')
        self.txt_collapse = tk.Text(filter_frame, height=3, width=40, font=('Consolas', 9))
        self.txt_collapse.grid(row=2, column=1, sticky='ew', padx=(6, 0), pady=(0, 4))

        # --- ボタン ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill='x', padx=12, pady=4)

        self.btn_start = ttk.Button(btn_frame, text='▶ クロール開始', command=self._start, width=18)
        self.btn_start.pack(side='left')

        self.btn_stop = ttk.Button(btn_frame, text='⛔ 中断', command=self._stop, width=10, state='disabled')
        self.btn_stop.pack(side='left', padx=(8, 0))

        self.btn_open = ttk.Button(btn_frame, text='📂 CSVを開く', command=self._open_csv, width=14, state='disabled')
        self.btn_open.pack(side='right')

        # --- ログエリア ---
        log_frame = ttk.LabelFrame(self, text='ログ', padding=6)
        log_frame.pack(fill='both', expand=True, padx=12, pady=(4, 12))

        self.log_area = scrolledtext.ScrolledText(
            log_frame, state='disabled', wrap='none',
            font=('Consolas', 9) if os.name == 'nt' else ('Menlo', 10),
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white'
        )
        self.log_area.pack(fill='both', expand=True)

    # ---------- 参照ボタン ----------
    def _browse_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension='.csv',
            filetypes=[('CSV ファイル', '*.csv'), ('すべてのファイル', '*.*')],
            initialfile='site_crawl_result.csv',
        )
        if path:
            self.var_csv.set(path)

    # ---------- ログ出力 ----------
    def _log(self, text):
        def _write():
            self.log_area.config(state='normal')
            self.log_area.insert('end', text + '\n')
            self.log_area.see('end')
            self.log_area.config(state='disabled')
        self.after(0, _write)

    # ---------- クロール開始 ----------
    def _start(self):
        url = self.var_url.get().strip()
        if not url.startswith(('http://', 'https://')):
            messagebox.showerror('入力エラー', 'URLは http:// または https:// で始めてください')
            return

        csv_path = self.var_csv.get().strip()
        if not csv_path:
            messagebox.showerror('入力エラー', 'CSVの保存先を指定してください')
            return

        # フィルタ設定: テキストエリアからパスリストを取得
        exclude_text = self.txt_exclude.get('1.0', 'end').strip()
        exclude_dirs = [line.strip() for line in exclude_text.splitlines() if line.strip()]
        exclude_dirs = [d if d.endswith('/') else d + '/' for d in exclude_dirs]

        collapse_text = self.txt_collapse.get('1.0', 'end').strip()
        collapse_dirs = [line.strip() for line in collapse_text.splitlines() if line.strip()]
        collapse_dirs = [d if d.endswith('/') else d + '/' for d in collapse_dirs]

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
            'collapse_dirs':   collapse_dirs,
            'wp_auto_detect':  self.var_wp_auto.get(),
            'skip_pagination': self.var_skip_page.get(),
        }

        self._save_settings(config)

        self._stop_event.clear()
        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.btn_open.config(state='disabled')
        self._last_csv = None

        # ログクリア
        self.log_area.config(state='normal')
        self.log_area.delete('1.0', 'end')
        self.log_area.config(state='disabled')

        self._crawl_thread = threading.Thread(
            target=run_crawler,
            args=(config, self._log, self._on_done, self._stop_event),
            daemon=True,
        )
        self._crawl_thread.start()

    # ---------- 中断 ----------
    def _stop(self):
        self._stop_event.set()
        self.btn_stop.config(state='disabled')

    # ---------- 完了コールバック ----------
    def _on_done(self, csv_path):
        self._last_csv = csv_path
        def _update():
            self.btn_start.config(state='normal')
            self.btn_stop.config(state='disabled')
            self.btn_open.config(state='normal')
        self.after(0, _update)

    # ---------- CSVを開く ----------
    def _open_csv(self):
        if self._last_csv and os.path.exists(self._last_csv):
            os.startfile(self._last_csv) if os.name == 'nt' else os.system(f'open "{self._last_csv}"')


# ========================================
# エントリーポイント
# ========================================
if __name__ == '__main__':
    import sys
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
        app = CrawlerApp()
        app.report_callback_exception = lambda *args: _handle_exception(*args)
        app.mainloop()
    except Exception:
        _handle_exception(*sys.exc_info())
