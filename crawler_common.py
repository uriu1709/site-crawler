#!/usr/bin/env python3
"""
crawler_common.py
サイトクローラー / スライドライブラリチェッカー 共通ロジック。

GUI（tkinter）に依存しない純粋なネットワーク・HTML解析ユーティリティをここに集約する。
tkinter 非依存のため、ヘッドレス環境（CI）でも import / テストが可能。
"""

import os
import re
import ssl
import sys
import time
import random
import subprocess
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from html import unescape

# ========================================
# スキップする拡張子
# ========================================
SKIP_EXTENSIONS = {
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
    '.zip', '.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt',
    '.mp4', '.mp3', '.mov', '.avi', '.wmv',
    '.css', '.js', '.ico', '.woff', '.woff2', '.ttf', '.eot',
}


def get_app_dir():
    """exe（PyInstaller frozen）またはこのモジュールと同じ場所を返す。

    ログ・設定ファイルの保存先決定に使用する。frozen 時は実行ファイルの
    ディレクトリ、非frozen 時はこのソースファイルのディレクトリ。
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def setup_run_logging(prefix, gui_log):
    """logs/ に日時付きログファイルを開き、(log_fn, log_file, log_path) を返す。

    返す log_fn はタイムスタンプ付きでファイルへ書き、同時に gui_log にも転送する。
    呼び出し側は finally で log_file.close() すること。
    """
    log_dir = os.path.join(get_app_dir(), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, datetime.now().strftime(f'{prefix}_%Y%m%d_%H%M%S.log'))
    log_file = open(log_path, 'w', encoding='utf-8')

    def log_fn(text):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_file.write(f'{ts} {text}\n')
        log_file.flush()
        gui_log(text)

    return log_fn, log_file, log_path


# ========================================
# URL 正規化・判定
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


def get_path_segments(url):
    path = urlparse(url).path
    return [s for s in path.strip('/').split('/') if s]


# ========================================
# SSL 互換アダプタ
# ========================================
class _SSLAdapter(HTTPAdapter):
    """古いサーバー（DH鍵サイズ不足等）にも接続できるよう SSL セキュリティレベルを緩和"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        # LibreSSL（macOS 等）は @SECLEVEL 指定をサポートせず SSLError になるため、
        # 失敗時は通常の DEFAULT 暗号スイートにフォールバックする
        try:
            ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        except ssl.SSLError:
            ctx.set_ciphers('DEFAULT')
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


# ========================================
# robots.txt
# ========================================
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


# ========================================
# HTML 解析
# ========================================
def extract_title(html):
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    return unescape(m.group(1).strip()) if m else ''


def extract_description(html):
    # <meta> タグ単位で走査し、name=description のタグから content を取り出す。
    # content 値はクォート種別（" / '）を区別して取得し、値内に別種クォートが
    # 含まれていても（例: content="It's great"）途中で切れないようにする。
    # 属性順（name先/content先）に依存しないのも利点。
    for meta in re.findall(r'<meta\s[^>]+>', html, re.IGNORECASE):
        if re.search(r'name\s*=\s*["\']description["\']', meta, re.IGNORECASE):
            m = re.search(r'content\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', meta, re.IGNORECASE)
            if m:
                return unescape((m.group(1) or m.group(2) or '').strip())
    return ''


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
    # クォート種別（" / '）を区別して取得し、値内に別種クォートを含む URL でも
    # 途中で切れないようにする
    for g1, g2 in re.findall(r'<a\s[^>]*href\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', html, re.IGNORECASE):
        # 属性値の前後空白を除去してから判定・結合する
        # （空白があると startswith 判定や urljoin が正しく動かないため）
        href = (g1 or g2).strip()
        if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
            continue
        abs_url = normalize_url(urljoin(current_url, href))
        parsed = urlparse(abs_url)
        if parsed.netloc == base_domain and parsed.scheme in ('http', 'https'):
            if not is_skip_url(abs_url):
                links.add(abs_url)
    return links


# ========================================
# JS インクルード（.load() / fetch() で読み込まれるパーシャル）
# ========================================
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
        # 同一ドメイン・http(s) のみ取得する（外部絶対URLへの
        # 意図しないリクエスト＝SSRF/情報漏洩を防ぐ）
        parsed_abs = urlparse(abs_url)
        if parsed_abs.netloc != base_domain or parsed_abs.scheme not in ('http', 'https'):
            continue
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
        except Exception as e:
            # 取得失敗の原因を追えるようログに残す（処理は続行）
            log_fn(f'  JSインクルード取得失敗: {path} - {e}')
            cache[abs_url] = set()
    return extra_links


# ========================================
# 取得（リトライ付き）
# ========================================
def fetch_with_retry(session, url, timeout_sec, retry_count, retry_delay_sec, log_fn):
    """指数バックオフ＋ジッターでリトライしつつ GET する。

    retry_count は「試行回数」。0 以下が渡されても最低 1 回は試行する
    （多重防御。GUI 側でも下限 1 を設定している）。
    """
    retry_count = max(1, retry_count)
    last_error = None
    for attempt in range(1, retry_count + 1):
        try:
            resp = session.get(url, timeout=timeout_sec, allow_redirects=True)
            # 429/503 は Retry-After ヘッダがあれば従い、なければ指数バックオフ
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


def open_path(path):
    """OS に応じてファイルを既定アプリで開く（Windows / macOS / Linux 対応）。

    シェルを介さず引数をリストで渡すことで、パスにシェル特殊文字
    （`"`, `;`, `&`, `|` 等）が含まれていてもコマンドインジェクションが
    起きないようにする。xdg-open 等が存在しない環境でも例外で落ちないよう
    OSError は握りつぶす。相対パス／カレントディレクトリ変更時にも確実に
    開けるよう絶対パスへ変換する。
    """
    try:
        abs_path = os.path.abspath(path)
        if os.name == 'nt':
            os.startfile(abs_path)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', abs_path])
        else:
            subprocess.Popen(['xdg-open', abs_path])
    except OSError:
        pass
