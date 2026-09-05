#!/usr/bin/env python3
"""
Bot de veille EditionCollector.fr -> Telegram

Surveille DEUX pages :
1. /collectors   -> nouvelles fiches   : titre, photo, type, disponibilités marchands (liens), lien fiche, hashtag
2. /bons-plans   -> nouvelles promos   : titre, photo, type, prix barré/promo (+lien marchand), lien fiche, hashtag

Compare avec les listes déjà connues (seen_articles.json / seen_promos.json).
Au tout premier lancement de chaque flux, le script enregistre juste l'état
actuel SANS notifier, pour ne pas spammer avec tout l'historique déjà en
ligne.

Toutes les notifications de nouveautés sont envoyées en SILENCIEUX (pas de
son/vibration côté Telegram).

Quand un cycle ne trouve AUCUNE nouveauté (ni article ni bon plan), un GIF
"rien de neuf" est envoyé — en remplaçant le précédent GIF du même type
(l'ancien message est supprimé avant l'envoi du nouveau, pour qu'un seul GIF
de ce type existe à la fois dans la discussion).

Les RECENT_UPDATES_CHECK derniers articles connus sont revisités à chaque
cycle pour détecter un changement de prix/marchand ; si un changement est
détecté, une notification "mise à jour" est renvoyée.

Une ligne est ajoutée à pointeuse.txt à chaque exécution, utile pour
vérifier que le workflow se déclenche bien régulièrement.
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

SEEN_FILE = Path(__file__).parent / "seen_articles.json"
SEEN_PROMOS_FILE = Path(__file__).parent / "seen_promos.json"
SNAPSHOTS_FILE = Path(__file__).parent / "article_snapshots.json"
POINTEUSE_FILE = Path(__file__).parent / "pointeuse.txt"
HEARTBEAT_STATE_FILE = Path(__file__).parent / "heartbeat_state.json"

RECENT_UPDATES_CHECK = 40  # nombre de derniers articles revérifiés à chaque cycle pour détecter une mise à jour

HEARTBEAT_GIF_URL = "https://c.tenor.com/VmUFY5_WKUEAAAAd/tenor.gif"
HEARTBEAT_CAPTION = "🏴‍☠️ • Pas de promo en vue moussaillon..."

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


# --------------------------------------------------------------------------
# GIF "rien de neuf" — un seul à la fois, remplacé à chaque cycle vide
# --------------------------------------------------------------------------

def send_animation(url: str, caption: str) -> int | None:
    """Envoie un GIF/MP4 par URL, retourne le message_id Telegram ou None."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAnimation"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "animation": url,
        "caption": caption,
        "parse_mode": "HTML",
        "disable_notification": True,
    }
    try:
        resp = requests.post(api_url, data=payload, timeout=20)
        resp.raise_for_status()
        return resp.json()["result"]["message_id"]
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Erreur envoi GIF : {exc}")
        return None


