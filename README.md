# Bot de veille EditionCollector.fr → Telegram

Envoie un message Telegram automatiquement pour :

**🆕 Nouvel article** sur `/collectors` :
- Titre, photo, type (Jeux vidéo, Films/Séries, etc.)
- Prix de tous les marchands trouvés sur la fiche, chacun en hyperlien
- Lien vers la fiche Édition Collector
- Hashtag du type (#JV, #Films_Séries, #Livres, #Musiques, #Goodies...)

**💸 Bon plan** sur `/bons-plans` :
- Titre, photo, type
- Prix de base barré ➜ prix promo (-X%), en hyperlien vers le marchand
- Lien vers la fiche Édition Collector
- Hashtag du type

**✅ Message de test + aperçu** à chaque lancement **manuel** du workflow
(bouton "Run workflow") : un message de confirmation, puis le vrai dernier
article et la vraie dernière promo publiés (récupérés en direct, pas des
exemples codés en dur). Les runs automatiques (toutes les 15 min)
n'envoient PAS ces messages de test, pour ne pas spammer.

**📋 pointeuse.txt** : une ligne est ajoutée à ce fichier à chaque run
(manuel ou automatique), avec la date/heure et le type de déclenchement.
Utile pour vérifier depuis le repo que le cron se déclenche bien
régulièrement, sans avoir à ouvrir l'onglet Actions.

Les notifications de nouveautés sont envoyées **en silencieux** (pas de
son/vibration) ; le message de test et les aperçus manuels ont le son normal.

---

## Installation

### 1. Créer le bot Telegram
1. Cherche **@BotFather** sur Telegram, envoie `/newbot`, suis les
   instructions. Il te donne un **token**.
2. Démarre une conversation avec ton bot (`/start`).
3. Récupère ton **chat_id** en ouvrant :
   `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
   → cherche `"chat":{"id": 123456789, ...}`.

### 2. Secrets GitHub
Dans le repo : **Settings → Secrets and variables → Actions → New
repository secret** :
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 3. Permissions d'écriture
**Settings → Actions → General → Workflow permissions** → coche **"Read and
write permissions"** → **Save**. (Indispensable : sans ça, le commit final
échoue avec `exit code 128`.)

### 4. Premier lancement
**Actions → Check New Articles → Run workflow.**

---

## Limites à connaître

- Le script ne lit que la 1ère page de `/collectors` et `/bons-plans` (~36
  éléments les plus récents chacune) à chaque exécution.
- Pour les bons plans, le "type" (univers) n'est pas toujours affiché sur la
  page de la promo elle-même ; le script essaie de le récupérer sur la fiche
  associée si un lien y est présent. S'il ne trouve rien, le message part
  sans la ligne "Type" ni hashtag.
- Le cron GitHub Actions n'est pas garanti à la minute près (délai possible
  en cas de forte charge côté GitHub).
- **`seen_articles.json` et `seen_promos.json` DOIVENT être commités par le
  workflow à chaque run** (étape "Commit et push des fichiers de suivi").
  Si un jour les notifications se remettent à répéter les mêmes articles en
  boucle, c'est le premier endroit à vérifier : est-ce que ces fichiers
  changent bien dans l'historique Git après chaque run ?
- Si le cron semble s'arrêter de se déclencher, regarde `pointeuse.txt` dans
  le repo : la dernière ligne "Cron (Auto)" te dit quand le dernier run
  automatique a réellement eu lieu.
