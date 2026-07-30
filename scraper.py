import os
import json
import datetime
import time
import random
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

GAS_API_URL = os.environ.get("GAS_API_URL")
GAS_PASSWORD = os.environ.get("GAS_PASSWORD")
STATE_FILE = "last_state.json"
HTML_FILE = "index.html"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fetch_configs():
    print("📡 スプレッドシート(GAS API)から設定を取得中...")
    resp = requests.get(GAS_API_URL, timeout=15)
    resp.raise_for_status()
    return resp.json()

def send_email_via_gas(email, subject, html_body):
    payload = {"email": email, "subject": subject, "htmlBody": html_body, "password": GAS_PASSWORD}
    requests.post(GAS_API_URL, json=payload, timeout=15)

def parse_date(date_str):
    cleaned = re.sub(r'[^\d/.-]', '', date_str.replace('年', '/').replace('月', '/').replace('日', ''))
    for fmt in ('%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d'):
        try:
            return datetime.datetime.strptime(cleaned, fmt)
        except ValueError:
            pass
    return None

# --- セレクタのサニタイズ（全角クォートの半角化、余計な空白・改行の削除） ---
def clean_selector(sel):
    if not sel:
        return None
    # 全角クォートを半角にし、前後の空白・改行を除去
    return sel.replace('”', '"').replace('“', '"').replace('’', "'").strip()

def main():
    configs = fetch_configs()
    last_state = load_state()
    new_state = {}
    all_sites_data = []

    # ✨ Playwright の起動
    with sync_playwright() as p:
        # ヘッドレスモード（画面なし）で軽量起動
        browser = p.chromium.launch(headless=True)
        # ユーザーエージェントを一般的なブラウザに偽装
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = context.new_page()

        for config in configs:
            site_name = config["siteName"]
            site_url = config["siteUrl"]

            # 設定から取得する際に clean_selector を通す
            category_sel = clean_selector(config.get("categorySelector"))
            category_name_sel = clean_selector(config.get("categoryNameSelector"))
            item_sel = clean_selector(config.get("itemSelector"))
            date_sel = clean_selector(config.get("dateSelector"))
            content_sel = clean_selector(config.get("contentSelector"))
            
            display_days = config.get("displayDays", 30)
            email = config.get("email")

            print(f"\n🔍 解析中: [{site_name}] ({site_url})")
            
            try:
                # サーバー負荷軽減のためのランダムウェイト (1.5〜3.5秒)
                time.sleep(random.uniform(1.5, 3.5))

                # ページへアクセス（JSのネットワーク通信が落ち着く 'networkidle' まで待機）
                page.goto(site_url, wait_until="networkidle", timeout=60000)
                
                # 遅延読み込み（Lazy Load）対策の段階的スクロール
                for _ in range(3):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(1000)

                # JS実行後の「完成形HTML」を取得
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                categories = soup.select(category_sel) if category_sel else [soup]
                site_parsed_data = {}
                new_items_for_mail = {}

                for cat_block in categories:
                    cat_name = "お知らせ"
                    if category_name_sel:
                        name_el = cat_block.select_one(category_name_sel)
                        if name_el:
                            cat_name = name_el.get_text(strip=True)

                    items = cat_block.select(item_sel)
                    parsed_items = []

                    for item in items:
                        date_el = item.select_one(date_sel)
                        content_el = item.select_one(content_sel)

                        if date_el and content_el:
                            date_text = date_el.get_text(strip=True)
                            date_obj = parse_date(date_text)

                            # 相対パスを絶対パスに変換
                            for a in content_el.find_all("a", href=True):
                                a["href"] = requests.compat.urljoin(site_url, a["href"])
                                a["target"] = "_blank"
                                a["rel"] = "noopener noreferrer"

                            item_id = f"{date_text}_{content_el.get_text(strip=True)[:30]}"
                            
                            parsed_items.append({
                                "id": item_id,
                                "dateObj": date_obj,
                                "htmlContent": f"<dt>{date_text}</dt><dd>{content_el.decode_contents()}</dd>"
                            })

                    if parsed_items:
                        site_parsed_data[cat_name] = parsed_items

                if not site_parsed_data:
                    print("⚠️ 指定された要素が見つかりませんでした。")
                    continue

                # 差分（新着）チェック処理
                site_new_state = {}
                prev_site_state = last_state.get(site_name, {})

                for cat_name, items in site_parsed_data.items():
                    site_new_state[cat_name] = items[0]["id"]
                    prev_top_id = prev_site_state.get(cat_name)
                    
                    cat_new_items = []
                    if prev_top_id:
                        for item in items:
                            if item["id"] == prev_top_id:
                                break
                            cat_new_items.append(item)
                    
                    if cat_new_items:
                        new_items_for_mail[cat_name] = cat_new_items

                new_state[site_name] = site_new_state

                # 新着メール通知リクエスト
                if new_items_for_mail and email:
                    mail_html = f"<h2>【{site_name}】新着のお知らせ</h2>"
                    for cat_name, items in new_items_for_mail.items():
                        mail_html += f"<h3>■ {cat_name}</h3><dl>"
                        for item in items:
                            mail_html += item["htmlContent"]
                        mail_html += "</dl><hr>"
                    mail_html += f"<p><a href='{site_url}'>サイトを確認する</a></p>"
                    send_email_via_gas(email, f"【新着通知】{site_name}", mail_html)

                # GitHub Pages用データ追加
                all_sites_data.append({
                    "siteName": site_name,
                    "siteUrl": site_url,
                    "displayDays": display_days,
                    "groupedData": site_parsed_data
                })

            except Exception as e:
                print(f"❌ エラー発生 [{site_name}]: {e}")

        # ブラウザを閉じる
        browser.close()

    save_state(new_state)
    generate_github_pages_html(all_sites_data)

