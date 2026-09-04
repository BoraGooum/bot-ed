#!/usr/bin/env python3
"""
Bot de veille EditionCollector.fr -> Telegram

Surveille DEUX pages :
1. /collectors     -> nouvelles fiches (titre, photo, lien, prix Fnac/Amazon/Leclerc, hashtag univers)
2. /bons-plans     -> nouvelles promos (titre, photo, prix barré/promo, %, lien marchand)

Compare avec les listes déjà connues (seen_articles.json / seen_promos.json).

Au tout premier lancement de chaque flux (fichier seen absent ou vide), le script
enregistre juste l'état actuel SANS envoyer de notifications, pour éviter de
spammer avec tout l'historique déjà en ligne.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://editioncollector.fr"
LISTING_URL = f"{BASE_URL}/collectors"
BONS_PLANS_URL = f"{BASE_URL}/bons-plans"
SEEN_FILE = Path(__file__).parent / "seen_articles.json"
SEEN_PROMOS_FILE = Path(__file__).parent / "seen_promos.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Marchands à afficher (nom tel qu'affiché sur le site -> label dans le message)
MERCHANTS_WANTED = ["Fnac", "Amazon", "Leclerc"]

# Mapping univers du site -> hashtag demandé
HASHTAG_MAP = {
    "jeux vidéo": "#JV",
    "films/séries": "#Films-Series",
    "livres": "#Livres",
    "musique": "#Musiques",
    "goodies": "#Goodies",
    "bons plans": "#BonsPlans",
}


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_latest_article_links() -> list[str]:
    """Récupère les URLs des fiches sur la 1ère page de /collectors, dans l'ordre affiché."""
    soup = fetch(LISTING_URL)
    links = []
    seen = set()
    for a in soup.select("a[href^='/collectors/']"):
        href = a.get("href", "")
        # on ignore les liens de filtre type /collectors/univers/... ou /collectors/pre-commandes
        if not re.match(r"^/collectors/[a-z0-9\-]+$", href):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(BASE_URL + href)
    return links


def parse_article(url: str) -> dict:
    """Extrait titre, image, univers et prix marchands d'une fiche."""
    soup = fetch(url)

    title_tag = soup.find("meta", property="og:title")
    title = title_tag["content"].strip() if title_tag else soup.title.get_text(strip=True)

    image_tag = soup.find("meta", property="og:image")
    image_url = image_tag["content"].strip() if image_tag else None

    page_text = soup.get_text("\n", strip=True)

    # Univers (catégorie) : cherche "Univers :" suivi du nom
    univers = None
    m = re.search(r"Univers\s*:\s*([^\n]+)", page_text)
    if m:
        univers = m.group(1).strip()

    # Prix marchands : on cherche tous les liens dont le texte ressemble à
    # "NomMarchand XX,XX€"
    merchants = {}
    price_pattern = re.compile(r"^([A-Za-zÀ-ÿ'’\.\s]+?)\s+([\d]+[.,]\d{2})\s*€", re.UNICODE)
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        match = price_pattern.match(text)
        if not match:
            continue
        merchant_name = match.group(1).strip()
        price = match.group(2).strip()
        for wanted in MERCHANTS_WANTED:
            if wanted.lower() in merchant_name.lower():
                merchants.setdefault(wanted, {"price": price, "url": a["href"]})

    return {
        "url": url,
        "title": title,
        "image": image_url,
        "univers": univers,
        "merchants": merchants,
    }


def build_hashtags(univers: str | None) -> str:
    if not univers:
        return ""
    tag = HASHTAG_MAP.get(univers.strip().lower())
    if tag:
        return tag
    # fallback : on fabrique un hashtag à partir du nom de l'univers
    fallback = re.sub(r"[^A-Za-z0-9À-ÿ]", "", univers)
    return f"#{fallback}"


def format_message(article: dict) -> str:
    lines = [f"🆕 <b>{escape_html(article['title'])}</b>", ""]
    lines.append(f"🔗 <a href=\"{article['url']}\">Voir la fiche</a>")

    if article["merchants"]:
        lines.append("")
        lines.append("💰 <b>Prix marchands :</b>")
        for name in MERCHANTS_WANTED:
            info = article["merchants"].get(name)
            if info:
                lines.append(f"• <a href=\"{info['url']}\">{name} — {info['price']}€</a>")

    hashtag = build_hashtags(article["univers"])
    if hashtag:
        lines.append("")
        lines.append(hashtag)

    return "\n".join(lines)


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_telegram_message(text: str, photo_url: str | None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants, message non envoyé :")
        print(text)
        return

    if photo_url:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": text[:1024],  # limite Telegram pour les légendes
            "parse_mode": "HTML",
            "photo": photo_url,
        }
    else:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

    resp = requests.post(api_url, data=payload, timeout=20)
    if not resp.ok:
        print(f"❌ Erreur Telegram ({resp.status_code}): {resp.text}")
    resp.raise_for_status()


