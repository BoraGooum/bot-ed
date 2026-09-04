#!/usr/bin/env python3
"""
Bot de veille EditionCollector.fr -> Telegram

Surveille DEUX pages :
1. /collectors   -> nouvelles fiches   : titre, photos (album max 10), type, prix marchands (liens), lien fiche, hashtag
2. /bons-plans   -> nouvelles promos   : titre, photo, type, prix barré/promo (+lien marchand), lien fiche, hashtag

Compare avec les listes déjà connues (seen_articles.json / seen_promos.json).
"""

import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://editioncollector.fr"
LISTING_URL = f"{BASE_URL}/collectors"
BONS_PLANS_URL = f"{BASE_URL}/bons-plans"
TEST_ARTICLE_URL = f"{BASE_URL}/collectors/alan-wake-design-works-deluxe-edition"

PIRATE_GIF_URL = "https://c.tenor.com/VmUFY5_WKUEAAAAd/tenor.gif"

SEEN_FILE = Path(__file__).parent / "seen_articles.json"
SEEN_PROMOS_FILE = Path(__file__).parent / "seen_promos.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TRIGGER_EVENT = os.environ.get("TRIGGER_EVENT", "")  # défini par le workflow GitHub

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Univers du site -> hashtag Telegram
HASHTAG_MAP = {
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
# Utilitaires communs
# --------------------------------------------------------------------------

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
        return ""
    tag = HASHTAG_MAP.get(univers.strip().lower())
    if tag:
        return tag
    fallback = re.sub(r"[^A-Za-zÀ-ÿ0-9]", "_", univers.strip())
    return f"#{fallback}"


def send_telegram_message(text: str):
    """Envoie un message texte simple sur Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(api_url, data=payload, timeout=20)
    if not resp.ok:
        print(f"❌ Erreur Telegram message ({resp.status_code}): {resp.text}")


def send_telegram_animation(caption: str, gif_url: str):
    """Envoie une animation/GIF avec légende sur Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAnimation"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": caption,
        "animation": gif_url,
    }
    resp = requests.post(api_url, data=payload, timeout=20)
    if not resp.ok:
        print(f"❌ Erreur Telegram animation ({resp.status_code}): {resp.text}")


