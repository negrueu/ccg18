# CCG 18 - bot Telegram pentru asociație (demo) - design

Data: 2026-11-04 · Aprobat de beneficiar · Autor: Ketu

## Intenție
Cooperativa de construcție a garajelor nr. 18 (Chișinău) nu are un canal digital prin care membrii să voteze, să primească anunțuri și convocări, să-și vadă datoriile pe garaj și să trimită sesizări. Construim un bot Telegram + un panou web pentru conducere. Mâine (2026-11-05) se prezintă asociației ca **demo funcțional**; dacă prinde, devine produs pentru și alte cooperative/asociații.

## Ce a decis beneficiarul
- Identitatea vine **exclusiv din lista asociației** (încărcată de conducere). Un număr de telefon poate avea mai multe garaje.
- Intrare și vot **doar prin Telegram**: butonul „Trimite numărul meu” (request_contact); numărul se compară cu lista. Fără SMS, fără Viber (Viber = 100 EUR/lună/bot, amânat).
- Funcții în demo: vot, anunțuri, convocare, cotizații (QR demo), sesizări, panou conducere.

## Deciziile constructorului (de verificat de beneficiar)
- Cine nu are Telegram: votul pe hârtie e introdus de un admin în panou, marcat „introdus de <admin>” în procesul-verbal.
- Bot bilingv română/rusă, limba aleasă la primul mesaj.
- Ponderea (un vot pe membru / un vot pe garaj) și caracterul (secret / deschis) sunt **setări per vot** până se vede statutul. Implicit: pe membru, deschis.
- Un membru votează o singură dată per vot; votul e final după confirmare.
- Vot secret: se păstrează CINE a votat (anti-dublu) separat de CE a votat (fără legătură între ele).
- Procesul-verbal = pagină HTML de printat („Salvează ca PDF” din browser), nu PDF generat pe server.
- Stack: Python 3.12, aiogram 3 (polling), FastAPI + Jinja2 pentru panou, SQLite, un singur proces, Docker (Coolify).
- Intrare în panou: admin trimite `/panou` în bot, primește link unic valabil 10 minute.
- Date fictive: 30 membri, 40 garaje, pe 4 rânduri; telefoanele reale de demo se adaugă din panou sau din `ADMIN_PHONES`.

## Non-obiective (demo)
Plată reală (MIA QR cere contract CCG-bancă); valabilitate juridică a votului la distanță (neverificată: statutul nu e disponibil, legis.md inaccesibil automat); multi-asociație; Viber; SMS.

## Anti-obiective
- Cineva votează în numele altuia (ex. trimite contactul altcuiva) - **inacceptabil**.
- Un vot numărat de două ori sau un membru care nu e pe listă votează.
- Telefoanele membrilor apar în procesul-verbal sau în orice ieșire publică.

## Rămâne la constructor
Aspectul vizual al panoului, textele exacte ale mesajelor, structura internă a modulelor.
