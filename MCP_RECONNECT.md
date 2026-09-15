# Oprava pripojenia: Unknown client or callback

Táto príručka dopĺňa README_LOCAL_BETA.md. Úložisko áut zostáva dočasné a nové
analýzy nevolajú platené modelové API.

## Príčina a hranice overenia

Predchádzajúci server generoval náhodné OAuth client_id a uchovával ich iba v
SQLite v /tmp. ChatGPT registruje DCR klienta raz a potom jeho ID používa opakovane.
Po vymazaní lokálneho disku preto nová autorizácia skončí chybou ešte pred heslom.
Pôvodná hláška tiež spájala chýbajúceho klienta s nezhodou callbacku; bez konkrétnej
požiadavky nemožno spätne rozlíšiť tieto dve príčiny iba z tejto hlášky.

Strata fotografií v bete je akceptovaná. Registrácia pripojenia však nemá závisieť
od životnosti fotogalérie. Server preto obsahuje preddefinovaného public OAuth
klienta, ktorý existuje aj pri úplne prázdnej databáze:

```
Client ID: checkniauto-chatgpt
Client Secret: nechať prázdne
Redirect URI: https://chatgpt.com/connector_platform_oauth_redirect
```

Client ID NIE JE heslo ani prístupový token. Stále je potrebné heslo operátora,
platný podpísaný formulár a cookie, správny Origin, PKCE S256, resource a scopes.
Callback je presne predregistrovaný, nie zástupný wildcard. Neznáme staré ID sa
neobnovujú na základe ľubovoľnej požiadavky. Chyba callbacku je samostatná.

## Jednorazový postup po nasadení opravy

1. Zavri staré OAuth okno. V nastavení MCP aplikácie CheckniAuto nastav manuálne
   preddefinovaného OAuth klienta podľa tabuľky nižšie. Ak existujúcu autentifikáciu
   nemožno upraviť, odstráň starú vlastnú MCP aplikáciu a vytvor ju znovu.
2. Použi:

   | Nastavenie | Hodnota |
   | --- | --- |
   | Názov | CheckniAuto |
   | MCP URL | https://checkniauto.onrender.com/mcp |
   | Autentifikácia | OAuth |
   | OAuth Client ID | checkniauto-chatgpt |
   | OAuth Client Secret | prázdne |

3. Over, že návratová adresa v správe aplikácie je presne
   `https://chatgpt.com/connector_platform_oauth_redirect`. Server inzeruje issuer
   identification a vracia `iss`, čo je podmienka použitia stabilného callbacku.
   Ak správa aplikácie ponúka callback-ID-specific adresu, neprepisuj allowlist
   náhodne. Taký klient potrebuje vlastnú presnú registráciu; jej dynamický variant
   je naďalej dočasný. Na túto betu použi nový predefined klient so stable callbackom.
4. Spusti Connect/Pripojiť. Na stránke CheckniAuto zadaj existujúce administračné
   heslo. Heslo sa NEVKLADÁ do poľa OAuth Client Secret.

Nové DCR registrácie s jediným stabilným callbackom tiež získajú tento public
client_id. Registrácie so špecifickými callbackmi zostávajú spätne kompatibilné,
ale ich riadky stále podliehajú strate dočasného disku. CIMD sa touto zmenou
neinzeruje ani neimplementuje; manuálny predefined klient ho nepotrebuje.

## Čo sa pri reštarte stále stratí

Prístupové tokeny, refresh tokeny, prebiehajúce autorizačné formuláre a kódy sú
stále dočasné. Po resetovaní Renderu môže byť nutné znovu kliknúť na Pripojiť a
zadať heslo. Nový predefined Client ID sa tým nemení a jeho registráciu netreba
vytvárať znovu. Rozpracovaný formulár cez samotný redeploy neprežije.

Existujúce fotografie a analýzy sa touto opravou neobnovia. Modelové preferencie
ani zapnutie platených API sa nemenia. Server MCP hlási verziu 1.2.0.

## Testy

- Pôvodný náhodný klient po strate databázy zostáva odmietnutý s návodom na opravu.
- Preddefinovaný klient funguje bez DCR registrácie a bez sieťového volania.
- Nový DCR klient so stabilným callbackom prežije vymazanie celej databázy.
- Skutočný Chromium prejde formulárom aj PKCE výmenou po strate registračného disku.
- Cudzí callback, chybné heslo, Origin, scope a PKCE zostávajú odmietnuté.
- Verejný Client ID nefunguje ako heslo ani Bearer token.
- Staré prístupové tokeny sa po strate disku neobnovia.

Oficiálne podklady:
https://developers.openai.com/plugins/build/auth
https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
