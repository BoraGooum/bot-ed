#!/usr/bin/env python3
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://editioncollector.fr"
LISTING_URL = f"{BASE_URL}/collectors"
URL_ALAN_WAKE = "https://editioncollector.fr/collectors/alan-wake-design-works-deluxe-edition"

SEEN_FILE = Path(__file__).parent / "seen_articles.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", os.environ.get("TRIGGER_EVENT", ""))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

HASHTAG_MAP = {
    "artbook": "#Artbook",
    "jeux vidéo": "#JV",
    "films/séries": "#Films_Séries",
    "livres": "#Livres",
    "musique": "#Musiques",
    "goodies": "#Goodies",
    "figurines": "#Figurines",
    "accessoires": "#Accessoires",
    "jouets": "#Jouets",
    "print": "#Print",
    "pin's": "#Pins",
}

# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def obtenir_heure_paris():
    fuseau_paris = ZoneInfo("Europe/Paris")
    return datetime.now(fuseau_paris).strftime("%d/%m/%Y à %H:%M")

def mettre_a_jour_pointeuse():
    try:
        maintenant = obtenir_heure_paris()
        ligne = f"- Corvée {'manuelle ' if EVENT_NAME == 'workflow_dispatch' else ''}effectuée à : {maintenant}\n"
        with open("pointeuse.txt", "a", encoding="utf-8") as f:
            f.write(ligne)
    except Exception as e:
        print(f"Erreur écriture pointeuse : {e}")

