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
import sys
import time
import os
import traceback
from urllib.parse import urlparse
from collections import deque

from crawler_common import (
    get_app_dir,
    setup_run_logging,
    normalize_url,
    _SSLAdapter,
    load_robots,
    extract_title,
    extract_description,
    extract_h1s,
    extract_links,
    fetch_js_includes,
    get_path_segments,
    fetch_with_retry,
    open_path,
)


# ========================================
# クローラー本体
# ========================================
def run_crawler(config, log_fn, done_fn, stop_event):
    """クローラーのGUIエントリ。ログ設定・例外処理・後始末を担い、本体は _crawl に委譲する。

    本体で予期しない例外が出ても、ログファイルを確実に閉じ done_fn を必ず呼ぶことで
    GUIのボタン状態が復帰し、エラー内容もログに残るようにする。ログ初期化
    （logs/ 作成・ログファイル open）自体の失敗も try 内で捕捉する。
    """
    log_file = None
    result_csv = None
    try:
        log_fn, log_file, log_path = setup_run_logging('crawl', log_fn)
        result_csv = _crawl(config, log_fn, log_path, stop_event)
    except Exception:
        log_fn('=' * 60)
        log_fn('❌ 予期しないエラーで中断しました:')
        for line in traceback.format_exc().rstrip().splitlines():
            log_fn('  ' + line)
    finally:
        if log_file is not None:
            log_file.close()
        done_fn(result_csv)


def _crawl(config, log_fn, log_path, stop_event):
    """
    クローラー本体。別スレッドで実行。出力CSVのパスを返す。
    config: dict, log_fn: ログ出力コールバック, log_path: ログファイルパス, stop_event: threading.Event
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

    # 注意: 同時接続は意図的に1本に制限している。
    # 並列化するとサーバ負荷が delay 設定を簡単に突破するため、
    # 高速化したい場合は delay を下げるのではなく対象サイトの管理者に確認のこと。
    session = requests.Session()
    session.headers.update({'User-Agent': 'SiteCrawlerBot/1.0 (+https://github.com/uriu1709/site-crawler)'})
    session.mount('https://', _SSLAdapter())

    rp = load_robots(session, base_url, timeout_sec, log_fn) if respect_robots else None

    # robots.txt の Crawl-delay が設定値より大きければ優先する
    effective_delay = delay_sec
    if rp:
        try:
            crawl_delay = rp.crawl_delay('*')
            if crawl_delay and float(crawl_delay) > delay_sec:
                effective_delay = float(crawl_delay)
                log_fn(f'robots.txt の Crawl-delay={crawl_delay}秒を採用（設定値 {delay_sec}秒より優先）')
        except Exception:
            pass

    visited = set()
    queue   = deque([start_url])
    queued  = {start_url}  # キューに追加済みURLのセット（O(1)重複チェック用）
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
            time.sleep(effective_delay)
            continue

        final_url = normalize_url(resp.url)
        # リダイレクト先が処理済みの場合は重複記録を防ぐためスキップ
        if final_url != url:
            if final_url in visited:
                log_fn(f'[{count:4d}] SKIP:リダイレクト先処理済 {url} → {final_url}')
                time.sleep(effective_delay)
                continue
            visited.add(final_url)

        if urlparse(final_url).netloc != base_domain:
            log_fn(f'[{count:4d}] SKIP:外部リダイレクト {url}')
            time.sleep(effective_delay)
            continue

        if resp.status_code != 200:
            log_fn(f'[{count:4d}] HTTP_{resp.status_code} {url}')
            results.append({'url': url, 'status': resp.status_code, 'title': '', 'description': '', 'h1': ''})
            time.sleep(effective_delay)
            continue

        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            log_fn(f'[{count:4d}] SKIP:非HTML ({content_type.split(";")[0].strip()}) {url}')
            time.sleep(effective_delay)
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
                                       timeout_sec, effective_delay, js_include_cache, log_fn)
        for link in sorted(new_links):
            if link not in visited and link not in queued and not is_filtered_url(link) and not is_collapse_skip(link):
                queue.append(link)
                queued.add(link)

        time.sleep(effective_delay)

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
    return output_csv


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
        self._app_dir = get_app_dir()
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
        self.var_delay = tk.DoubleVar(value=1.5)
        ttk.Spinbox(cfg_frame, textvariable=self.var_delay, from_=0.5, to=30.0, increment=0.1, format='%.1f', width=8).grid(row=3, column=1, sticky='w')

        # TIMEOUT_SEC
        row_label(cfg_frame, 'タイムアウト（秒）', 4)
        self.var_timeout = tk.IntVar(value=20)
        ttk.Spinbox(cfg_frame, textvariable=self.var_timeout, from_=1, to=120, width=8).grid(row=4, column=1, sticky='w')

        # RETRY_COUNT / RETRY_DELAY
        row_label(cfg_frame, '試行回数（リトライ含む）', 5)
        self.var_retry = tk.IntVar(value=3)
        ttk.Spinbox(cfg_frame, textvariable=self.var_retry, from_=1, to=10, width=8).grid(row=5, column=1, sticky='w')

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
        # csv_path が None の場合はエラー終了。開始ボタンは復帰させるが
        # 「CSVを開く」は有効化しない。
        self._last_csv = csv_path
        def _update():
            self.btn_start.config(state='normal')
            self.btn_stop.config(state='disabled')
            self.btn_open.config(state='normal' if csv_path else 'disabled')
        self.after(0, _update)

    # ---------- CSVを開く ----------
    def _open_csv(self):
        if self._last_csv and os.path.exists(self._last_csv):
            open_path(self._last_csv)


# ========================================
# エントリーポイント
# ========================================
if __name__ == '__main__':

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
