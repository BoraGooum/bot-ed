import os
import re
import datetime
import requests
from bs4 import BeautifulSoup

# Variables d'environnement GitHub / Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "cron")  # 'workflow_dispatch' ou 'schedule'/'cron'

URL_COLLECTORS = "https://editioncollector.fr/collectors"
URL_BONS_PLANS = "https://editioncollector.fr/bons-plans"
URL_ALAN_WAKE = "https://editioncollector.fr/collectors/alan-wake-design-works-deluxe-edition"

# 1. Gestion de la pointeuse.txt
def mettre_a_jour_pointeuse():
    maintenant = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")
    if EVENT_NAME == "workflow_dispatch":
        ligne = f"- Corvée manuelle effectuée à : {maintenant}\n"
    else:
        ligne = f"- Corvée effectuée à : {maintenant}\n"
    
    with open("pointeuse.txt", "a", encoding="utf-8") as f:
        f.write(ligne)

# 2. Envoi Telegram (1 photo vs Album de photos)
def envoyer_telegram_media(caption, image_urls):
    if not image_urls:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "parse_mode": "HTML",
            "disable_notification": True
        }
        requests.post(url, json=payload)
        return

    # Nettoyage et filtre (10 images max)
    image_urls = [u for u in image_urls if isinstance(u, str) and u.startswith("http")][:10]

    if len(image_urls) == 1:
        # Une seule photo -> sendPhoto (évite l'erreur 400 du MediaGroup à 1 élément)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": image_urls[0],
            "caption": caption,
            "parse_mode": "HTML",
            "disable_notification": True
        }
        requests.post(url, json=payload)
    else:
        # 2 photos ou plus -> sendMediaGroup (Album)
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
        requests.post(url, json=payload)

# 3. Scraping adapté à la structure d'Édition Collector
def parser_article(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        return None, []

    soup = BeautifulSoup(res.text, "html.parser")

    # Titre principal
    titre_elem = soup.find("h1") or soup.find("h2", class_="entry-title")
    titre = titre_elem.get_text(strip=True) if titre_elem else "Article"

    content_div = soup.find("div", class_="entry-content") or soup
    texte_complet = content_div.get_text()

    # Détection Artbook / Type / Hashtag
    if "artbook" in titre.lower() or "artbook" in texte_complet.lower() or "design works" in titre.lower():
        type_str = "Artbook"
        hashtag = "#Artbook"
    elif "livre" in texte_complet.lower() or "roman" in texte_complet.lower():
        type_str = "Livres"
        hashtag = "#Livres"
    elif any(k in texte_complet.lower() for k in ["ps5", "xbox", "switch", "jeu"]):
        type_str = "Jeux Vidéo"
        hashtag = "#JV"
    else:
        type_str = "Collector"
        hashtag = "#Collector"

    # Langue
    langue = "Anglais"
    if "français" in texte_complet.lower() and "anglais" not in texte_complet.lower():
        langue = "Français"

    # Format / Relié
    relie = "? pages / couverture rigide"
    match_pages = re.search(r'(\d+\s*pages)', texte_complet, re.IGNORECASE)
    if match_pages:
        relie = f"{match_pages.group(1)} / couverture rigide"

    # Extraction du contenu détaillé
    contenu_list = []
    
    for elem in content_div.find_all(["li", "p"]):
        t = elem.get_text(strip=True)
        if t.startswith(("-", "•", "–")) or any(keyword in t.lower() for keyword in ["livre", "carte", "poster", "fourreau", "coffret", "artbook", "boîte", "jaquette", "serviette", "manuscrit", "polaroid"]):
            if 3 < len(t) < 180 and not t.lower().startswith("disponibilités"):
                clean_t = re.sub(r'^[•–-\s]+', '', t)
                if clean_t not in contenu_list:
                    contenu_list.append(f"- {clean_t}")

    contenu_str = "\n".join(contenu_list) if contenu_list else "- Détails à venir dans la description."

    # Extraction des marchands et prix (France vs Import)
    dispo_fr = []
    dispo_import = []

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

    # Images de l'article
    images = []
    for img in content_div.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and "wp-content/uploads" in src and not src.endswith(".svg"):
            if src.startswith("//"):
                src = "https:" + src
            images.append(src)
    
    images_uniques = list(dict.fromkeys(images))

    # Mise en forme du message Telegram
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

# 4. Confirmation sonore pour les déclenchements manuels
def envoyer_confirmation_manuelle():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "✅ • Workflow réussi !",
        "disable_notification": False
    }
    requests.post(url, json=payload)

# Exécution principale
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