def fetch_raw(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp

def fetch(url: str) -> BeautifulSoup:
    resp = fetch_raw(url)
    return BeautifulSoup(resp.text, "html.parser")

def normalize_href(href: str, prefix: str) -> str | None:
    href = href.strip()
    href = re.sub(r"^https?://editioncollector\.fr", "", href)
    m = re.match(rf"^/{prefix}/([a-z0-9\-]+)/?$", href)
    if not m:
        return None
    return f"/{prefix}/{m.group(1)}"

def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def build_hashtag(univers: str | None) -> str:
    if not univers:
        return "#Collector"
    tag = HASHTAG_MAP.get(univers.strip().lower())
    if tag:
        return tag
    fallback = re.sub(r"[^A-Za-zÀ-ÿ0-9]", "_", univers.strip())
    return f"#{fallback}"

def send_telegram_message(text: str, photo_url: str | None, silent: bool = True):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants.")
        return

    if photo_url and not photo_url.startswith("http"):
        photo_url = BASE_URL + (photo_url if photo_url.startswith("/") else "/" + photo_url)

    if photo_url:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": text[:1024],
            "parse_mode": "HTML",
            "photo": photo_url,
            "disable_notification": silent
        }
    else:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": silent
        }

    try:
        resp = requests.post(api_url, data=payload, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Erreur Telegram : {e}")

def load_seen(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

def save_seen(path: Path, urls: list[str]):
    path.write_text(json.dumps(urls[:500], ensure_ascii=False, indent=2), encoding="utf-8")

# --------------------------------------------------------------------------
# Parsing des articles / collectors
# --------------------------------------------------------------------------

def get_latest_article_links() -> list[str]:
    soup = fetch(LISTING_URL)
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        rel = normalize_href(a["href"], "collectors")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        links.append(BASE_URL + rel)
    return links

def parse_article(url: str) -> dict:
    resp = fetch_raw(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("h1") or soup.find("h2", class_="entry-title") or soup.find("meta", property="og:title")
    if title_tag:
        title = title_tag.get_text(strip=True) if hasattr(title_tag, "get_text") else title_tag.get("content", "").strip()
    else:
        title = "Article"

    # Extraction d'image (S3 -> figure -> lazy -> og:image)
    image_url = None
    s3_matches = re.findall(
        r'https://edition-collector-production\.s3\.amazonaws\.com/uploads/image/file/[^\s"\'<>]+',
        resp.text,
    )
    if s3_matches:
        image_url = s3_matches[0]

    if not image_url:
        figure_img = soup.select_one("figure img") or soup.find("img")
        if figure_img:
            image_url = figure_img.get("data-lazy-src") or figure_img.get("data-src") or figure_img.get("src")

    if not image_url or image_url.startswith("data:"):
        image_tag = soup.find("meta", property="og:image")
        if image_tag and image_tag.get("content"):
            candidate = image_tag["content"].strip()
            if "logo" not in candidate.lower() and "favicon" not in candidate.lower():
                image_url = candidate

    page_text = soup.get_text("\n", strip=True)

    # Détection du type
    if "artbook" in title.lower() or "design works" in title.lower() or "artbook" in page_text.lower():
        univers = "Artbook"
    else:
        m = re.search(r"Univers\s*:\s*([^\n]+)", page_text)
        univers = m.group(1).strip() if m else None

    # Traitement des marchands (Dispo FR / Import)
    dispo_fr, dispo_import = [], []
    seen_names = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        
        if "/out/" in href or any(m in href.lower() for m in ["lostincult", "amazon", "fnac", "micromania", "leclerc"]):
            clean_name = re.sub(r'\d+[\.,]?\d*\s*[€$£]', '', text).strip()
            if not clean_name:
                clean_name = "Lien marchand"

            if clean_name.lower() in seen_names:
                continue
            seen_names.add(clean_name.lower())

            parent_txt = a.parent.get_text(strip=True) if a.parent else text
            prix_trouves = re.findall(r'(\d+[\.,]?\d*\s*[€$£])', parent_txt)
            prix_str = f" <b>{prix_trouves[0]}</b>" if prix_trouves else ""

            lien_html = f'<a href="{href}">{escape_html(clean_name)}</a>{prix_str}'

            if "lostincult" in href.lower() or "uk" in href.lower() or "£" in parent_txt:
                dispo_import.append(f"- {lien_html}")
            else:
                dispo_fr.append(f"- {lien_html}")

    return {
        "url": url,
        "title": title,
        "image": image_url,
        "univers": univers,
        "dispo_fr": dispo_fr,
        "dispo_import": dispo_import,
    }

def format_article_message(article: dict) -> str:
    lines = [f"🆕 • <b>{escape_html(article['title'])}</b>", ""]
    
    if article["univers"]:
        lines.append(f"• <b>Type :</b> {escape_html(article['univers'])}\n")

    lines.append("• <b>Disponibilités France :</b>")
    if article["dispo_fr"]:
        lines.extend(article["dispo_fr"])
    else:
        lines.append("- bientôt ?")

    lines.append("\n• <b>Disponibilité import :</b>")
    if article["dispo_import"]:
        lines.extend(article["dispo_import"])
    else:
        lines.append("- Aucune pour le moment")

    lines.append("")
    lines.append(f"🔗 <a href=\"{article['url']}\">Voir sur Édition Collector</a>\n")

    hashtag = build_hashtag(article["univers"])
    if hashtag:
        lines.append(hashtag)

    return "\n".join(lines)

def check_collectors():
    latest_links = get_latest_article_links()
    seen = load_seen(SEEN_FILE)

    # Initialisation si le fichier est vide
    if not seen:
        save_seen(SEEN_FILE, latest_links)
        return

    # Récupération de TOUS les nouveaux articles non vus, du plus ancien au plus récent
    new_links = [url for url in latest_links if url not in seen]
    for url in reversed(new_links):
        try:
            article = parse_article(url)
            send_telegram_message(format_article_message(article), article["image"], silent=True)
            time.sleep(1)
        except Exception as exc:
            print(f"❌ Error {url}: {exc}")

    # Sauvegarde de la liste mise à jour pour ne pas les retraiter
    updated = latest_links + [u for u in seen if u not in latest_links]
    save_seen(SEEN_FILE, updated)

# --------------------------------------------------------------------------
# Notification manuelle (workflow_dispatch)
# --------------------------------------------------------------------------

def send_test_message():
    if EVENT_NAME != "workflow_dispatch":
        return

    now = obtenir_heure_paris()
    send_telegram_message(f"✅ • Workflow réussi, bot opérationnel ({now})", None, silent=False)

    try:
        last_article = parse_article(URL_ALAN_WAKE)
        send_telegram_message(format_article_message(last_article), last_article["image"], silent=True)
    except Exception as exc:
        print(f"⚠️ Impossible d'envoyer l'aperçu Alan Wake : {exc}")

def main():
    mettre_a_jour_pointeuse()
    send_test_message()
    check_collectors()

if __name__ == "__main__":
    main()
