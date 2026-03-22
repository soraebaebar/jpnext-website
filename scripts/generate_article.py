#!/usr/bin/env python3
"""
JP NEXT — TDnet自動記事生成スクリプト
TDnetのRSSからM&A関連ニュースを取得し、Claude APIで記事を生成してblog/に追加する
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

# TDnet RSS（適時開示一覧）
TDNET_RSS_URL = "https://www.release.tdnet.info/inbs/I_list_001_{date}.html"
TDNET_RSS_FEED = "https://www.release.tdnet.info/inbs/I_list_001_x.rss"

# M&A関連キーワード（いずれかを含む開示をピックアップ）
MA_KEYWORDS = [
    "M&A", "合併", "買収", "譲渡", "事業承継", "TOB", "株式交換",
    "子会社化", "持分法", "資本業務提携", "会社分割", "吸収合併",
    "連結子会社", "第三者割当", "MBO", "売却", "経営統合",
]

POSTED_JSON = "data/posted.json"
BLOG_DIR    = "blog"
MAX_ARTICLES_PER_RUN = 3  # 1回の実行で最大3記事まで生成


# ── ユーティリティ ────────────────────────────────────
JST = timezone(timedelta(hours=9))

def jst_now():
    return datetime.now(JST)

def load_posted():
    """投稿済みIDを読み込む"""
    if os.path.exists(POSTED_JSON):
        with open(POSTED_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"posted_ids": [], "last_updated": ""}

def save_posted(data):
    """投稿済みIDを保存"""
    os.makedirs("data", exist_ok=True)
    data["last_updated"] = jst_now().isoformat()
    with open(POSTED_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def slugify(text):
    """タイトルからURLスラッグを生成"""
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = text.strip('-').lower()
    if not text or len(text) < 3:
        text = hashlib.md5(text.encode()).hexdigest()[:8]
    return text[:60]

def fetch_url(url, timeout=15):
    """URLからコンテンツを取得"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; JPNEXTBot/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ── TDnetからニュース取得 ─────────────────────────────
def fetch_tdnet_news():
    """TDnet適時開示RSSからM&A関連ニュースを取得"""
    news_items = []

    # 今日と昨日の開示を確認
    for days_ago in range(0, 2):
        target_date = (jst_now() - timedelta(days=days_ago)).strftime("%Y%m%d")
        url = f"https://www.release.tdnet.info/inbs/I_list_001_{target_date}.html"

        try:
            html = fetch_url(url)
            items = parse_tdnet_html(html, target_date)
            news_items.extend(items)
            print(f"TDnet {target_date}: {len(items)}件取得")
        except Exception as e:
            print(f"TDnet {target_date} 取得失敗: {e}")

    return news_items

def parse_tdnet_html(html, date_str):
    """TDnet HTMLから適時開示情報をパース"""
    items = []

    # タイトル行を抽出（簡易パーサー）
    pattern = re.compile(
        r'<td[^>]*class="kjTitle"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?'
        r'<td[^>]*class="kjName"[^>]*>([^<]+)</td>',
        re.DOTALL
    )

    for match in pattern.finditer(html):
        href, title, company = match.group(1), match.group(2).strip(), match.group(3).strip()

        # M&A関連キーワードチェック
        if not any(kw in title for kw in MA_KEYWORDS):
            continue

        # IDを生成（日付+タイトルのハッシュ）
        item_id = hashlib.md5(f"{date_str}_{title}_{company}".encode()).hexdigest()[:16]

        # PDFリンク構築
        if href.startswith("/"):
            pdf_url = f"https://www.release.tdnet.info{href}"
        else:
            pdf_url = href

        items.append({
            "id": item_id,
            "title": title,
            "company": company,
            "date": date_str,
            "url": pdf_url,
        })

    return items


# ── Claude APIで記事生成 ──────────────────────────────
def call_claude(prompt, max_tokens=3000):
    """Claude APIを呼び出して記事を生成"""
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

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    return result["content"][0]["text"]

