#!/usr/bin/env python3
"""
JP NEXT — TDnet M&Aニュースまとめ記事生成スクリプト
前日の適時開示からM&A関連を全件取得し、1本のまとめ記事を毎朝8時に生成する
"""

import os
import json
import re
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

# ── 設定 ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO       = os.environ.get("GITHUB_REPO", "soraebaebar/jpnext-website")

NEWS_DIR    = "news"
POSTED_JSON = "data/posted.json"

MA_KEYWORDS = [
    "M&A", "合併", "買収", "譲渡", "事業承継", "TOB", "株式交換",
    "子会社化", "持分法", "資本業務提携", "会社分割", "吸収合併",
    "連結子会社", "第三者割当", "MBO", "売却", "経営統合",
]

JST = timezone(timedelta(hours=9))


def jst_now():
    return datetime.now(JST)

def load_posted():
    if os.path.exists(POSTED_JSON):
        with open(POSTED_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"posted_dates": [], "last_updated": ""}

def save_posted(data):
    os.makedirs("data", exist_ok=True)
    data["last_updated"] = jst_now().isoformat()
    with open(POSTED_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def fetch_url(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; JPNEXTBot/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_yesterday_news():
    yesterday = (jst_now() - timedelta(days=1)).strftime("%Y%m%d")
    items = []
    seen = set()

    urls = [
        f"https://webapi.yanoshin.jp/webapi/tdnet/list/{yesterday}.rss",
        "https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss",
    ]

    for url in urls:
        try:
            xml = fetch_url(url)
            root = ET.fromstring(xml)
            raw_items = root.findall('.//item')
            print(f"TDnet取得: {url.split('/')[-1]} -> {len(raw_items)}件")

            for item in raw_items:
                title_el = item.find('title')
                link_el  = item.find('link')
                desc_el  = item.find('description')

                if title_el is None:
                    continue

                title = title_el.text or ''
                link  = link_el.text or '' if link_el is not None else ''
                desc  = re.sub(r'<[^>]+>', '', desc_el.text or '') if desc_el is not None else ''

                if not any(kw in title + desc for kw in MA_KEYWORDS):
                    continue

                item_id = hashlib.md5(title.encode()).hexdigest()[:12]
                if item_id in seen:
                    continue
                seen.add(item_id)

                company_match = re.search(r'^(.+?)[（(][\d\w]{4}[）)]', title)
                company = company_match.group(1).strip() if company_match else '上場企業'

                items.append({
                    "company": company,
                    "title": title,
                    "url": link,
                    "desc": desc[:200],
                })

            if items:
                break

        except Exception as e:
            print(f"TDnet取得失敗: {e}")

    print(f"M&A関連: {len(items)}件")
    return yesterday, items


def call_claude(prompt, max_tokens=4000):
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    return result["content"][0]["text"]


def generate_digest(date_str, news_items):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    date_label = f"{y}年{m}月{d}日"

    news_list_text = ""
    for i, item in enumerate(news_items, 1):
        news_list_text += f"{i}. [{item['company']}] {item['title']}\n"
        if item['desc']:
            news_list_text += f"   概要: {item['desc']}\n"
        news_list_text += "\n"

    prompt = f"""あなたはM&Aアドバイザーの経験を持つ専門ライターです。
{date_label}に東証TDnetで開示されたM&A関連の適時開示情報をまとめた解説記事を書いてください。

【{date_label}のM&A関連適時開示一覧】
{news_list_text}

【記事の要件】
- 読者：事業承継を検討するオーナー経営者・M&A業界への転職希望者・投資家
- 各開示の解説は3〜5行、「何が起きたか」「M&A的な意味・背景」を簡潔に
- 適時開示の内容をそのまま転載せず、必ず自分の言葉で解説する
- トーン：実務家目線でわかりやすく

【出力形式】JSON形式のみ（コードブロック不要）:
{{
  "seo_title": "{date_label}のM&Aニュースまとめ｜適時開示{len(news_items)}件を解説",
  "meta_description": "メタディスクリプション80〜120文字",
  "h1": "{date_label}のM&Aニュース｜適時開示まとめ",
  "lead": "リード文3〜4文",
  "items": [
    {{
      "company": "会社名",
      "disclosure_title": "開示タイトル",
      "commentary": "解説文3〜5行"
    }}
  ],
  "summary": "本日のまとめ・所感3〜5文"
}}"""

    raw = call_claude(prompt, max_tokens=4000)
    raw = re.sub(r'^```json\s*', '', raw.strip())
    raw = re.sub(r'\s*```$', '', raw.strip())
    return json.loads(raw)


def build_digest_html(date_str, digest, news_items):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    date_label = f"{y}年{m}月{d}日"
    date_display = f"{y}.{m}.{d}"

    items_html = ""
    for i, item in enumerate(digest.get("items", []), 1):
        source = next((n for n in news_items if item.get("company", "") in n["title"]), None)
        source_url = source["url"] if source else "#"
        items_html += f"""
    <div class="disclosure-item">
      <div class="disclosure-num">{i:02d}</div>
      <div class="disclosure-body">
        <div class="disclosure-company">{item.get('company','')}</div>
        <div class="disclosure-title">{item.get('disclosure_title','')}</div>
        <p class="disclosure-commentary">{item.get('commentary','')}</p>
        <a href="{source_url}" target="_blank" rel="noopener" class="disclosure-source">適時開示を見る →</a>
      </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{digest['seo_title']} | JP NEXT</title>
  <meta name="description" content="{digest['meta_description']}">
  <meta property="og:title" content="{digest['seo_title']}">
  <meta property="og:url" content="https://www.jpnext.co/news/ma-{date_str}.html">
  <link rel="canonical" href="https://www.jpnext.co/news/ma-{date_str}.html">
  <link rel="icon" href="/favicon.ico">
  <link rel="stylesheet" href="/assets/style.css">
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-P8KB1E1GRV"></script>
  <script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-P8KB1E1GRV');</script>
  <style>
    .news-header{{background:var(--black);padding:48px 24px 40px;border-bottom:1px solid #222;}}
    .news-header-inner{{max-width:760px;margin:0 auto;}}
    .news-date-label{{font-size:0.72rem;font-weight:700;letter-spacing:0.14em;color:var(--accent-mid);text-transform:uppercase;margin-bottom:12px;}}
    .news-h1{{font-family:var(--font-serif);font-size:clamp(1.6rem,3.5vw,2.2rem);color:var(--white);line-height:1.4;margin-bottom:14px;}}
    .news-lead{{font-size:0.9rem;color:var(--gray-400);line-height:1.9;}}
    .news-meta{{display:flex;gap:16px;margin-top:16px;font-size:0.75rem;color:var(--gray-600);}}
    .news-layout{{max-width:900px;margin:0 auto;padding:36px 24px;display:grid;grid-template-columns:1fr 240px;gap:40px;align-items:start;}}
    .notice-box{{background:var(--gold-light);border-left:4px solid var(--gold);padding:12px 16px;border-radius:0 6px 6px 0;margin-bottom:24px;font-size:0.8rem;color:#6b4e1a;line-height:1.7;}}
    .disclosure-list{{display:flex;flex-direction:column;gap:0;}}
    .disclosure-item{{display:grid;grid-template-columns:44px 1fr;gap:14px;padding:22px 0;border-bottom:1px solid var(--gray-200);}}
    .disclosure-item:last-child{{border-bottom:none;}}
    .disclosure-num{{font-family:var(--font-serif);font-size:1.5rem;color:var(--accent);line-height:1;padding-top:4px;}}
    .disclosure-company{{font-size:0.75rem;font-weight:700;color:var(--accent);letter-spacing:0.06em;margin-bottom:4px;}}
    .disclosure-title{{font-size:0.9rem;font-weight:500;color:var(--black);line-height:1.5;margin-bottom:8px;}}
    .disclosure-commentary{{font-size:0.86rem;color:var(--gray-600);line-height:1.85;margin-bottom:8px;}}
    .disclosure-source{{font-size:0.76rem;color:var(--accent);}}
    .summary-box{{background:var(--gray-100);border-radius:10px;padding:22px 26px;margin-top:28px;}}
    .summary-title{{font-size:0.75rem;font-weight:700;color:var(--gray-600);letter-spacing:0.08em;margin-bottom:10px;text-transform:uppercase;}}
    .summary-box p{{font-size:0.88rem;color:var(--gray-600);line-height:1.85;}}
    .sidebar-sticky{{position:sticky;top:80px;display:flex;flex-direction:column;gap:16px;}}
    .sw{{background:var(--white);border:1px solid var(--gray-200);border-radius:10px;overflow:hidden;}}
    .sw-head{{background:var(--accent);padding:10px 16px;font-size:0.68rem;font-weight:700;letter-spacing:0.1em;color:var(--white);text-transform:uppercase;}}
    .sw-body{{padding:14px;font-size:0.8rem;color:var(--gray-600);line-height:1.7;}}
    @media(max-width:900px){{.news-layout{{grid-template-columns:1fr;}}.sidebar-sticky{{display:none;}}}}
  </style>
</head>
<body>
<nav class="nav">
  <div class="nav-inner">
    <a href="/" class="nav-logo">JP <span>NEXT</span></a>
    <ul class="nav-links">
      <li><a href="/blog/">コラム</a></li>
      <li><a href="/news/">M&Aニュース</a></li>
      <li><a href="/blog/?cat=career">転職ガイド</a></li>
      <li><a href="/ma-tool.html">無料ツール</a></li>
      <li><a href="/#contact" class="nav-cta">無料相談</a></li>
    </ul>
  </div>
</nav>
<div style="padding:10px 24px;font-size:0.75rem;color:var(--gray-400);border-bottom:1px solid var(--gray-200);">
  <div style="max-width:var(--max-w);margin:0 auto;">
    <a href="/" style="color:var(--gray-400);">JP NEXT</a> › <a href="/news/" style="color:var(--gray-400);">M&Aニュース</a> › {date_label}のまとめ
  </div>
</div>
<div class="news-header">
  <div class="news-header-inner">
    <div class="news-date-label">M&A News Digest — {date_display}</div>
    <h1 class="news-h1">{digest['h1']}</h1>
    <p class="news-lead">{digest['lead']}</p>
    <div class="news-meta">
      <span>適時開示 {len(news_items)}件</span>
      <span>掲載日: {date_display}</span>
      <span>出典: 東証TDnet</span>
    </div>
  </div>
</div>
<div class="news-layout">
  <main>
    <div class="notice-box">この記事は東証TDnetの適時開示情報をもとに作成しています。各社の開示原文は「適時開示を見る」からご確認ください。実務解説は<a href="/blog/" style="color:var(--gold);font-weight:700;">コラム</a>へ。</div>
    <div class="disclosure-list">{items_html}</div>
    <div class="summary-box">
      <div class="summary-title">本日のまとめ・所感</div>
      <p>{digest['summary']}</p>
    </div>
    <div style="background:var(--black);border-radius:10px;padding:22px;text-align:center;margin-top:28px;">
      <div style="font-family:var(--font-serif);font-size:1.05rem;color:var(--white);margin-bottom:8px;">自社の企業価値を無料で算定する</div>
      <p style="font-size:0.78rem;color:var(--gray-600);margin-bottom:14px;">登録不要。売上・利益を入力するだけ。</p>
      <a href="/ma-tool.html" class="btn btn-primary" style="display:inline-block;padding:9px 24px;">ツールを使う →</a>
    </div>
  </main>
  <aside class="sidebar-sticky">
    <div style="background:var(--black);border-radius:10px;padding:18px;text-align:center;">
      <div style="font-family:var(--font-serif);font-size:0.95rem;color:var(--white);margin-bottom:6px;">無料で企業価値を算定</div>
      <p style="font-size:0.75rem;color:var(--gray-600);margin-bottom:12px;line-height:1.6;">登録不要・3手法対応</p>
      <a href="/ma-tool.html" class="btn btn-primary" style="display:block;text-align:center;padding:8px;font-size:0.8rem;">ツールを使う →</a>
    </div>
    <div class="sw">
      <div class="sw-head">実務解説コラム</div>
      <div class="sw-body">
        <ul style="list-style:none;display:flex;flex-direction:column;gap:8px;">
          <li><a href="/blog/ma-career.html" style="color:var(--accent);font-size:0.78rem;">M&Aアドバイザーの年収実態</a></li>
          <li><a href="/blog/kigyokachi-santei.html" style="color:var(--accent);font-size:0.78rem;">企業価値算定の3手法</a></li>
          <li><a href="/news/" style="color:var(--accent);font-size:0.78rem;">M&Aニュース一覧 →</a></li>
        </ul>
      </div>
    </div>
  </aside>
</div>
<footer class="footer">
  <div class="footer-inner">
    <div class="footer-logo">JP <span>NEXT</span></div>
    <div class="footer-tagline">M&A・事業承継の実務情報メディア</div>
    <div class="footer-bottom" style="margin-top:28px;">
      <span>© 2025 JP NEXT. All rights reserved.</span>
      <a href="/" style="color:var(--gray-600);">トップへ戻る</a>
    </div>
  </div>
</footer>
</body>
</html>"""


def update_news_index(date_str, digest, news_items):
    index_path = os.path.join(NEWS_DIR, "index.html")
    if not os.path.exists(index_path):
        print("news/index.html が見つかりません")
        return

    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    insert_marker = '<div class="news-list" id="newsList">'
    if insert_marker not in html:
        print("マーカーが見つかりません")
        return

    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    new_card = f"""
      <article class="news-row">
        <div class="news-date-badge">
          <div class="news-date-month">{m}月</div>
          <div class="news-date-day">{d}</div>
        </div>
        <div>
          <div class="news-meta">
            <span class="tag tag-ma">適時開示まとめ</span>
            <span>{len(news_items)}件</span>
          </div>
          <div class="news-title">
            <a href="/news/ma-{date_str}.html">{digest['h1']}</a>
          </div>
          <p class="news-excerpt">{digest['lead'][:80]}...</p>
        </div>
      </article>"""

    html = html.replace(insert_marker, insert_marker + new_card)
    html = html.replace(
        '<div id="empty-state" style="text-align:center;padding:60px 0;color:var(--gray-400);">',
        '<div id="empty-state" style="display:none;text-align:center;padding:60px 0;color:var(--gray-400);">'
    )

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("news/index.html を更新しました")


def main():
    print(f"=== JP NEXT M&Aニュースまとめ生成 {jst_now().strftime('%Y-%m-%d %H:%M')} ===")

    posted_data  = load_posted()
    posted_dates = set(posted_data.get("posted_dates", []))

    yesterday, news_items = fetch_yesterday_news()

    if not news_items:
        print("M&A関連の適時開示なし。終了します。")
        return

    if yesterday in posted_dates:
        print(f"{yesterday}分は既に生成済み。終了します。")
        return

    print(f"\n{yesterday}のM&A適時開示 {len(news_items)}件でまとめ記事を生成中...")

    try:
        digest = generate_digest(yesterday, news_items)
        html   = build_digest_html(yesterday, digest, news_items)

        os.makedirs(NEWS_DIR, exist_ok=True)
        filepath = os.path.join(NEWS_DIR, f"ma-{yesterday}.html")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        update_news_index(yesterday, digest, news_items)

        posted_dates.add(yesterday)
        posted_data["posted_dates"] = list(posted_dates)
        save_posted(posted_data)

        print(f"\n完了: {filepath} ({len(news_items)}件分)")

    except Exception as e:
        print(f"\n生成失敗: {e}")
        raise


if __name__ == "__main__":
    main()
