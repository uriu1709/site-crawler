#!/usr/bin/env python3
"""
サイトクローラー
スタートURLから同一ドメインのリンクを辿り、title/description/H1を収集してCSV出力
出力列: seg1, seg2, ..., segN（最深パスに合わせて自動可変）, url, status, title, description, h1
"""

import requests
import csv
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from html import unescape
from collections import deque

# ========================================
# 設定
# ========================================
START_URL       = 'https://www.bgu.ac.jp/'  # クロール開始URL
OUTPUT_CSV      = 'site_crawl_result.csv'
MAX_PAGES       = 2000   # 最大クロールページ数
DELAY_SEC       = 0.5    # リクエスト間隔（秒）
TIMEOUT_SEC     = 20     # タイムアウト（秒）
RESPECT_ROBOTS  = True   # robots.txtを尊重するか
RETRY_COUNT     = 3      # タイムアウト・エラー時の最大リトライ回数
RETRY_DELAY_SEC = 3.0    # リトライ前の待機秒数

# スキップする拡張子（小文字で列挙）
SKIP_EXTENSIONS = {
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
    '.zip', '.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt',
    '.mp4', '.mp3', '.mov', '.avi', '.wmv',
    '.css', '.js', '.ico', '.woff', '.woff2', '.ttf', '.eot',
}

# ========================================
# URL正規化（クエリ・フラグメント除去のみ）
# ========================================
def normalize_url(url):
    parsed = urlparse(url)
    return parsed._replace(query='', fragment='').geturl()

# ========================================
# スキップ対象URLか判定（拡張子ベース）
# ========================================
def is_skip_url(url):
    path = urlparse(url).path.lower()
    filename = path.split('/')[-1]
    if '.' in filename:
        ext = '.' + filename.rsplit('.', 1)[1]
        return ext in SKIP_EXTENSIONS
    return False

# ========================================
# robots.txt読み込み
# ========================================
def load_robots(base_url):
    rp = RobotFileParser()
    rp.set_url(base_url.rstrip('/') + '/robots.txt')
    try:
        rp.read()
        print(f'robots.txt読み込み: {rp.url}')
    except Exception as e:
        print(f'robots.txt読み込み失敗（無視して続行）: {e}')
    return rp

# ========================================
# HTML解析系
# ========================================
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
            # 拡張子でスキップ対象はキューに追加しない
            if not is_skip_url(abs_url):
                links.add(abs_url)
    return links

# ========================================
# URLからパスセグメントリストを取得
# ========================================
def get_path_segments(url):
    path = urlparse(url).path
    return [s for s in path.strip('/').split('/') if s]

# ========================================
# リトライ付きGET
# ========================================
def fetch_with_retry(session, url, count):
    """
    リトライ付きHTTP GET。
    成功時は (resp, None)、全試行失敗時は (None, エラー種別文字列) を返す。
    """
    last_error = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT_SEC, allow_redirects=True)
            return resp, None
        except requests.Timeout:
            last_error = 'TIMEOUT'
            if attempt < RETRY_COUNT:
                print(f'[{count:4d}] TIMEOUT (試行{attempt}/{RETRY_COUNT}) {url} — {RETRY_DELAY_SEC}秒後リトライ')
                time.sleep(RETRY_DELAY_SEC)
            else:
                print(f'[{count:4d}] TIMEOUT (試行{attempt}/{RETRY_COUNT}、リトライ上限) {url}')
        except Exception as e:
            last_error = f'ERROR: {e}'
            if attempt < RETRY_COUNT:
                print(f'[{count:4d}] ERROR (試行{attempt}/{RETRY_COUNT}) {url} — {e} — {RETRY_DELAY_SEC}秒後リトライ')
                time.sleep(RETRY_DELAY_SEC)
            else:
                print(f'[{count:4d}] ERROR (試行{attempt}/{RETRY_COUNT}、リトライ上限) {url} — {e}')
    return None, last_error

# ========================================
# メイン処理
# ========================================
def main():
    start_url   = normalize_url(START_URL)
    parsed      = urlparse(start_url)
    base_domain = parsed.netloc
    base_url    = f'{parsed.scheme}://{parsed.netloc}'

    print(f'クロール開始: {start_url}')
    print(f'ドメイン: {base_domain}')
    print(f'最大ページ数: {MAX_PAGES}')
    print(f'リトライ: {RETRY_COUNT}回 / 待機: {RETRY_DELAY_SEC}秒')

    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; SiteCrawler/1.0)'})

    rp = load_robots(base_url) if RESPECT_ROBOTS else None

    visited = set()
    queue   = deque([start_url])
    results = []

    while queue and len(visited) < MAX_PAGES:
        url = queue.popleft()
        if url in visited:
            continue

        if rp and not rp.can_fetch('*', url):
            print(f'[SKIP:robots] {url}')
            visited.add(url)
            continue

        visited.add(url)
        count = len(visited)

        resp, error = fetch_with_retry(session, url, count)

        if resp is None:
            # 全リトライ失敗
            status = 'TIMEOUT' if error == 'TIMEOUT' else 'ERROR'
            results.append({'url': url, 'status': status, 'title': '', 'description': '', 'h1': ''})
            time.sleep(DELAY_SEC)
            continue

        final_url = normalize_url(resp.url)

        # リダイレクト先が別ドメインならスキップ
        if urlparse(final_url).netloc != base_domain:
            print(f'[{count:4d}] SKIP:外部リダイレクト {url}')
            time.sleep(DELAY_SEC)
            continue

        if resp.status_code != 200:
            print(f'[{count:4d}] HTTP_{resp.status_code} {url}')
            results.append({'url': url, 'status': resp.status_code, 'title': '', 'description': '', 'h1': ''})
            time.sleep(DELAY_SEC)
            continue

        # text/html以外はスキップ（PDF・画像等）— 記録もしない
        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            print(f'[{count:4d}] SKIP:非HTML ({content_type.split(";")[0].strip()}) {url}')
            time.sleep(DELAY_SEC)
            continue

        html  = resp.text
        title = extract_title(html)
        desc  = extract_description(html)
        h1s   = extract_h1s(html)

        results.append({
            'url':         final_url,
            'status':      200,
            'title':       title,
            'description': desc,
            'h1':          ', '.join(h1s),
        })
        print(f'[{count:4d}] OK {url}')

        new_links = extract_links(html, final_url, base_domain)
        for link in sorted(new_links):
            if link not in visited:
                queue.append(link)

        time.sleep(DELAY_SEC)

    # ========================================
    # CSV出力（パスセグメント列を自動生成）
    # ========================================

    # 全結果の最大パス深度を計算
    max_depth = max((len(get_path_segments(row['url'])) for row in results), default=0)

    # 列名を構築: seg1, seg2, ..., segN, url, status, title, description, h1
    seg_fields = [f'seg{i+1}' for i in range(max_depth)]
    fieldnames = seg_fields + ['url', 'status', 'title', 'description', 'h1']

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            segs = get_path_segments(row['url'])
            seg_dict = {f'seg{i+1}': (segs[i] if i < len(segs) else '') for i in range(max_depth)}
            writer.writerow({**seg_dict, **row})

    print('\n' + '='*50)
    print(f'クロール完了: {len(results)}ページ')
    print(f'パス最大深度: {max_depth}階層 → seg1〜seg{max_depth} 列を出力')
    print(f'結果: {OUTPUT_CSV}')

if __name__ == '__main__':
    main()
