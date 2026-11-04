# CCG 18 - bot Telegram pentru asociații de proprietari și cooperative

Bot Telegram + panou web pentru o cooperativă de construcție a garajelor (CCG) din Chișinău. Membrii se identifică prin numărul de telefon confirmat de Telegram și pot vota, primi anunțuri și convocări, vedea cotizațiile pe garaj și trimite sesizări. Conducerea lucrează dintr-un panou web, deschis și ca Telegram Mini App.

Gândit să poată fi folosit de orice cooperativă sau asociație de proprietari.

## Ce face

- **Identificare fără parole**: butonul „Trimite numărul meu”; numărul (confirmat de Telegram, nu tastat) se compară cu registrul asociației. Contactul altei persoane e refuzat.
- **Membri confirmați / neconfirmați**: o listă preliminară (ex. din grupul de chat existent) intră ca neconfirmată; botul îi cere fiecăruia numele și garajele, conducerea le aliniază cu registrul și confirmă. Doar membrii confirmați votează și intră în cvorum.
- **Vot**: buletin cu butoane, confirmare, chitanță; un vot pe membru sau pe garaj; deschis sau secret (cine a votat e separat de ce a votat); vot pe hârtie introdus de conducere, marcat nominal; proces-verbal de printat.
- **Anunțuri și convocări** către toți sau pe rânduri, cu confirmare de primire; conținut protejat la redirecționare pentru convocări.
- **Cotizații** pe garaj, **sesizări** cu poză.
- **Grup Telegram verificat**: intrarea doar prin cerere de aderare, aprobată automat numai pentru numerele din registru.
- **Jurnal de audit**: fiecare acțiune a conducerii, cu autor și oră.
- Română și rusă.

## Regula datelor personale (obligatorie)

**În acest depozit nu intră niciodată date cu caracter personal**: telefoane, nume reale, adrese, e-mailuri, IBAN-uri, liste de membri, baze de date, exporturi.

- Datele reale stau doar pe server (volum `/data`) și local în `data/`, exclus din git.
- Testele folosesc numere fictive cu prefixul `0000` / `3730`, care nu există în rețelele din Moldova; numerele cu format real se asamblează la rulare.
- `scripts/pii_scan.py` blochează commit-ul (hook `.githooks/pre-commit`) și CI-ul dacă găsește un telefon, e-mail, IBAN sau fișier de date. Activare locală: `git config core.hooksPath .githooks`.
- Secretele (tokenul botului, cheia de sesiune) doar în variabile de mediu; `gitleaks` rulează în CI.

## Rulare

```bash
pip install -r requirements.txt
cp .env.example .env   # BOT_TOKEN de la @BotFather, SECRET_KEY aleator
python -m uvicorn app.main:app --port 8000
```

Local (BASE_URL `http://...`) botul rulează prin polling; în producție (BASE_URL `https://...`) setează automat un webhook cu `secret_token`. Folosiți **tokenuri diferite** pentru dezvoltare și producție: un token nu poate fi folosit simultan de polling și webhook.

Docker: `docker build -t ccg18 . && docker run -p 8000:8000 --env-file .env -v ccg18-data:/data ccg18`

## Teste

```bash
python -m pytest -q
python scripts/pii_scan.py
```

## Licență

AGPL-3.0: oricine poate folosi și modifica codul, iar cine îl oferă ca serviciu trebuie să publice modificările.