def generate_article_content(news_item):
    """Claude APIでM&A解説記事を生成"""
    prompt = f"""あなたはM&Aアドバイザーの経験を持つ専門ライターです。
以下の適時開示情報をもとに、jpnext.co（M&A・事業承継メディア）向けの解説記事を書いてください。

【開示情報】
企業名: {news_item['company']}
タイトル: {news_item['title']}
開示日: {news_item['date']}

【記事の要件】
- 読者：事業承継を検討するオーナー経営者 または M&A業界への転職希望者
- 文字数：800〜1200字
- 構成：リード文（2〜3行）→ h2見出し2〜3個 → 各200〜300字の本文 → まとめ
- トーン：実務家目線で信頼感があり、わかりやすい
- SEO：タイトルにキーワードを含める

【出力形式】JSON形式で以下を返してください（コードブロック不要、JSONのみ）:
{{
  "seo_title": "SEOタイトル（30〜60文字、キーワード含む）",
  "meta_description": "メタディスクリプション（80〜120文字）",
  "category": "事業承継" または "M&A仲介" または "転職" のいずれか,
  "h1": "記事タイトル（わかりやすく）",
  "lead": "リード文（2〜3文）",
  "sections": [
    {{"h2": "見出し", "body": "本文"}},
    {{"h2": "見出し", "body": "本文"}},
    {{"h2": "見出し", "body": "本文"}}
  ],
  "summary": "まとめ（3〜5文）",
  "read_time": 読了時間（分、整数）
}}"""

    raw = call_claude(prompt)

    # JSON抽出
    raw = re.sub(r'^```json\s*', '', raw.strip())
    raw = re.sub(r'\s*```$', '', raw.strip())

    return json.loads(raw)