def delete_telegram_message(message_id: int):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
    try:
        requests.post(api_url, data={"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Impossible de supprimer l'ancien GIF (id {message_id}) : {exc}")


def load_heartbeat_message_id() -> int | None:
    if not HEARTBEAT_STATE_FILE.exists():
        return None
    try:
        data = json.loads(HEARTBEAT_STATE_FILE.read_text(encoding="utf-8"))
        return data.get("message_id")
    except json.JSONDecodeError:
        return None


def save_heartbeat_message_id(message_id: int | None):
    HEARTBEAT_STATE_FILE.write_text(json.dumps({"message_id": message_id}), encoding="utf-8")


def manage_heartbeat_gif(found_new: bool):
    """Si rien de neuf ce cycle : supprime l'ancien GIF et en envoie un
    nouveau. Si quelque chose de neuf est arrivé : supprime le GIF en
    attente (il n'a plus lieu d'être) sans en renvoyer un."""
    previous_id = load_heartbeat_message_id()

    if found_new:
        if previous_id:
            delete_telegram_message(previous_id)
            save_heartbeat_message_id(None)
        return

    if previous_id:
        delete_telegram_message(previous_id)

    new_id = send_animation(HEARTBEAT_GIF_URL, HEARTBEAT_CAPTION)
    save_heartbeat_message_id(new_id)
    if new_id:
        print("✅ [heartbeat] GIF 'rien de neuf' envoyé.")


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


# Le site fait passer TOUS ses liens marchands par un raccourcisseur
# d'affiliation (edcol.fr/xxxx) : le nom du marchand n'apparaît jamais dans
# l'URL, seulement dans le texte visible du lien (ex. "Amazon 34,99€",
# "Amazon 699€" sans centimes, "Micromania 799,99€ (débit à la commande)").
# On détecte donc le marchand sur le TEXTE du lien, pas sur son href, et le
# prix n'a PAS toujours de décimales.
MERCHANT_TEXT_PATTERN = re.compile(
    r"^([A-Za-zÀ-ÿ0-9'’\.\-\s]+?)\s+([\d]+(?:[.,]\d{1,2})?)\s*([€£$])", re.UNICODE
)

# Préfixes parasites parfois collés devant le nom du marchand sur le site
_NAME_PREFIXES_TO_STRIP = ("exclu ", "précommande ", "dispo ")


def detect_merchants(soup: BeautifulSoup) -> tuple[list[dict], list[dict]]:
    """Repère les liens marchands sur une fiche (texte 'Marchand prix€') et
    les répartit en Disponibilités France / Disponibilité import (Belgique,
    UK, £, .be...)."""
    dispo_fr, dispo_import = [], []
    seen_names = set()

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        match = MERCHANT_TEXT_PATTERN.match(text)
        if not match:
            continue

        name, price_num, currency = match.groups()
        name = name.strip()
        for prefix in _NAME_PREFIXES_TO_STRIP:
            if name.lower().startswith(prefix):
                name = name[len(prefix):].strip()
                break

        dedup_key = name.lower()
        if dedup_key in seen_names:
            continue
        seen_names.add(dedup_key)

        price = f"{price_num}{currency}"
        entry = {"name": name, "url": a["href"], "price": price}

        # étranger si : devise non-€, nom se terminant par un suffixe pays
        # non-français (.be, .de, .uk, .es, .it, .nl...), ou marchand connu
        # comme uniquement étranger (Zavvi, Lost in Cult).
        country_suffix = re.search(r"\.([a-z]{2})$", dedup_key)
        is_foreign_domain = bool(country_suffix) and country_suffix.group(1) != "fr"
        is_import = (
            currency != "€"
            or is_foreign_domain
            or "zavvi" in dedup_key
            or "lostincult" in dedup_key
            or "lostincult" in a["href"].lower()
        )
        (dispo_import if is_import else dispo_fr).append(entry)

    return dispo_fr, dispo_import


def parse_article(url: str) -> dict:
    """Extrait titre, image, type (univers) et disponibilités FR/import d'une fiche."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("meta", property="og:title")
    title = title_tag["content"].strip() if title_tag else (soup.title.get_text(strip=True) if soup.title else "Article")

    image_url = extract_image(soup, resp.text)

    page_text = soup.get_text("\n", strip=True)
    univers = extract_category(page_text)

    dispo_fr, dispo_import = detect_merchants(soup)

    prices = []
    for entry in dispo_fr + dispo_import:
        if entry["price"] and entry["price"] not in prices:
            prices.append(entry["price"])

    return {
        "url": url,
        "title": title,
        "image": image_url,
        "univers": univers,
        "dispo_fr": dispo_fr,
        "dispo_import": dispo_import,
        "prices": prices,
    }


def format_merchant_line(entry: dict) -> str:
    link = f'<a href="{entry["url"]}">{escape_html(entry["name"])}</a>'
    return f"- {link} {entry['price']}" if entry["price"] else f"- {link}"


def format_article_message(article: dict, updated: bool = False) -> str:
    emoji = "🔄" if updated else "🆕"
    suffix = " (mise à jour)" if updated else ""
    lines = [f"{emoji} • <b>{escape_html(article['title'])}</b>{suffix}", ""]

    if article["univers"]:
        lines.append(f"• Type : {escape_html(article['univers'])}")
        lines.append("")

    lines.append("• Disponibilités France :")
    if article["dispo_fr"]:
        lines.extend(format_merchant_line(e) for e in article["dispo_fr"])
    else:
        lines.append("- bientôt ?")
    lines.append("")

    lines.append("• Disponibilité import :")
    if article["dispo_import"]:
        lines.extend(format_merchant_line(e) for e in article["dispo_import"])
    else:
        lines.append("- Aucune pour le moment")
    lines.append("")

    if article["prices"]:
        lines.append("• Prix :")
        lines.extend(f"- {p}" for p in article["prices"])
        lines.append("")

    lines.append(f"🔗 <a href=\"{article['url']}\">Voir sur Édition Collector</a>")

    hashtag = build_hashtag(article["univers"])
    if hashtag:
        lines.append("")
        lines.append(hashtag)

    return "\n".join(lines)


def build_article_snapshot(article: dict) -> dict:
    """Résumé comparable d'une fiche (univers + disponibilités), utilisé
    pour détecter si elle a changé depuis la dernière vérification."""
    return {
        "univers": article.get("univers"),
        "dispo_fr": sorted(f"{e['name']}|{e['price']}" for e in article["dispo_fr"]),
        "dispo_import": sorted(f"{e['name']}|{e['price']}" for e in article["dispo_import"]),
    }


def load_snapshots() -> dict:
    if not SNAPSHOTS_FILE.exists():
        return {}
    try:
        return json.loads(SNAPSHOTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_snapshots(snapshots: dict):
    SNAPSHOTS_FILE.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")


def check_article_updates() -> bool:
    """Revisite les RECENT_UPDATES_CHECK derniers articles connus et
    renvoie une notification si leurs disponibilités/prix ont changé.
    Retourne True si au moins une mise à jour a été notifiée."""
    seen = load_seen(SEEN_FILE)
    if not seen:
        return False

    recent_urls = seen[:RECENT_UPDATES_CHECK]
    snapshots = load_snapshots()
    found_update = False

    for url in recent_urls:
        try:
            article = parse_article(url)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ [maj] Erreur sur {url}: {exc}")
            continue

        new_snapshot = build_article_snapshot(article)
        old_snapshot = snapshots.get(url)

        if old_snapshot is not None and old_snapshot != new_snapshot:
            send_telegram_message(format_article_message(article, updated=True), article["image"], silent=True)
            print(f"🔄 [maj] Mise à jour notifiée : {article['title']}")
            found_update = True
            time.sleep(1)

        snapshots[url] = new_snapshot

    # on ne garde que les snapshots des articles encore suivis (borne la taille du fichier)
    snapshots = {u: s for u, s in snapshots.items() if u in seen}
    save_snapshots(snapshots)

    if not found_update:
        print(f"[maj] Aucun changement sur les {len(recent_urls)} derniers articles vérifiés.")

    return found_update


def check_collectors() -> bool:
    """Retourne True si au moins une nouveauté a été notifiée."""
    latest_links = get_latest_article_links()
    print(f"[collectors] {len(latest_links)} liens trouvés sur la page.")
    if not latest_links:
        return False

    seen = load_seen(SEEN_FILE)
    first_run = len(seen) == 0
    new_links = [url for url in latest_links if url not in seen]

    if first_run:
        print(f"[collectors] Premier lancement : {len(latest_links)} fiches enregistrées, aucune notification envoyée.")
        save_seen(SEEN_FILE, latest_links)
        return False

    if not new_links:
        print("[collectors] Aucun nouvel article.")
        return False

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
    return True


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


def check_bons_plans() -> bool:
    """Retourne True si au moins une nouveauté a été notifiée."""
    latest_promos = get_latest_promos_raw()
    if not latest_promos:
        return False

    latest_urls = [p["url"] for p in latest_promos]
    seen = load_seen(SEEN_PROMOS_FILE)
    first_run = len(seen) == 0
    new_promos = [p for p in latest_promos if p["url"] not in seen]

    if first_run:
        print(f"[bons-plans] Premier lancement : {len(latest_urls)} promos enregistrées, aucune notification envoyée.")
        save_seen(SEEN_PROMOS_FILE, latest_urls)
        return False

    if not new_promos:
        print("[bons-plans] Aucune nouvelle promo.")
        return False

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
    return True


def main():
    log_pointeuse()

    found_collectors = check_collectors()
    found_updates = check_article_updates()
    found_promos = check_bons_plans()

    manage_heartbeat_gif(found_new=found_collectors or found_updates or found_promos)


if __name__ == "__main__":
    main()