def send_telegram_media_group(text: str, photo_urls: list[str]):
    """Envoie des images sous forme d'album Telegram (max 10) avec secours en photo unique."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants, message non envoyé :")
        print(text)
        return

    # Nettoyage strict et restriction aux 10 premières photos maximum
    clean_urls = []
    for u in photo_urls:
        if u and u.startswith("http") and not u.startswith("data:"):
            cleaned = u.split("&")[0].split('"')[0].split("'")[0]
            if cleaned not in clean_urls:
                clean_urls.append(cleaned)

    clean_urls = clean_urls[:10]  # Limite stricte Telegram de 10 médias

    # Si pas d'image valide, envoi simple
    if not clean_urls:
        send_telegram_message(text)
        return

    # Si 1 seule photo
    if len(clean_urls) == 1:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": text[:1024],
            "parse_mode": "HTML",
            "photo": clean_urls[0],
        }
        resp = requests.post(api_url, data=payload, timeout=20)
        resp.raise_for_status()
        return

    # Si album photo (2 à 10 photos)
    media = []
    for i, url in enumerate(clean_urls):
        item = {"type": "photo", "media": url}
        if i == 0:
            item["caption"] = text[:1024]
            item["parse_mode"] = "HTML"
        media.append(item)

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "media": json.dumps(media),
    }

    resp = requests.post(api_url, data=payload, timeout=20)

    # Secours : si Telegram refuse l'album, bascule sur la 1ère photo
    if not resp.ok:
        print(f"⚠️ Album refusé par Telegram ({resp.status_code}). Tentative avec 1 seule photo de secours...")
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": text[:1024],
            "parse_mode": "HTML",
            "photo": clean_urls[0],
        }
        fallback_resp = requests.post(api_url, data=payload, timeout=20)
        fallback_resp.raise_for_status()
        return

    resp.raise_for_status()


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
# /collectors -> nouveaux articles
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

    title_tag = soup.find("meta", property="og:title")
    title = title_tag["content"].strip() if title_tag else soup.title.get_text(strip=True)

    s3_matches = re.findall(
        r'https://edition-collector-production\.s3\.amazonaws\.com/uploads/image/file/[^\s"\'<>]+',
        resp.text,
    )

    images = []
    seen_imgs = set()
    for img_url in s3_matches:
        cleaned = img_url.split("&")[0].split('"')[0].split("'")[0]
        if cleaned not in seen_imgs:
            seen_imgs.add(cleaned)
            images.append(cleaned)
            if len(images) == 10:  # Extraction limitée à 10 images max
                break

    if not images:
        image_tag = soup.find("meta", property="og:image")
        if image_tag and image_tag.get("content"):
            candidate = image_tag["content"].strip()
            if "logo" not in candidate.lower() and "favicon" not in candidate.lower():
                images.append(candidate)

    page_text = soup.get_text("\n", strip=True)

    univers = None
    m = re.search(r"Univers\s*:\s*([^\n]+)", page_text)
    if m:
        univers = m.group(1).strip()

    merchants = []
    seen_names = set()
    price_pattern = re.compile(r"^([A-Za-zÀ-ÿ'’\.\s]+?)\s+([\d]+[.,]\d{2})\s*€", re.UNICODE)
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        match = price_pattern.match(text)
        if not match:
            continue
        name = match.group(1).strip()
        if name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        merchants.append({"name": name, "price": match.group(2).strip(), "url": a["href"]})

    return {
        "url": url,
        "title": title,
        "images": images,
        "univers": univers,
        "merchants": merchants,
    }


def format_article_message(article: dict) -> str:
    lines = ["🆕 • <b>Nouvel Article !</b>", ""]
    lines.append(f"<b>{escape_html(article['title'])}</b>")
    if article["univers"]:
        lines.append(f"Type : {escape_html(article['univers'])}")

    if article["merchants"]:
        lines.append("")
        lines.append("💰 <b>Prix :</b>")
        for m in article["merchants"]:
            lines.append(f"• <a href=\"{m['url']}\">{escape_html(m['name'])} — {m['price']}€</a>")

    lines.append("")
    lines.append(f"🔗 <a href=\"{article['url']}\">Voir sur Édition Collector</a>")

    hashtag = build_hashtag(article["univers"])
    if hashtag:
        lines.append("")
        lines.append(hashtag)

    return "\n".join(lines)


def check_collectors() -> int:
    latest_links = get_latest_article_links()
    print(f"[collectors] {len(latest_links)} liens trouvés sur la page.")

    seen = load_seen(SEEN_FILE)
    first_run = len(seen) == 0

    if first_run:
        print(f"[collectors] Premier lancement : {len(latest_links)} fiches enregistrées.")
        save_seen(SEEN_FILE, latest_links)
        return 0

    if not latest_links:
        print("[collectors] Aucun article trouvé.")
        return 0

    new_links = [url for url in latest_links if url not in seen]

    if not new_links:
        print("[collectors] Aucun nouvel article.")
        return 0

    count = 0
    for url in reversed(new_links):
        try:
            article = parse_article(url)
            send_telegram_media_group(format_article_message(article), article["images"])
            print(f"✅ [collectors] Notifié : {article['title']}")
            count += 1
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [collectors] Erreur sur {url}: {exc}")

    updated = latest_links + [u for u in seen if u not in latest_links]
    save_seen(SEEN_FILE, updated)
    return count


# --------------------------------------------------------------------------
# /bons-plans -> nouvelles promos
# --------------------------------------------------------------------------

PROMO_PATTERN = re.compile(
    r"(.+?)\s+est en promo\s*-(\d+)%\s*([\d]+[.,]\d{2})\s*€\s*([\d]+[.,]\d{2})\s*€",
    re.UNICODE,
)


def get_latest_promos_raw() -> list[dict]:
    soup = fetch(BONS_PLANS_URL)
    candidates = [a for a in soup.find_all("a", href=True) if normalize_href(a["href"], "bons-plans")]
    print(f"[bons-plans] {len(candidates)} liens '/bons-plans/...' bruts trouvés sur la page.")

    promos, seen_urls = [], set()

    for a in candidates:
        rel = normalize_href(a["href"], "bons-plans")
        text = a.get_text(" ", strip=True)
        text = re.sub(r"^(NEW|MAJ)\s+", "", text)
        text = re.sub(r"\s+(NEW|MAJ)$", "", text)

        match = PROMO_PATTERN.search(text)
        if not match:
            continue

        url = BASE_URL + rel
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title, discount, promo_price, original_price = match.groups()

        image_url = None
        for img_link in soup.find_all("a", href=a["href"]):
            img_tag = img_link.find("img")
            if img_tag:
                image_url = img_tag.get("src") or img_tag.get("data-src")
                if image_url and not image_url.startswith("data:"):
                    break

        merchant_name, merchant_url = None, None
        next_link = a.find_next("a", href=True)
        if next_link and "edcol.fr" in next_link.get("href", ""):
            merchant_name = next_link.get_text(strip=True)
            merchant_url = next_link["href"]

        promos.append({
            "url": url,
            "title": title.strip(),
            "discount": discount,
            "promo_price": promo_price,
            "original_price": original_price,
            "images": [image_url] if image_url else [],
            "merchant_name": merchant_name,
            "merchant_url": merchant_url,
        })

    return promos


def get_univers_for_promo(promo_url: str) -> str | None:
    try:
        soup = fetch(promo_url)
    except Exception:  # noqa: BLE001
        return None

    page_text = soup.get_text("\n", strip=True)
    m = re.search(r"Univers\s*:\s*([^\n]+)", page_text)
    if m:
        return m.group(1).strip()

    for a in soup.find_all("a", href=True):
        rel = normalize_href(a["href"], "collectors")
        if not rel:
            continue
        try:
            sub_soup = fetch(BASE_URL + rel)
        except Exception:  # noqa: BLE001
            continue
        sub_text = sub_soup.get_text("\n", strip=True)
        m2 = re.search(r"Univers\s*:\s*([^\n]+)", sub_text)
        if m2:
            return m2.group(1).strip()
        break

    return None


def format_promo_message(promo: dict) -> str:
    lines = ["💸 • <b>Bon plan !</b>", ""]
    lines.append(f"<b>{escape_html(promo['title'])}</b>")
    if promo.get("univers"):
        lines.append(f"Type : {escape_html(promo['univers'])}")

    lines.append("")
    price_line = f"<s>{promo['original_price']}€</s> ➜ <b>{promo['promo_price']}€</b> (-{promo['discount']}%)"
    if promo["merchant_name"] and promo["merchant_url"]:
        lines.append(f"💰 <a href=\"{promo['merchant_url']}\">{price_line} chez {escape_html(promo['merchant_name'])}</a>")
    else:
        lines.append(f"💰 {price_line}")

    lines.append("")
    lines.append(f"🔗 <a href=\"{promo['url']}\">Voir sur Édition Collector</a>")

    hashtag = build_hashtag(promo.get("univers"))
    if hashtag:
        lines.append("")
        lines.append(hashtag)

    return "\n".join(lines)


def check_bons_plans() -> int:
    latest_promos = get_latest_promos_raw()
    print(f"[bons-plans] {len(latest_promos)} promos parsées avec succès.")

    seen = load_seen(SEEN_PROMOS_FILE)
    first_run = len(seen) == 0

    if first_run:
        latest_urls = [p["url"] for p in latest_promos] if latest_promos else []
        print(f"[bons-plans] Premier lancement : {len(latest_urls)} promos enregistrées.")
        save_seen(SEEN_PROMOS_FILE, latest_urls)
        return 0

    if not latest_promos:
        print("[bons-plans] Aucune promo trouvée.")
        return 0

    latest_urls = [p["url"] for p in latest_promos]
    new_promos = [p for p in latest_promos if p["url"] not in seen]

    if not new_promos:
        print("[bons-plans] Aucune nouvelle promo.")
        return 0

    count = 0
    for promo in reversed(new_promos):
        try:
            promo["univers"] = get_univers_for_promo(promo["url"])
            send_telegram_media_group(format_promo_message(promo), promo["images"])
            print(f"✅ [bons-plans] Notifié : {promo['title']}")
            count += 1
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [bons-plans] Erreur sur {promo['url']}: {exc}")

    updated = latest_urls + [u for u in seen if u not in latest_urls]
    save_seen(SEEN_PROMOS_FILE, updated)
    return count


# --------------------------------------------------------------------------
# Test manuel (workflow_dispatch)
# --------------------------------------------------------------------------

def send_test_message():
    if TRIGGER_EVENT != "workflow_dispatch":
        return

    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y à %H:%M")

    print("🔎 Lancement manuel détecté : test sur la fiche Alan Wake...")

    # 1. Message de confirmation d'exécution du workflow
    status_msg = f"✅ • Workflow réussi, bot opérationnel ! ({now})"
    send_telegram_message(status_msg)

    # 2. Envoi de l'aperçu complet de la fiche Alan Wake
    try:
        alan_wake_article = parse_article(TEST_ARTICLE_URL)
        send_telegram_media_group(format_article_message(alan_wake_article), alan_wake_article["images"])
        print(f"✅ Test envoyé avec succès ({len(alan_wake_article['images'])} visuel(s) Alan Wake).")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Impossible d'envoyer le test Alan Wake : {exc}")


def main():
    send_test_message()
    new_articles_count = check_collectors()
    new_promos_count = check_bons_plans()

    # Message pirate automatique si le cron tourne et qu'il n'y a aucune nouveauté
    if TRIGGER_EVENT != "workflow_dispatch" and new_articles_count == 0 and new_promos_count == 0:
        if SEEN_FILE.exists() and SEEN_PROMOS_FILE.exists():
            send_telegram_animation("🏴‍☠️ • Pas de promo en vue moussaillon...", PIRATE_GIF_URL)


if __name__ == "__main__":
    main()
