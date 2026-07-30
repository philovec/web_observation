import os
import json
import datetime
import re
import requests
from bs4 import BeautifulSoup

GAS_API_URL = "https://script.google.com/macros/s/AKfycbwkWjy_TvfDcmcSQ-j1itySSmYztbkgLG7OkmVv5Rqgd2IcE-v-22uMV5-18975GRIm/exec"
GAS_PASSWORD = "mnnvfuoekvc~bsml"
# 環境変数からGASのデプロイURLを取得
#GAS_API_URL = os.environ.get("GAS_API_URL")
#GAS_PASSWORD = os.environ.get("GAS_PASSWORD")
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
    print(f"📧 メール送信実行: {email}")
    payload = {
        "password": GAS_PASSWORD,
        "email": email,
        "subject": subject,
        "htmlBody": html_body
    }
    requests.post(GAS_API_URL, json=payload, timeout=15)

def parse_date(date_str):
    # 年月日・ドット・ハイフン区切りを日付オブジェクトへ変換
    cleaned = re.sub(r'[^\d/.-]', '', date_str.replace('年', '/').replace('月', '/').replace('日', ''))
    for fmt in ('%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d'):
        try:
            return datetime.datetime.strptime(cleaned, fmt)
        except ValueError:
            pass
    return None

def main():
    configs = fetch_configs()
    last_state = load_state()
    new_state = {}
    all_sites_html_data = []

    for config in configs:
        site_name = config["siteName"]
        site_url = config["siteUrl"]
        block_sel = config["blockSelector"]
        date_sel = config["dateSelector"]
        content_sel = config["contentSelector"]
        display_days = config.get("displayDays", 30)
        email = config.get("email")

        print(f"\n🔍 解析中: [{site_name}] ({site_url})")
        
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            res = requests.get(site_url, headers=headers, timeout=15)
            res.encoding = res.apparent_encoding
            soup = BeautifulSoup(res.text, "html.parser")

            # 指定されたブロックを取得
            blocks = soup.select(block_sel)
            parsed_items = []

            for block in blocks:
                # ブロック内部から日付と本文要素をセレクタで個別に探す
                date_el = block.select_one(date_sel)
                content_el = block.select_one(content_sel)

                if date_el and content_el:
                    date_text = date_el.get_text(strip=True)
                    date_obj = parse_date(date_text)

                    # リンクを絶対パス化し、別タブ参照を付与
                    for a in content_el.find_all("a", href=True):
                        a["href"] = requests.compat.urljoin(site_url, a["href"])
                        a["target"] = "_blank"
                        a["rel"] = "noopener noreferrer"

                    # 識別用ユニークID
                    item_id = f"{date_text}_{content_el.get_text(strip=True)[:30]}"
                    
                    parsed_items.append({
                        "id": item_id,
                        "dateStr": date_text,
                        "dateObj": date_obj,
                        "htmlContent": f"<dt>{date_text}</dt><dd>{content_el.decode_contents()}</dd>"
                    })

            if not parsed_items:
                print("⚠️ 指定された要素が見つかりませんでした。")
                continue

            # 最新項目のIDを記憶
            top_item_id = parsed_items[0]["id"]
            new_state[site_name] = top_item_id

            # 新着差分の抽出
            prev_top_id = last_state.get(site_name)
            new_items = []

            if prev_top_id:
                for item in parsed_items:
                    if item["id"] == prev_top_id:
                        break
                    new_items.append(item)

            # 新着があればGASへメール送信リクエスト
            if new_items and email:
                mail_html = f"<h2>【{site_name}】新着のお知らせ</h2><dl>"
                for item in new_items:
                    mail_html += item["htmlContent"]
                mail_html += f"</dl><p><a href='{site_url}'>サイトを確認する</a></p>"
                
                send_email_via_gas(email, f"【新着通知】{site_name}", mail_html)

            # Webページ表示用データ
            all_sites_html_data.append({
                "siteName": site_name,
                "siteUrl": site_url,
                "displayDays": display_days,
                "items": parsed_items
            })

        except Exception as e:
            print(f"❌ エラー発生 [{site_name}]: {e}")

    # 状態の保存
    save_state(new_state)

    # GitHub Pages 用 index.html の生成
    generate_github_pages_html(all_sites_html_data)

def generate_github_pages_html(sites_data):
    now = datetime.datetime.now()
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>お知らせ一括確認</title>
    <style>
        body {{ font-family: sans-serif; margin: 20px; background: #f8f9fa; line-height: 1.6; }}
        .site-card {{ background: #fff; border: 1px solid #e9ecef; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
        .site-title {{ font-size: 1.2rem; margin-top: 0; border-bottom: 2px solid #0d6efd; padding-bottom: 8px; color: #0d6efd; }}
        dt {{ font-weight: bold; color: #495057; margin-top: 12px; }}
        dd {{ margin-left: 0; color: #212529; padding-bottom: 8px; border-bottom: 1px dashed #eee; }}
        a {{ color: #0d6efd; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h1>📢 新着お知らせ一覧</h1>
    <p>最終更新日時: {now.strftime('%Y-%m-%d %H:%M:%S')}</p>
"""
    for site in sites_data:
        html += f"""<div class="site-card">
            <h2 class="site-title"><a href="{site['siteUrl']}" target="_blank">{site['siteName']}</a></h2>
            <dl>"""
        threshold = now - datetime.timedelta(days=site["displayDays"])
        for item in site["items"]:
            if item["dateObj"] and item["dateObj"] < threshold:
                continue
            html += item["htmlContent"]
        html += "</dl></div>"

    html += "</body></html>"

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print("✨ GitHub Pages用 index.html を生成しました。")

if __name__ == "__main__":
    main()