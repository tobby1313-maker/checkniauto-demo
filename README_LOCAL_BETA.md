# CheckniAuto: okamžitý ručne spustený test bez cloudového úložiska

Produkčná vetva zostáva `v2`. Nová beta predvolene používa lokálne súbory a SQLite
pod `/tmp/checkniauto-beta`. Cloudflare, Supabase, persistentný disk a modelové API
kľúče nie sú potrebné. Nič sa automaticky nemigruje ani nemaže zo starého úložiska.

## Postup

1. Po deployi otvor web a priprav inzerát alebo vlož text a fotografie ručne.
2. Web uloží dostupné fotografie, označené koláže, text a trhové podklady. Po
   dokončení prípravy zobrazí `WAITING_FOR_AI` a pokyn na skopírovanie do ChatGPT.
3. Pošli pokyn v ChatGPT s pripojenou aplikáciou CheckniAuto. Nemusíš čakať do večera.
   Samotné tlačidlo na webe ChatGPT nespustí; nejde o automatický Pro backend.
4. S povolenými zapisovacími nástrojmi ChatGPT prevezme job a uloží validný report.
   Bez nich použi ZIP export a JSON import v `/beta-admin`.

Dáta môžu zmiznúť pri uspaní, reštarte alebo redeployi Render Free. Preto má zmysel
analýzu ihneď dokončiť alebo stiahnuť ZIP. Žiadna zmena zabraňujúca uspávaniu nie je
súčasťou tohto riešenia. Bezpečné ukladanie vydrží obnovu webovej stránky, nie reset
dočasného disku. Pri strate podkladov vlož inzerát znova. Reconnect MCP môže byť potrebný.

## Modely a fallback

- Preferovaný model v ChatGPT: **GPT-6 Pro**.
- Keď nie je dostupný: **GPT-5.6 Sol, Extra High** (`xhigh`).
- Model aj reasoning nastavuje operátor v rozhraní ChatGPT. MCP ich nemôže prepnúť,
  nepozná tvoje kvóty a nevie garantovať dostupnosť konkrétneho modelu.
- Web tieto hodnoty poskytuje ako `review_policy`, nie ako tvrdenie o skutočne
  použitom modeli. `actual_model` zostáva neoverený. Žiadny platený API fallback.
- Oficiálne automatické zníženie modelu v ChatGPT nemusí zvoliť Extra High. Skontroluj
  model picker pred ďalšou analýzou. Vyššie úsilie môže predĺžiť trvanie; okamžité
  spustenie znamená bez odkladu, nie nulový čas spracovania.

Overené produktové podklady:
https://help.openai.com/en/articles/20001354-gpt-5-6
https://developers.openai.com/api/docs/models/gpt-5.6-sol
https://developers.openai.com/api/docs/guides/developer-mode
https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt

## Nastavenie na Renderi

Ponechaj `web_server:app` a jeden Gunicorn worker. Predvolené nastavenia:

```text
CHECKNI_AI_MODE=chatgpt_beta
CHECKNI_STORAGE_MODE=local
CHECKNI_LOCAL_DATA_DIR=/tmp/checkniauto-beta
```

Explicitná hodnota `CHECKNI_STORAGE_MODE=cloudflare` stále používa pôvodný cloudový
adaptér; nenastavuj ju pre túto lokálnu betu. `legacy_api` je výslovný návrat k
pôvodnému platenému režimu, nie fallback novej bety.

Na ochranu administrácie a MCP sa použije `CHECKNI_OPERATOR_TOKEN`, alebo existujúci
`ADMIN_DASHBOARD_TOKEN` z token dashboardu. Tajomstvo sa nikdy nezapisuje do repozitára.
Odporúčaný nový token vygeneruješ lokálne:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Nastav ho iba v Render Environment. Nie je to OpenAI/Gemini API kľúč. Pri absencii
oboch nastavení príprava funguje, ale chránené prevzatie, export a MCP autorizácia
zostávajú zamknuté. Existujúce kratšie heslo dashboardu sa dá pre internú betu použiť;
prihlasovanie je obmedzené počtom pokusov. Pred rozšírením testovania ho vymeň za dlhý token.

## Pripojenie do ChatGPT

V Developer mode vytvor vlastnú MCP aplikáciu s OAuth:

```text
https://checkniauto.onrender.com/mcp
```

Server podporuje discovery, dynamickú registráciu, PKCE S256, jednorazové autorizačné
kódy a rotujúce refresh tokeny. Na autorizačnom formulári použi administračný kľúč
CheckniAuto, nikdy heslo do ChatGPT. Pri inom hostname nastav `CHECKNI_PUBLIC_ORIGIN`
na presný HTTPS origin; štandardne sa použije `RENDER_EXTERNAL_URL`.

Priame pripojenie z konkrétneho účtu ChatGPT musí byť overené živým testom.
Dokumentácia dostupnosti plných write tools pre Pro sa líši medzi Help Center a
vývojárskou príručkou. Keď sú dostupné len read tools, nastav
`CHECKNI_MCP_READ_ONLY=true`. Zápisy sa nesmú maskovať ako čítanie; použi JSON import.

Príklad pokynu:

> Spracuj CheckniAuto analýzu beta-<id>. Prečítaj pôvodný inzerát, pozri všetky
> prehľadové koláže a potrebné detaily. Ulož report, alebo vráť JSON na ručný import.

Toto je ručne spustená interná vývojová beta. Neautomatizuje ChatGPT prihlasovanie,
nepoužíva session cookies ako modelový backend, neobchádza limity predplatného a
nie je určená ako bezobslužná služba pre tretie strany.

## Kontroly

`/healthz` a `/_beta/config` zobrazia `storage_mode=local_ephemeral`,
`storage_persistent=false`, `ai_api_calls_enabled=false` a požadovaný `review_policy`.
Pole `cloud_configured=false` je v lokálnom režime správne; frontend používa
`storage_ready`.

Limity: 10 jobov za 24 hodín, 50 uchovaných jobov, 60 fotografií, 40 MB a 100 súborov
na job. Pri prekročení sa nové prijatie zastaví. Všetky dostupné fotky sú v galérii;
každá má vlastný stav posúdenia. Podobné fotografie sa nevyhadzujú a samotná tvorba
koláže sa nikdy neoznačuje ako AI kontrola.

Testy: `python -m unittest -v test_local_beta` plus existujúce beta/scraper testy.
