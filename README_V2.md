# Checkni Auto V2

V2 mení pôvodné technické demo na rozhodovací report pre kupujúceho ojazdeného auta. Pôvodné scrapery zostávajú zachované, ale zákaznícky web, orchestration AI a formát výsledku sú nové.

## Čo je nové

- nový responzívny landing page a reportové rozhranie bez technických logov,
- asynchrónne úlohy s `job_id`; stav a výsledok prežijú refresh prehliadača,
- deterministické skóre úplnosti inzerátu,
- normalizácia údajov z `car_info.md` aj `raw_data.json`,
- nový doménovo správny a paralelný scraper pre Bazoš.sk/Bazoš.cz,
- paralelná analýza fotografií a webové overenie,
- štruktúrovaný JSON report namiesto surového Markdownu,
- dôkaz, istota, akcia a finančný dopad pri každom významnom zistení,
- bezpečný záložný report, ak zlyhá finálny AI syntetizačný krok,
- SK/CZ výstup, mobilné rozhranie, tlač do PDF a export JSON,
- SSRF allowlist, validácia uploadov, bezpečnostné hlavičky a denný beta limit.

## Architektúra

```text
URL / manuálny vstup
        │
        ├── existujúci scraper → car_info.md + raw_data.json + fotografie
        │
        ├── normalizácia + deterministická kontrola úplnosti
        │
        ├── paralelne:
        │     ├── Gemini vision → photo_analysis.json
        │     └── Gemini + Google Search → web_research.json
        │
        └── Gemini structured output → report.json
                  │
                  └── validácia / bezpečný fallback → zákaznícky report
```

Backend beží cez `v2_app:app`. Pôvodný `web_server.py` a `web/` zostávajú v repozitári ako rollback a referenčná implementácia.

## Lokálne spustenie

```powershell
python -m pip install -r requirements.txt
$env:GEMINI_PRIMARY_API_KEY="..."
$env:FLASK_SECRET_KEY="change-me"
python v2_app.py
```

Pri priamom lokálnom spustení cez Flask použite napríklad:

```powershell
$env:FLASK_APP="v2_app:app"
flask run --port 5000
```

Produkčný príkaz je uvedený v `Procfile`.

## Povinné premenné prostredia

| Premenná | Význam |
|---|---|
| `GEMINI_PRIMARY_API_KEY` | Primárny serverový Gemini API kľúč |
| `FLASK_SECRET_KEY` | Náhodný produkčný secret |

## Odporúčané premenné pre Render

| Premenná | Predvolená hodnota | Poznámka |
|---|---:|---|
| `CHECKNI_TEXT_MODEL` | `gemini-3.8-flash` | Finálny report a web research |
| `CHECKNI_VISION_MODEL` | `gemini-2.5-flash` | Analýza fotografií cez generateContent |
| `CHECKNI_MAX_CONCURRENT_JOBS` | `2` | Počet súčasne spracovávaných reportov |
| `CHECKNI_MAX_PENDING_JOBS` | `6` | Horný limit rozpracovaných a čakajúcich úloh |
| `CHECKNI_AI_TIMEOUT_SECONDS` | `90` | Timeout jedného AI modulu |
| `CHECKNI_SCRAPE_TIMEOUT_SECONDS` | `90` | Timeout scrapera |
| `CHECKNI_MAX_VISION_IMAGES` | `10` | Reprezentatívne fotografie pre vision modul |
| `CHECKNI_MAX_UPLOAD_MB` | `30` | Maximálna veľkosť manuálneho requestu |
| `CHECKNI_RATE_LIMIT_PER_IP` | `5` | Denný beta limit na IP |
| `CHECKNI_JOB_TTL_HOURS` | `24` | Uchovanie lokálneho jobu |
| `CHECKNI_ACCESS_MODE` | `beta` | `beta`, `open` alebo `development` |
| `CHECKNI_PRICE_EUR` | `1.99` | Zobrazená plánovaná cena |
| `CHECKNI_DATA_DIR` | systémový temp | Pre produkciu nastavte persistentný disk |
| `GEMINI_BACKUP_API_KEY` | prázdne | Voliteľný záložný kľúč |

## API V2

### Vytvorenie analýzy URL

```http
POST /api/v2/jobs
Content-Type: application/json

{
  "url": "https://www.autobazar.eu/...",
  "language": "sk"
}
```

Server vráti `202` a `job_id`.

### Manuálna analýza

```http
POST /api/v2/jobs/manual
Content-Type: multipart/form-data
```

Polia: `title`, `price`, `currency`, `source_url`, `manual_text`, `images`, `language`.

### Stav a výsledok

- `GET /api/v2/jobs/{job_id}`
- `GET /api/v2/jobs/{job_id}/events` — SSE
- `GET /api/v2/jobs/{job_id}/report` — finálny JSON
- `GET /api/v2/config`
- `GET /healthz`

## Platby

V2 obsahuje cenovú a kreditovú pripravenosť v UX, ale **nespúšťa Stripe checkout ani neúčtuje zákazníka**. `/api/v2/config` vracia `checkout_enabled: false`. Pred zapnutím platieb treba doplniť:

1. serverom overovaný checkout a webhook,
2. perzistentnú databázu kreditov a idempotency kľúčov,
3. pravidlo „kredit odpočítať až po validnom `report.json`“,
4. automatický refund kreditu pri stave `failed`,
5. obchodné podmienky, ochranu osobných údajov a fakturačný režim.

Nespájajte platbu iba s existenciou HTTP 200 odpovede. Úspech je až job v stave `done` s validným reportom schémy `2.0`.

## Testy

```bash
PYTHONPATH=. python -m unittest discover -s tests -v  # aktuálne 8 testov
python -m py_compile v2_app.py v2_pipeline.py
node --check web_v2/app.js
```

## Prevádzkové limity

- Joby sa ukladajú do `CHECKNI_DATA_DIR`. Bez persistentného disku sa po reštarte platformy stratia.
- Rozpracované joby sa po reštarte označia ako prerušené; nesmú spotrebovať platený kredit.
- Denný limit je zatiaľ in-memory ochrana beta prevádzky, nie produkčný billing ledger.
- Trhové porovnanie závisí od dostupnosti dôveryhodných, aktuálnych a indexovaných ponúk. Ak podklady nestačia, report musí cenu označiť ako neoverenú.

## Rollback

Na návrat k pôvodnému demu zmeňte poslednú časť `Procfile` z:

```text
v2_app:app
```

na:

```text
web_server:app
```