# HTML生成関数は前回と全く同じなのでそのまま配置します
def generate_github_pages_html(sites_data):
    now = datetime.datetime.now()
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>お知らせ抽出結果</title>
    <style>
        body {{ font-family: sans-serif; padding: 20px; background-color: #f4f7f9; }}
        .site-section {{ margin-bottom: 30px; background: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
        .site-title {{ font-size: 1.3rem; border-bottom: 2px solid #0d6efd; padding-bottom: 5px; color: #0d6efd; text-decoration: none; display: block; margin-bottom: 15px; }}
        .cat-details {{ border: 1px solid #ccc; padding: 10px 15px; border-radius: 5px; margin-bottom: 15px; }}
        .cat-summary {{ font-size: 1.1rem; font-weight: bold; color: #1b365d; cursor: pointer; }}
        dl {{ margin: 10px 0 0 0; }}
        dt {{ font-weight: bold; color: #333; margin-top: 10px; border-top: 1px dashed #ddd; padding-top: 10px; }}
        dt:first-child {{ border-top: none; padding-top: 0; }}
        dd {{ margin-left: 0; padding-bottom: 5px; color: #212529; }}
    </style>
</head>
<body>
    <h1>📢 新着一覧</h1>
    <p style="color: #666; font-size: 0.9rem;">最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}</p>
"""
    for site in sites_data:
        threshold = now - datetime.timedelta(days=site["displayDays"])
        html += f'<div class="site-section"><a href="{site["siteUrl"]}" target="_blank" class="site-title">{site["siteName"]} (過去{site["displayDays"]}日分)</a>'
        
        has_recent = False
        for cat_name, items in site["groupedData"].items():
            recent_items = [i for i in items if i["dateObj"] and i["dateObj"] >= threshold]
            
            if recent_items:
                has_recent = True
                html += f'<details class="cat-details" open><summary class="cat-summary">{cat_name}</summary><dl>'
                for item in recent_items:
                    html += item["htmlContent"]
                html += '</dl></details>'
        
        if not has_recent:
            html += '<p style="color:#777;">該当する期間のお知らせはありません。</p>'
            
        html += "</div>"

    html += "</body></html>"

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print("✨ GitHub Pages用 index.html を生成しました。")

if __name__ == "__main__":
    main()