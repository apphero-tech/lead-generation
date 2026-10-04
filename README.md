# leadgen — US university contact lists (free sources only)

Builds one Excel file per state listing people who match 18 target profiles (head of institution,
enrollment, advancement/foundation, IT/CRM, continuing education/workforce) at every institution in IPEDS,
plus system offices. No paid API is used.

## Démarrage rapide (FR)

**Option A — dans le navigateur, sans rien installer (GitHub Codespaces)**
1. Sur la page GitHub du dépôt : bouton vert **Code** → onglet **Codespaces** → **Create codespace on main**.
2. Quand le terminal est prêt, taper : `tool ui`
3. GitHub ouvre l'interface dans un nouvel onglet (sinon : onglet **Ports**, ligne 8765, cliquer sur le globe).
4. Choisir l'état (et éventuellement la ville ou l'établissement), **Lancer la recherche**, puis **Télécharger le fichier Excel**.

Le Codespace s'arrête après une période d'inactivité : pour un état complet (plusieurs heures),
préférer l'option B sur un ordinateur qui reste allumé.

**Option B — sur son ordinateur (Mac/Linux, Python 3.9+)**
```bash
git clone https://github.com/apphero-tech/lead-generation.git
cd lead-generation
./install.sh
.venv/bin/tool ui        # ouvre http://127.0.0.1:8765/
```

Les résultats (base de contacts, fichiers Excel) restent sur la machine qui exécute l'outil et ne
sont jamais envoyés sur GitHub.

## Setup

```bash
./install.sh             # or: python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## Usage

Web interface (choose state, optional city or institution, watch the counters, download Excel):

```bash
.venv/bin/tool ui            # opens http://127.0.0.1:8765/ in the browser
# To accept other machines, set LEADGEN_UI_PASSWORD in .env, then: tool ui --host 0.0.0.0
```

Command line:

```bash
.venv/bin/tool run --state FL --unitid 134130     # one institution (University of Florida)
.venv/bin/tool run --state FL --name "valencia"   # institutions whose name contains a text
.venv/bin/tool run --state FL                     # whole state (resumable: re-run to continue)
.venv/bin/tool status --state FL                  # progress
.venv/bin/tool export --state FL                  # rebuild the Excel file only
.venv/bin/pytest                                  # tests
```

Output: `out/<STATE>/contacts_<STATE>.xlsx` (sheets: Contacts, Coverage, Stats, Read me).
Staging database: `data/leadgen.db` (SQLite). Logs: `logs/`.

## How it works

1. **Institutions**: IPEDS directory file (HD2024): every active institution in the state, and one
   entry per multi-campus system (`src/leadgen/systems.json` holds the system websites).
2. **Crawl**: each institution's own website, most promising pages first (cabinet, leadership,
   directory, advancement, foundation, IT, continuing education...). robots.txt honoured,
   1 request/second per host, every page cached (re-runs never re-download unless `--refresh`).
3. **Extract**: rule-based matching of titles to the 18 profiles (`src/leadgen/profiles.py`) and
   pairing with a person's name. Emails are attached only if they match the person's name.
   Small institutions: when nobody holds a target title, the closest junior title is kept
   (e.g. "Admissions Representative"), and the head of the institution comes from IPEDS if the
   website names no one. Everyone matching a profile is kept (e.g. one Director of Development per college), with their
   `unit` (college/office).
4. **Official directory search** (generic): the institution's people-search form is found
   automatically (directory.<domain>, or any crawled directory page), probed with people already
   found, then each person is searched by name. Email/phone come only from the result entry that
   carries that name; homonyms and name-less addresses are flagged. Results are cached.
5. **De-duplicate by person**: one row per person even when they cover several profiles.
6. **Emails**: `published` (on the page) or `deduced` (from the institution's email format, learned
   from at least 3 published addresses with a 60% majority). Deduced = always manual check.
7. **Export** to Excel with a coverage matrix and statistics.

Settings (page budget, delays, thresholds) are in `src/leadgen/config.py` and can be overridden
with environment variables `LEADGEN_<NAME>`, e.g. `LEADGEN_MAX_PAGES_PER_INSTITUTION=400`.
