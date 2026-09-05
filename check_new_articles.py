#!/usr/bin/env python3
"""
Bot de veille EditionCollector.fr -> Telegram

Surveille DEUX pages :
1. /collectors   -> nouvelles fiches   : titre, photo, type, prix marchands (liens), lien fiche, hashtag
2. /bons-plans   -> nouvelles promos   : titre, photo, type, prix barré/promo (+lien marchand), lien fiche, hashtag

Compare avec les listes déjà connues (seen_articles.json / seen_promos.json).
Au tout premier lancement de chaque flux, le script enregistre juste l'état
actuel SANS notifier, pour ne pas spammer avec tout l'historique déjà en ligne.

Un message de test + un aperçu des 2 derniers éléments (le vrai dernier
article et la vraie dernière promo, récupérés en direct) est envoyé quand le
workflow est lancé MANUELLEMENT (bouton "Run workflow" sur GitHub).

Les notifications de nouveautés (runs automatiques toutes les 15 min) sont
envoyées en SILENCIEUX (pas de son/vibration côté Telegram).

Une ligne est ajoutée à pointeuse.txt à chaque exécution (manuelle ou cron),
utile pour vérifier que le workflow se déclenche bien régulièrement.
"""

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
BONS_PLANS_URL = f"{BASE_URL}/bons-plans"
ALAN_WAKE_URL = f"{BASE_URL}/collectors/alan-wake-design-works-deluxe-edition"

SEEN_FILE = Path(__file__).parent / "seen_articles.json"
SEEN_PROMOS_FILE = Path(__file__).parent / "seen_promos.json"
POINTEUSE_FILE = Path(__file__).parent / "pointeuse.txt"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TRIGGER_EVENT = os.environ.get("TRIGGER_EVENT", os.environ.get("GITHUB_EVENT_NAME", ""))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

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

def heure_paris() -> str:
    return datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y à %H:%M")


def log_pointeuse():
    """Ajoute une ligne à pointeuse.txt à chaque run — sert à vérifier que le
    cron se déclenche bien régulièrement (visible dans le repo une fois commité)."""
    try:
        label = "Workflow manuel" if TRIGGER_EVENT == "workflow_dispatch" else "Cron (Auto)"
        with open(POINTEUSE_FILE, "a", encoding="utf-8") as f:
            f.write(f"- {label} : {heure_paris()}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Erreur écriture pointeuse : {exc}")


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def normalize_href(href: str, prefix: str) -> str | None:
    """Ramène un href (relatif OU absolu) vers sa forme relative '/prefix/slug',
    ou None si ça ne correspond pas à une fiche individuelle."""
    href = href.strip()
    href = re.sub(r"^https?://editioncollector\.fr", "", href)
    m = re.match(rf"^/{prefix}/([a-z0-9\-]+)/?$", href)
    if not m:
        return None
    return f"/{prefix}/{m.group(1)}"


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def extract_category(page_text: str) -> str | None:
    """Le site distingue 'Type' (précis, ex. Artbook, Manga, Steelbook) et
    'Univers' (large, ex. Livres, Jeux vidéo). On préfère le Type quand il
    existe, et on retombe sur l'Univers sinon."""
    m = re.search(r"Type\s*:\s*([^\n]+)", page_text)
    if m:
        return m.group(1).strip()
    m = re.search(r"Univers\s*:\s*([^\n]+)", page_text)
    if m:
        return m.group(1).strip()
    return None


def build_hashtag(univers: str | None) -> str:
    if not univers:
        return ""
    tag = HASHTAG_MAP.get(univers.strip().lower())
    if tag:
        return tag
    fallback = re.sub(r"[^A-Za-zÀ-ÿ0-9]", "_", univers.strip())
    return f"#{fallback}"


def send_telegram_message(text: str, photo_url: str | None, silent: bool = True):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants, message non envoyé :")
        print(text)
        return

    if photo_url and not photo_url.startswith("http"):
        photo_url = BASE_URL + (photo_url if photo_url.startswith("/") else "/" + photo_url)

    if photo_url:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": text[:1024],  # limite Telegram pour les légendes de photo
            "parse_mode": "HTML",
            "photo": photo_url,
            "disable_notification": silent,
        }
    else:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": silent,
        }

    try:
        resp = requests.post(api_url, data=payload, timeout=20)
        if not resp.ok:
            print(f"❌ Erreur Telegram ({resp.status_code}): {resp.text}")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Erreur Telegram : {exc}")