def load_seen(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_seen(path: Path, urls: list[str]):
    # on garde une liste raisonnable (les 500 derniers) pour ne pas faire grossir le fichier indéfiniment
    path.write_text(json.dumps(urls[:500], ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Bons plans (/bons-plans)
# --------------------------------------------------------------------------

PROMO_PATTERN = re.compile(
    r"^(.*?)\s+est en promo\s+-(\d+)%\s+([\d]+[.,]\d{2})\s*€\s+([\d]+[.,]\d{2})\s*€\s*$",
    re.UNICODE,
)


def get_latest_promos() -> list[dict]:
    """Récupère les promos de la 1ère page de /bons-plans, avec toutes les infos
    déjà présentes sur le listing (pas besoin d'aller sur chaque fiche)."""
    soup = fetch(BONS_PLANS_URL)
    promos = []
    seen_urls = set()

    for a in soup.select("a[href^='/bons-plans/']"):
        href = a.get("href", "")
        if not re.match(r"^/bons-plans/[a-z0-9\-]+$", href):
            continue
        text = a.get_text(" ", strip=True)
        match = PROMO_PATTERN.match(text)
        if not match:
            continue  # ce n'est pas le lien texte (probablement le lien image)

        url = BASE_URL + href
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title, discount, promo_price, original_price = match.groups()

        # image : cherche un <a href=même lien> contenant un <img>
        image_url = None
        for img_link in soup.select(f"a[href='{href}']"):
            img_tag = img_link.find("img")
            if img_tag and img_tag.get("src"):
                image_url = img_tag["src"]
                break

        # marchand : le lien externe (edcol.fr) juste après le lien texte de la promo
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
            "image": image_url,
            "merchant_name": merchant_name,
            "merchant_url": merchant_url,
        })

    return promos


def format_promo_message(promo: dict) -> str:
    lines = [
        "🔥 <b>EN PROMO !</b>",
        "",
        f"<b>{escape_html(promo['title'])}</b>",
        "",
        f"<s>{promo['original_price']}€</s> ➜ <b>{promo['promo_price']}€</b> (-{promo['discount']}%)",
    ]
    if promo["merchant_name"] and promo["merchant_url"]:
        lines.append(f"🛒 <a href=\"{promo['merchant_url']}\">Voir l'offre chez {escape_html(promo['merchant_name'])}</a>")
    lines.append(f"🔗 <a href=\"{promo['url']}\">Voir la fiche</a>")
    lines.append("")
    lines.append("#BonsPlans")
    return "\n".join(lines)


def check_collectors():
    latest_links = get_latest_article_links()
    if not latest_links:
        print("Aucun lien /collectors récupéré.")
        return

    seen = load_seen(SEEN_FILE)
    first_run = len(seen) == 0
    new_links = [url for url in latest_links if url not in seen]

    if first_run:
        print(f"[collectors] Premier lancement : {len(latest_links)} fiches enregistrées, aucune notification envoyée.")
        save_seen(SEEN_FILE, latest_links)
        return

    if not new_links:
        print("[collectors] Aucun nouvel article.")
        return

    for url in reversed(new_links):  # du plus ancien au plus récent
        try:
            article = parse_article(url)
            message = format_message(article)
            send_telegram_message(message, article["image"])
            print(f"✅ [collectors] Notifié : {article['title']}")
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [collectors] Erreur sur {url}: {exc}")

    updated = latest_links + [u for u in seen if u not in latest_links]
    save_seen(SEEN_FILE, updated)


def check_bons_plans():
    latest_promos = get_latest_promos()
    if not latest_promos:
        print("Aucune promo /bons-plans récupérée.")
        return

    latest_urls = [p["url"] for p in latest_promos]
    seen = load_seen(SEEN_PROMOS_FILE)
    first_run = len(seen) == 0
    new_promos = [p for p in latest_promos if p["url"] not in seen]

    if first_run:
        print(f"[bons-plans] Premier lancement : {len(latest_urls)} promos enregistrées, aucune notification envoyée.")
        save_seen(SEEN_PROMOS_FILE, latest_urls)
        return

    if not new_promos:
        print("[bons-plans] Aucune nouvelle promo.")
        return

    for promo in reversed(new_promos):  # du plus ancien au plus récent
        try:
            message = format_promo_message(promo)
            send_telegram_message(message, promo["image"])
            print(f"✅ [bons-plans] Notifié : {promo['title']}")
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [bons-plans] Erreur sur {promo['url']}: {exc}")

    updated = latest_urls + [u for u in seen if u not in latest_urls]
    save_seen(SEEN_PROMOS_FILE, updated)


def main():
    check_collectors()
    check_bons_plans()


if __name__ == "__main__":
    main()