# ── HTML記事ファイル生成 ──────────────────────────────
def build_article_html(news_item, article, slug):
    """記事データからHTMLファイルを生成"""
    date_display = f"{news_item['date'][:4]}.{news_item['date'][4:6]}.{news_item['date'][6:]}"

    tag_class = {
        "事業承継": "tag-ma",
        "M&A仲介": "tag-ma",
        "転職": "tag-transfer",
    }.get(article.get("category", "事業承継"), "tag-ma")

    thumb_bg = {
        "事業承継": "var(--accent)",
        "M&A仲介": "var(--black)",
        "転職": "var(--gold)",
    }.get(article.get("category", "事業承継"), "var(--accent)")

    sections_html = ""
    for sec in article.get("sections", []):
        sections_html += f"""
        <h2 id="{slugify(sec['h2'])}">{sec['h2']}</h2>
        <p>{sec['body']}</p>"""

    toc_items = ""
    for sec in article.get("sections", []):
        toc_items += f'<li><a href="#{slugify(sec["h2"])}">{sec["h2"]}</a></li>\n'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{article['seo_title']} | JP NEXT</title>
  <meta name="description" content="{article['meta_description']}">
  <meta property="og:title" content="{article['seo_title']}">
  <meta property="og:description" content="{article['meta_description']}">
  <meta property="og:url" content="https://www.jpnext.co/blog/{slug}.html">
  <link rel="canonical" href="https://www.jpnext.co/blog/{slug}.html">
  <link rel="icon" href="/favicon.ico">
  <link rel="stylesheet" href="/assets/style.css">
  <style>
    .article-header {{ background:var(--gray-100); padding:56px 24px 48px; border-bottom:1px solid var(--gray-200); }}
    .article-header-inner {{ max-width:760px; margin:0 auto; }}
    .article-meta {{ display:flex; align-items:center; gap:12px; margin-bottom:20px; font-size:0.8rem; color:var(--gray-400); }}
    .article-title {{ font-family:var(--font-serif); font-size:clamp(1.6rem,3.5vw,2.2rem); line-height:1.4; margin-bottom:20px; }}
    .article-lead {{ font-size:0.95rem; color:var(--gray-600); line-height:1.9; padding:20px 24px; background:var(--white); border-left:4px solid var(--accent); border-radius:0 4px 4px 0; }}
    .article-layout {{ max-width:960px; margin:0 auto; padding:48px 24px; display:grid; grid-template-columns:1fr 260px; gap:48px; align-items:start; }}
    .article-content h2 {{ font-family:var(--font-serif); font-size:1.4rem; margin:40px 0 16px; padding-bottom:10px; border-bottom:2px solid var(--gray-200); position:relative; }}
    .article-content h2::after {{ content:''; position:absolute; bottom:-2px; left:0; width:40px; height:2px; background:var(--accent); }}
    .article-content p {{ font-size:0.95rem; line-height:1.95; color:#2a2a2a; margin-bottom:18px; }}
    .toc {{ background:var(--gray-100); border-radius:12px; padding:22px 26px; margin:28px 0 36px; }}
    .toc-title {{ font-size:0.78rem; font-weight:700; color:var(--gray-600); margin-bottom:12px; letter-spacing:0.06em; }}
    .toc ol {{ margin:0 0 0 16px; }}
    .toc li {{ font-size:0.85rem; margin-bottom:6px; }}
    .toc a {{ color:var(--accent); }}
    .source-box {{ background:var(--gray-100); border-radius:8px; padding:16px 20px; margin:32px 0; font-size:0.82rem; color:var(--gray-600); }}
    .sidebar-sticky {{ position:sticky; top:80px; display:flex; flex-direction:column; gap:20px; }}
    .sw {{ background:var(--white); border:1px solid var(--gray-200); border-radius:12px; overflow:hidden; }}
    .sw-head {{ background:var(--accent); padding:12px 18px; font-size:0.72rem; font-weight:700; letter-spacing:0.1em; color:var(--white); text-transform:uppercase; }}
    .sw-body {{ padding:18px; font-size:0.82rem; color:var(--gray-600); line-height:1.7; }}
    @media(max-width:900px){{ .article-layout{{grid-template-columns:1fr;}} .sidebar-sticky{{display:none;}} }}
  </style>
</head>
<body>
<nav class="nav">
  <div class="nav-inner">
    <a href="/" class="nav-logo">JP <span>NEXT</span></a>
    <ul class="nav-links">
      <li><a href="/blog/">コラム</a></li>
      <li><a href="/blog/?cat=jigyo">事業承継</a></li>
      <li><a href="/blog/?cat=career">転職ガイド</a></li>
      <li><a href="/ma-tool.html">無料ツール</a></li>
      <li><a href="/#contact" class="nav-cta">無料相談</a></li>
    </ul>
  </div>
</nav>

<div style="padding:12px 24px;font-size:0.78rem;color:var(--gray-400);border-bottom:1px solid var(--gray-200);">
  <div style="max-width:var(--max-w);margin:0 auto;">
    <a href="/" style="color:var(--gray-400);">JP NEXT</a> › <a href="/blog/" style="color:var(--gray-400);">コラム</a> › {article['h1'][:30]}...
  </div>
</div>

<div class="article-header">
  <div class="article-header-inner">
    <div class="article-meta">
      <span class="tag {tag_class}">{article.get('category','事業承継')}</span>
      <span>{date_display}</span>
      <span>読了 {article.get('read_time', 5)}分</span>
    </div>
    <h1 class="article-title">{article['h1']}</h1>
    <p class="article-lead">{article['lead']}</p>
  </div>
</div>

<div class="article-layout">
  <article class="article-content">
    <nav class="toc">
      <div class="toc-title">目次</div>
      <ol>{toc_items}</ol>
    </nav>
    {sections_html}
    <h2>まとめ</h2>
    <p>{article['summary']}</p>

    <div class="source-box">
      出典：<a href="{news_item['url']}" target="_blank" rel="noopener">{news_item['company']} 適時開示（{date_display}）</a>
    </div>

    <div style="background:var(--black);border-radius:12px;padding:28px;text-align:center;margin:40px 0;">
      <div style="font-family:var(--font-serif);font-size:1.2rem;color:var(--white);margin-bottom:10px;">自社の企業価値を無料で算定する</div>
      <p style="font-size:0.82rem;color:var(--gray-600);margin-bottom:20px;">登録不要。売上・利益を入力するだけで即時算定。</p>
      <a href="/ma-tool.html" class="btn btn-primary" style="display:inline-block;">企業価値算定ツールを使う →</a>
    </div>
  </article>

  <aside class="sidebar-sticky">
    <div class="sw">
      <div class="sw-head">無料ツール</div>
      <div class="sw-body">
        <p>企業価値を今すぐ無料で算定。登録不要・3手法対応。</p>
        <a href="/ma-tool.html" class="btn btn-primary" style="display:block;text-align:center;margin-top:12px;padding:10px;">ツールを使う →</a>
      </div>
    </div>
    <div class="sw">
      <div class="sw-head">関連記事</div>
      <div class="sw-body">
        <ul style="list-style:none;display:flex;flex-direction:column;gap:10px;">
          <li><a href="/blog/kigyokachi-santei.html" style="color:var(--accent);font-size:0.82rem;">企業価値算定の3手法を解説</a></li>
          <li><a href="/blog/chukaisya-hikaku.html" style="color:var(--accent);font-size:0.82rem;">M&A仲介会社比較</a></li>
          <li><a href="/blog/jigyo-shokei.html" style="color:var(--accent);font-size:0.82rem;">事業承継のプロセス</a></li>
        </ul>
      </div>
    </div>
  </aside>
</div>

<footer class="footer">
  <div class="footer-inner">
    <div class="footer-logo">JP <span>NEXT</span></div>
    <div class="footer-tagline">M&A・事業承継の実務情報メディア</div>
    <div class="footer-bottom" style="margin-top:32px;">
      <span>© 2025 JP NEXT. All rights reserved.</span>
      <a href="/" style="color:var(--gray-600);">トップへ戻る</a>
    </div>
  </div>
</footer>
</body>
</html>"""


# ── blog/index.html の記事一覧を更新 ─────────────────
def update_blog_index(new_articles):
    """blog/index.htmlの記事一覧に新記事を追加"""
    index_path = os.path.join(BLOG_DIR, "index.html")
    if not os.path.exists(index_path):
        print(f"blog/index.html が見つかりません: {index_path}")
        return

    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    insert_marker = '<div class="articles-list" id="articleList">'
    if insert_marker not in html:
        print("articles-list マーカーが見つかりません")
        return

    for news_item, article, slug in reversed(new_articles):
        date_display = f"{news_item['date'][:4]}.{news_item['date'][4:6]}.{news_item['date'][6:]}"
        cat = article.get("category", "事業承継")
        tag_class = "tag-transfer" if cat == "転職" else "tag-ma"
        thumb_bg = {"事業承継": "var(--accent)", "M&A仲介": "var(--black)", "転職": "var(--gold)"}.get(cat, "var(--accent)")
        data_cat = {"事業承継": "jigyo", "M&A仲介": "ma", "転職": "career"}.get(cat, "jigyo")

        new_card = f"""
      <article class="article-row" data-cat="{data_cat}">
        <div class="article-row-thumb" style="background:{thumb_bg};">
          <span style="font-family:var(--font-serif);font-size:0.9rem;color:rgba(255,255,255,0.9);letter-spacing:0.1em;">{cat}</span>
        </div>
        <div class="article-row-body">
          <div class="article-row-meta">
            <span class="tag {tag_class}">{cat}</span>
            <span>{date_display}</span>
            <span>読了 {article.get('read_time', 5)}分</span>
          </div>
          <h2 class="article-row-title">
            <a href="/blog/{slug}.html">{article['h1']}</a>
          </h2>
          <p class="article-row-excerpt">{article['lead'][:80]}...</p>
        </div>
      </article>"""

        html = html.replace(insert_marker, insert_marker + new_card)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"blog/index.html を更新しました（{len(new_articles)}記事追加）")


# ── メイン処理 ────────────────────────────────────────
def main():
    print(f"=== JP NEXT 自動記事生成 {jst_now().strftime('%Y-%m-%d %H:%M')} ===")

    posted_data = load_posted()
    posted_ids  = set(posted_data.get("posted_ids", []))

    # TDnetからニュース取得
    news_items = fetch_tdnet_news()
    print(f"M&A関連ニュース: {len(news_items)}件")

    if not news_items:
        print("新着ニュースなし。終了します。")
        return

    # 未投稿のみフィルタ
    new_items = [n for n in news_items if n["id"] not in posted_ids]
    print(f"未投稿: {len(new_items)}件")

    if not new_items:
        print("新規投稿対象なし。終了します。")
        return

    new_items = new_items[:MAX_ARTICLES_PER_RUN]
    os.makedirs(BLOG_DIR, exist_ok=True)

    generated = []
    for news_item in new_items:
        print(f"\n記事生成中: {news_item['company']} — {news_item['title'][:40]}...")
        try:
            article = generate_article_content(news_item)
            slug    = f"tdnet-{news_item['date']}-{slugify(news_item['title'])}"
            html    = build_article_html(news_item, article, slug)

            filepath = os.path.join(BLOG_DIR, f"{slug}.html")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)

            posted_ids.add(news_item["id"])
            generated.append((news_item, article, slug))
            print(f"  → 生成完了: {filepath}")

        except Exception as e:
            print(f"  → 生成失敗: {e}")

    if generated:
        update_blog_index(generated)
        posted_data["posted_ids"] = list(posted_ids)
        save_posted(posted_data)
        print(f"\n完了: {len(generated)}記事を生成しました")
    else:
        print("\n生成できた記事がありませんでした")


if __name__ == "__main__":
    main()
