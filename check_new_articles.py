import os
import re
import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

# Variables d'environnement GitHub / Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "cron")

URL_COLLECTORS = "https://editioncollector.fr/collectors"
URL_ALAN_WAKE = "https://editioncollector.fr/collectors/alan-wake-design-works-deluxe-edition"

# 1. Pointeuse calée sur l'heure de Paris
def mettre_a_jour_pointeuse():
    try:
        fuseau_paris = ZoneInfo("Europe/Paris")
        maintenant = datetime.datetime.now(fuseau_paris).strftime("%d/%m/%Y à %H:%M")
        ligne = f"- Corvée {'manuelle ' if EVENT_NAME == 'workflow_dispatch' else ''}effectuée à : {maintenant}\n"
        with open("pointeuse.txt", "a", encoding="utf-8") as f:
            f.write(ligne)
    except Exception as e:
        print(f"Erreur lors de l'écriture dans la pointeuse : {e}")

# 2. Envoi Telegram avec uniquement la première image
def envoyer_telegram(caption, image_url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Erreur : TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID absent des Secrets GitHub.")
        return

    try:
        if image_url:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "HTML",
                "disable_notification": True
            }
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": caption,
                "parse_mode": "HTML",
                "disable_notification": True
            }
            
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"Erreur lors de l'envoi Telegram : {e}")

# 3. Scraping et mise en forme du message
def parser_article(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            print(f"Échec HTTP ({res.status_code}) pour {url}")
            return None, None

        soup = BeautifulSoup(res.text, "html.parser")

        # Titre
        titre_elem = soup.find("h1") or soup.find("h2", class_="entry-title")
        titre = titre_elem.get_text(strip=True) if titre_elem else "Article"

        content_div = soup.find("div", class_="entry-content") or soup
        texte_complet = content_div.get_text()

        # En-tête : Bon plan ou Nouvel Article
        is_bon_plan = "bons-plans" in url or "bon plan" in titre.lower()
        header_str = "💸 • <b>Bon plan !</b>" if is_bon_plan else "🆕 • <b>Nouvel Article !</b>"

        # Type & Hashtag
        if "artbook" in titre.lower() or "artbook" in texte_complet.lower() or "design works" in titre.lower():
            type_str, hashtag = "Artbook", "#Artbook"
        elif "livre" in texte_complet.lower() or "roman" in texte_complet.lower():
            type_str, hashtag = "Livres", "#Livres"
        elif any(k in texte_complet.lower() for k in ["ps5", "xbox", "switch", "jeu"]):
            type_str, hashtag = "Jeux Vidéo", "#JV"
        else:
            type_str, hashtag = "Collector", "#Collector"

        # Traitement des marchands et des prix (avec prix barré pour les bons plans)
        dispo_fr, dispo_import = [], []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            texte_bouton = a.get_text(strip=True)
            
            if "/out/" in href or any(m in href.lower() for m in ["lostincult", "amazon", "fnac", "micromania", "leclerc"]):
                nom_marchand = texte_bouton if texte_bouton else "Lien marchand"
                parent_txt = a.parent.get_text(strip=True) if a.parent else ""
                
                prix_trouves = re.findall(r'(\d+[\.,]?\d*\s*[€$£])', parent_txt)
                
                if len(prix_trouves) >= 2 and is_bon_plan:
                    prix_str = f" <s>{prix_trouves[0]}</s> <b>{prix_trouves[1]}</b>"
                elif len(prix_trouves) == 1:
                    prix_str = f" <b>{prix_trouves[0]}</b>"
                else:
                    prix_str = ""

                lien_html = f'<a href="{href}">{nom_marchand}</a>{prix_str}'

                if "lostincult" in href.lower() or "lost in cult" in nom_marchand.lower() or "uk" in href.lower() or "£" in prix_str:
                    dispo_import.append(f"- {lien_html}")
                else:
                    dispo_fr.append(f"- {lien_html}")

        dispo_fr_uniques = list(dict.fromkeys(dispo_fr))
        dispo_import_uniques = list(dict.fromkeys(dispo_import))

        txt_dispo_fr = "\n".join(dispo_fr_uniques) if dispo_fr_uniques else "- bientôt ?"
        txt_dispo_import = "\n".join(dispo_import_uniques) if dispo_import_uniques else "- Aucune pour le moment"

        # Image principale (la première uniquement)
        premiere_image = None
        for img in content_div.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src and "wp-content/uploads" in src and not src.endswith(".svg"):
                if src.startswith("//"):
                    src = "https:" + src
                premiere_image = src
                break

        # Assemblage du message
        message = (
            f"{header_str}\n\n"
            f"<b>{titre}</b>\n\n"
            f"• <b>Type :</b> {type_str}\n\n"
            f"• <b>Disponibilités France :</b>\n{txt_dispo_fr}\n\n"
            f"• <b>Disponibilité import :</b>\n{txt_dispo_import}\n\n"
            f"🔗 <a href=\"{url}\">Voir sur Édition Collector</a>\n\n"
            f"{hashtag}"
        )

        return message, premiere_image
    except Exception as e:
        print(f"Erreur lors du parsing de l'article : {e}")
        return None, None

def envoyer_confirmation_manuelle():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "✅ • Workflow réussi !",
        "disable_notification": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur notification : {e}")

if __name__ == "__main__":
    mettre_a_jour_pointeuse()

    if EVENT_NAME == "workflow_dispatch":
        envoyer_confirmation_manuelle()
        caption, img_url = parser_article(URL_ALAN_WAKE)
        if caption:
            envoyer_telegram(caption, img_url)
    else:
        caption, img_url = parser_article(URL_COLLECTORS)
        if caption:
            envoyer_telegram(caption, img_url)
