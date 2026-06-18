#!/usr/bin/env python3
"""
slide_libs.py
スライドショー系JSライブラリの検出ロジック（tkinter 非依存）。

スライドライブラリチェッカー本体（slide_lib_checker_gui.py）から利用する。
ヘッドレス環境（CI）でも import / テストが可能。
"""

import re
from urllib.parse import urljoin

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

# JS/CSS ファイル内のバージョン文字列を探す正規表現
# 例: /*! Swiper v8.4.5  /  version:"8.4.5"  /  e.version="8.4.5"
# 2つ目のパターンは [^*]+? を使い、"Owl Carousel" 等スペースを含む
# ライブラリ名のコメントでもコメント終端(*)を超えずにバージョンを抽出する。
VERSION_IN_CONTENT_RE = re.compile(
    r'(?:version|VERSION)\s*[:=]\s*["\']v?(\d+\.\d+[.\d]*)["\']'
    r'|/\*!?\s*[^*]+?\s+v?(\d+\.\d+[.\d]*)',
    re.I,
)


def fetch_lib_version(url, session, timeout_sec, cache):
    """JS/CSS ファイルの先頭 8KB からバージョン文字列を取得（キャッシュ付き）"""
    if url in cache:
        return cache[url]
    version = ''
    try:
        with session.get(url, timeout=timeout_sec, stream=True) as resp:
            if resp.status_code == 200:
                chunk = b''
                for c in resp.iter_content(8192):
                    chunk = c
                    break
                content = chunk.decode('utf-8', errors='ignore')
                m = VERSION_IN_CONTENT_RE.search(content)
                version = next((g for g in m.groups() if g), '') if m else ''
    except Exception:
        version = ''
    cache[url] = version
    return version


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
        re.findall(r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']', html, re.I) +
        re.findall(r'<link[^>]+href\s*=\s*["\']([^"\']+)["\']', html, re.I)
    )
    load_urls = [urljoin(page_url, u) for u in raw_urls]

    # インライン <script> の内容を結合（src 属性のないものだけ）
    # \s+src\s*= で実際の src 属性のみを除外条件にする（id="src-..." 等の
    # 単語 src を含む他属性での誤除外を防ぐ）。
    inline_js = '\n'.join(
        re.findall(r'<script(?![^>]*\s+src\s*=)[^>]*>(.*?)</script>', html, re.I | re.DOTALL)
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
