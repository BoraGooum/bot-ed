import os
import re
import datetime
import requests
from bs4 import BeautifulSoup

# Variables d'environnement GitHub / Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "cron")

URL_COLLECTORS = "https://editioncollector.fr/collectors"
URL_ALAN_WAKE = "https://editioncollector.fr/collectors/alan-wake-design-works-deluxe-edition"

def mettre_a_jour_pointeuse():
    try:
        maintenant = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
        ligne = f"- Corvée {'manuelle ' if EVENT_NAME == 'workflow_dispatch' else ''}effectuée à : {maintenant}\n"
        with open("pointeuse.txt", "a", encoding="utf-8") as f:
            f.write(ligne)
    except Exception as e:
        print(f"Erreur lors de l'écriture dans la pointeuse : {e}")

def envoyer_telegram_media(caption, image_urls):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Erreur : TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID non configuré dans les Secrets GitHub.")
        return

    try:
        if not image_urls:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": caption,
                "parse_mode": "HTML",
                "disable_notification": True
            }
            res = requests.post(url, json=payload, timeout=10)
            res.raise_for_status()
            return

        image_urls = [u for u in image_urls if isinstance(u, str) and u.startswith("http")][:10]

        if len(image_urls) == 1:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": image_urls[0],
                "caption": caption,
                "parse_mode": "HTML",
                "disable_notification": True
            }
            res = requests.post(url, json=payload, timeout=10)
            res.raise_for_status()
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup"
            media = []
            for i, img in enumerate(image_urls):
                item = {"type": "photo", "media": img}
                if i == 0:
                    item["caption"] = caption
                    item["parse_mode"] = "HTML"
                media.append(item)
            
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "media": media,
                "disable_notification": True
            }
            res = requests.post(url, json=payload, timeout=10)
            res.raise_for_status()
    except Exception as e:
        print(f"Erreur lors de l'envoi Telegram : {e}")

def parser_article(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            print(f"Échec de la requête HTTP (Status Code: {res.status_code})")
            return None, []

        soup = BeautifulSoup(res.text, "html.parser")

        titre_elem = soup.find("h1") or soup.find("h2", class_="entry-title")
        titre = titre_elem.get_text(strip=True) if titre_elem else "Article"

        content_div = soup.find("div", class_="entry-content") or soup
        texte_complet = content_div.get_text()

        if "artbook" in titre.lower() or "artbook" in texte_complet.lower() or "design works" in titre.lower():
            type_str, hashtag = "Artbook", "#Artbook"
        elif "livre" in texte_complet.lower() or "roman" in texte_complet.lower():
            type_str, hashtag = "Livres", "#Livres"
        elif any(k in texte_complet.lower() for k in ["ps5", "xbox", "switch", "jeu"]):
            type_str, hashtag = "Jeux Vidéo", "#JV"
        else:
            type_str, hashtag = "Collector", "#Collector"

        langue = "Anglais"
        if "français" in texte_complet.lower() and "anglais" not in texte_complet.lower():
            langue = "Français"

        relie = "? pages / couverture rigide"
        match_pages = re.search(r'(\d+\s*pages)', texte_complet, re.IGNORECASE)
        if match_pages:
            relie = f"{match_pages.group(1)} / couverture rigide"

        contenu_list = []
        for elem in content_div.find_all(["li", "p"]):
            t = elem.get_text(strip=True)
            if t.startswith(("-", "•", "–")) or any(keyword in t.lower() for keyword in ["livre", "carte", "poster", "fourreau", "coffret", "artbook", "boîte", "jaquette", "serviette", "manuscrit", "polaroid"]):
                if 3 < len(t) < 180 and not t.lower().startswith("disponibilités"):
                    clean_t = re.sub(r'^[•–-\s]+', '', t)
                    if clean_t not in contenu_list:
                        contenu_list.append(f"- {clean_t}")

        contenu_str = "\n".join(contenu_list) if contenu_list else "- Détails à venir dans la description."

        dispo_fr, dispo_import = [], []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            texte_bouton = a.get_text(strip=True)
            
            if "/out/" in href or any(m in href.lower() for m in ["lostincult", "amazon", "fnac", "micromania", "leclerc"]):
                nom_marchand = texte_bouton if texte_bouton else "Lien marchand"
                parent_txt = a.parent.get_text(strip=True) if a.parent else ""
                prix_match = re.search(r'(\d+[\.,]?\d*\s*[€$£])', parent_txt)
                prix_str = f" {prix_match.group(1)}" if prix_match else ""

                lien_html = f'<a href="{href}">{nom_marchand}</a>{prix_str}'

                if "lostincult" in href.lower() or "lost in cult" in nom_marchand.lower() or "uk" in href.lower() or "£" in prix_str:
                    dispo_import.append(f"- {lien_html}")
                else:
                    dispo_fr.append(f"- {lien_html}")

        dispo_fr_uniques = list(dict.fromkeys(dispo_fr))
        dispo_import_uniques = list(dict.fromkeys(dispo_import))

        txt_dispo_fr = "\n".join(dispo_fr_uniques) if dispo_fr_uniques else "- bientôt ?"
        txt_dispo_import = "\n".join(dispo_import_uniques) if dispo_import_uniques else "- Aucune pour le moment"

        images = []
        for img in content_div.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src and "wp-content/uploads" in src and not src.endswith(".svg"):
                if src.startswith("//"):
                    src = "https:" + src
                images.append(src)
        
        images_uniques = list(dict.fromkeys(images))

        message = (
            f"🆕 • <b>Nouvel Article !</b>\n\n"
            f"<b>{titre}</b>\n"
            f"<b>Type :</b> {type_str}\n\n"
            f"🔗 <a href=\"{url}\">Voir sur Édition Collector</a>\n\n"
            f"{hashtag}\n\n"
            f"<b>Langue :</b> {langue}\n"
            f"<b>Relié :</b> {relie}\n\n"
            f"<b>Contenu :</b>\n{contenu_str}\n\n"
            f"<b>Disponibilités France :</b>\n{txt_dispo_fr}\n\n"
            f"<b>Disponibilité import :</b>\n{txt_dispo_import}"
        )

        return message, images_uniques
    except Exception as e:
        print(f"Erreur lors du parsing de l'article : {e}")
        return None, []

def envoyer_confirmation_manuelle():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "✅ • Workflow réussi !",
        "disable_notification": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur envoi notification de confirmation : {e}")

if __name__ == "__main__":
    mettre_a_jour_pointeuse()

    if EVENT_NAME == "workflow_dispatch":
        envoyer_confirmation_manuelle()
        caption, imgs = parser_article(URL_ALAN_WAKE)
        if caption:
            envoyer_telegram_media(caption, imgs)
    else:
        caption, imgs = parser_article(URL_COLLECTORS)
        if caption:
            envoyer_telegram_media(caption, imgs)