def load_seen(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_seen(path: Path, urls: list[str]):
    path.write_text(json.dumps(urls[:500], ensure_ascii=False, indent=2), encoding="utf-8")


def extract_image(soup: BeautifulSoup, resp_text: str) -> str | None:
    """Cherche la meilleure image possible : S3 direct -> figure/img -> og:image."""
    s3_matches = re.findall(
        r'https://edition-collector-production\.s3\.amazonaws\.com/uploads/image/file/[^\s"\'<>]+',
        resp_text,
    )
    if s3_matches:
        return s3_matches[0]

    figure_img = soup.select_one("figure img") or soup.find("img")
    if figure_img:
        candidate = figure_img.get("data-lazy-src") or figure_img.get("data-src") or figure_img.get("src")
        if candidate and not candidate.startswith("data:"):
            return candidate

    image_tag = soup.find("meta", property="og:image")
    if image_tag and image_tag.get("content"):
        candidate = image_tag["content"].strip()
        if "logo" not in candidate.lower() and "favicon" not in candidate.lower():
            return candidate

    return None


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
    """Extrait titre, image, type (univers) et TOUS les prix marchands d'une fiche."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("meta", property="og:title")
    title = title_tag["content"].strip() if title_tag else (soup.title.get_text(strip=True) if soup.title else "Article")

    image_url = extract_image(soup, resp.text)

    page_text = soup.get_text("\n", strip=True)

    univers = extract_category(page_text)

    # Tous les marchands avec prix, dans l'ordre où ils apparaissent
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
        "image": image_url,
        "univers": univers,
        "merchants": merchants,
    }


def format_article_message(article: dict) -> str:
    lines = [f"🆕 • <b>{escape_html(article['title'])}</b>", ""]
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


def check_collectors():
    latest_links = get_latest_article_links()
    print(f"[collectors] {len(latest_links)} liens trouvés sur la page.")
    if not latest_links:
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
            send_telegram_message(format_article_message(article), article["image"], silent=True)
            print(f"✅ [collectors] Notifié : {article['title']}")
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [collectors] Erreur sur {url}: {exc}")

    updated = latest_links + [u for u in seen if u not in latest_links]
    save_seen(SEEN_FILE, updated)


# --------------------------------------------------------------------------
# /bons-plans -> nouvelles promos
# --------------------------------------------------------------------------

PROMO_PATTERN = re.compile(
    r"(.+?)\s+est en promo\s*-(\d+)%\s*([\d]+[.,]\d{2})\s*€\s*([\d]+[.,]\d{2})\s*€",
    re.UNICODE,
)


def get_latest_promo_links() -> list[str]:
    soup = fetch(BONS_PLANS_URL)
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        rel = normalize_href(a["href"], "bons-plans")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        links.append(BASE_URL + rel)
    return links


def get_latest_promos_raw() -> list[dict]:
    """Récupère titre / remise / prix / marchand / image / url depuis le listing
    /bons-plans. Le prix et le titre ne sont pas forcément dans la même balise
    que le lien (mise en page variable), donc on cherche le motif sur le texte
    de TOUTE la page, puis on associe chaque promo trouvée aux liens (fiche /
    image / marchand) dans le même ordre d'apparition."""
    soup = fetch(BONS_PLANS_URL)

    page_text = soup.get_text("\n", strip=True)
    matches = list(PROMO_PATTERN.finditer(page_text))
    print(f"[bons-plans] {len(matches)} motifs 'est en promo' trouvés dans le texte de la page.")

    detail_links = get_latest_promo_links()
    print(f"[bons-plans] {len(detail_links)} liens de fiches uniques trouvés.")

    images_by_href = {}
    for a in soup.find_all("a", href=True):
        rel = normalize_href(a["href"], "bons-plans")
        if not rel:
            continue
        full = BASE_URL + rel
        if full in images_by_href:
            continue
        img = a.find("img")
        if img and img.get("src"):
            images_by_href[full] = img["src"]

    merchant_links = []
    for a in soup.find_all("a", href=True):
        if "edcol.fr" in a["href"]:
            merchant_links.append({"name": a.get_text(strip=True), "url": a["href"]})

    if len({len(matches), len(detail_links)}) > 1:
        print(f"⚠️ [bons-plans] Décalage entre motifs ({len(matches)}) et liens ({len(detail_links)}) — association par ordre, best-effort.")

    count = min(len(matches), len(detail_links))
    promos = []
    for i in range(count):
        m = matches[i]
        title = re.sub(r"^(NEW|MAJ)\s+", "", m.group(1).strip())
        url = detail_links[i]
        promos.append({
            "url": url,
            "title": title,
            "discount": m.group(2),
            "promo_price": m.group(3),
            "original_price": m.group(4),
            "image": images_by_href.get(url),
            "merchant_name": merchant_links[i]["name"] if i < len(merchant_links) else None,
            "merchant_url": merchant_links[i]["url"] if i < len(merchant_links) else None,
        })

    print(f"[bons-plans] {len(promos)} promos assemblées avec succès.")
    return promos


def get_univers_for_promo(promo_url: str) -> str | None:
    """Va chercher le Type/Univers sur la fiche promo, ou sur la fiche
    collector associée si elle y est liée."""
    try:
        soup = fetch(promo_url)
    except Exception:  # noqa: BLE001
        return None

    page_text = soup.get_text("\n", strip=True)
    category = extract_category(page_text)
    if category:
        return category

    for a in soup.find_all("a", href=True):
        rel = normalize_href(a["href"], "collectors")
        if not rel:
            continue
        try:
            sub_soup = fetch(BASE_URL + rel)
        except Exception:  # noqa: BLE001
            continue
        sub_text = sub_soup.get_text("\n", strip=True)
        sub_category = extract_category(sub_text)
        if sub_category:
            return sub_category
        break

    return None


def format_promo_message(promo: dict) -> str:
    lines = [f"💸 • <b>{escape_html(promo['title'])}</b>", ""]
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


def check_bons_plans():
    latest_promos = get_latest_promos_raw()
    if not latest_promos:
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
            promo["univers"] = get_univers_for_promo(promo["url"])
            send_telegram_message(format_promo_message(promo), promo["image"], silent=True)
            print(f"✅ [bons-plans] Notifié : {promo['title']}")
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [bons-plans] Erreur sur {promo['url']}: {exc}")

    updated = latest_urls + [u for u in seen if u not in latest_urls]
    save_seen(SEEN_PROMOS_FILE, updated)


# --------------------------------------------------------------------------
# Lancement manuel : message de test + aperçu des 2 derniers éléments
# --------------------------------------------------------------------------

def send_test_message():
    send_telegram_message(f"✅ • Workflow réussi, bot opérationnel ({heure_paris()})", None, silent=False)
    print("✅ Message de test envoyé.")


def send_latest_collector_preview():
    try:
        article = parse_article(ALAN_WAKE_URL)
        message = "🔍 <i>Aperçu manuel</i>\n\n" + format_article_message(article)
        send_telegram_message(message, article["image"], silent=False)
        print(f"✅ [aperçu] Article envoyé : {article['title']}")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ [aperçu] Erreur sur l'article de test : {exc}")


def send_latest_bons_plan_preview():
    try:
        latest_promos = get_latest_promos_raw()
        if not latest_promos:
            print("⚠️ [aperçu] Aucune promo /bons-plans trouvée.")
            return
        promo = latest_promos[0]
        promo["univers"] = get_univers_for_promo(promo["url"])
        message = "🔍 <i>Aperçu manuel — dernier bon plan publié</i>\n\n" + format_promo_message(promo)
        send_telegram_message(message, promo["image"], silent=False)
        print(f"✅ [aperçu] Dernier bon plan envoyé : {promo['title']}")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ [aperçu] Erreur sur le dernier bon plan : {exc}")


def main():
    log_pointeuse()

    is_manual = TRIGGER_EVENT == "workflow_dispatch"
    if is_manual:
        send_test_message()
        send_latest_collector_preview()
        send_latest_bons_plan_preview()

    check_collectors()
    check_bons_plans()


if __name__ == "__main__":
    main()
