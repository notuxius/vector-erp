# Ukrainian interface catalog

`../translations/uk.csv` is generated data, containing a GPL-3.0-or-later
derived translation catalog. The application code's MIT license does not
relicense this third-party catalog. Preserve this notice and the included
`LICENSE.erpnext-uk` when redistributing it.

Base: [erpnext-uk](https://github.com/v0980392409-spec/erpnext-uk), commit
`61eefb120ce4f9c040773ad221b65a2aac4714cb`, file `erpnext_uk/locale/uk.po`.
The original catalog, license and README are retained here. Its source
community contributors are laser, dvortsov, p.kulinich, oa.service.online,
vsevsimlutsk, meghna90, oddooptronix and v0980392409. It combines community
translations with machine translations; it has not been fully proofread.

Build precedence: pinned community catalog, installed Frappe/ERPNext Ukrainian
catalogs, then Vector ERP's `overrides.uk.csv` and `supplemental.uk.csv`.
The latter contain project terminology, corrections, new-version messages,
and additional source-extracted navigation, form, import, reporting, printing
and business-screen text. Context keys are retained; placeholders and HTML
tags are checked. JavaScript Unicode escapes and newlines are normalized.
The final branding pass replaces ERPNext in display prose with Вектор ERP and
generates `../translations/en.csv` with Vector ERP wording. Original message
keys, HTML attributes, URLs, code spans and parameter examples are preserved.
Copy both generated CSV files back after a build. `branding.verify` checks
both installed dictionaries and the installed app identifiers. The English
`public/js/onboarding_labels.js` adapter translates onboarding display fields while
preserving saved keys, matching the Ukrainian rendering adapter.
`navigation.uk.csv` also translates the standard onboarding records, dashboard
cards and navigation groups from the snapshot in `navigation_sources.json`.
The small `public/js/uk_ui.js` rendering adapter covers upstream dock and
onboarding methods that otherwise bypass translation. It restores original
values immediately after rendering so routes and saved progress keys stay intact.
It also supplies the missing Ukrainian date-picker locale and translates native
button hover titles whenever the button caption is refreshed.
Recent and frequent command-search results translate view names before adding
HTML emphasis, and display dashboard names instead of the internal route token.
Routes and business record names remain unchanged. Run
`node scripts/check_uk_search.cjs` to check this rendering regression.

`configuration.uk.csv` contains the reviewed settings, departments, designations,
roles, letterheads and print-format names reported on 2026-10-14. These keys are
included in the coverage audit. The Ukrainian adapter translates configuration
list text and link captions while preserving filter attributes, checkbox values,
document names and role assignments. FIFO/LIFO retain their standard abbreviations
with Ukrainian explanations. Custom names without catalog entries are unchanged.
The `configuration_localization` migration translates only exact, untouched English
defaults in Support Settings and the Dispatch Notification email. It preserves
custom content, HTML structure and Jinja variables and runs only on Ukrainian
sites. Its verification uses rolled-back fixtures and is included in `erp.ps1 verify`.

The 2026-10-14 wording review covers the main sales, purchasing and stock
document fields, child tables, settings descriptions, action/validation messages,
and navigation/report names. `business_review.json` records each correction,
its previous wording and source references. Use `товар` for individual items,
`замовлення клієнта`, `замовлення постачальнику`, `рахунок продажу`,
`рахунок постачальника`, `надходження товарів` and `складська операція`
consistently. Billing means issuing an invoice, not receiving payment. Use
`націнка` for pricing markup; preserve the generic layout meaning of Margin
with a `Pricing Rule` context override. Dates, codes, Python examples, document
identifiers and user-entered values must not be translated as prose.

`coverage.json` measures the installed `main.pot` catalogs plus the additional
source messages in `supplemental_sources.json` and `navigation_sources.json`.
It also scans installed Dashboard Chart JSON files for `empty_state_message`,
which the chart renderer translates dynamically but the gettext catalogs omit.
All 44 chart descriptions in the installed version have Ukrainian translations
in `navigation.uk.csv`, including quality inspection and asset value charts.
`source_audit.json` is the
initial supplemental gap audit from Frappe's native `get_messages_for_app`.
The supplemental set excludes compiled-bundle duplicates, test/demo component
examples, naming series, known identifiers and already translated messages.
`keep_english.json` documents deliberately unchanged technical strings,
product names, code, document-number formats, and other non-language content.
This is string coverage, not a claim of linguistic perfection or visual
verification of every screen. User-entered data is not translated or renamed.

Public `/login` and `/update-password` pages use the site language before
rendering their native templates, so English browser headers or old guest
language cookies do not override the Ukrainian site default. The
`website_localization.before_request` hook preserves explicit `_lang` URLs,
signed-in user preferences, API requests and other website pages. Login and
forgot-password captions and client validation use the existing catalog;
authentication and password-reset handlers are unchanged. `check_local.py`
checks guest English-header/cookie cases, explicit English requests, and login.

The standalone Banking React app initializes module-level labels before its
upstream asynchronous translation fetch completes. `infra/patch_banking.py`
initializes `_messages` synchronously from the existing session boot dictionary
and localizes the page title, in both source and generated HTML entry points.
The patch is applied while building the pinned image and asserts its expected
upstream structure. No React bundles, bank data or reconciliation handlers are
modified. The 483 literal translation calls in the Banking source were checked
against the catalog; only numeric/format tokens remain unchanged. Authenticated
Banking and statement-importer entry checks are included in `check_local.py`.

The Fixed Asset Register's status options and group/period prompts are included
in `configuration.uk.csv`. Native Select controls translate their display labels
while preserving the original filter values used by the report queries.

The pinned ERPNext trend helper formats English month abbreviations directly.
`infra/patch_trend_translations.py` adds a context-aware translation lookup and
removes the localized amount suffix from Sales Order Trends chart captions.
Only display captions change; report dates, item codes and amounts are preserved.
The Selling dashboard's Item-wise Annual Sales heading is included in the catalog.

Regenerate explicitly in the matching ERP image:
```powershell
docker compose --project-name vector-erp exec -T backend bench --site frontend execute warehouse_core.localization.build
docker compose --project-name vector-erp cp backend:/home/frappe/frappe-bench/apps/warehouse_core/warehouse_core/translations/uk.csv apps/warehouse_core/warehouse_core/translations/uk.csv
docker compose --project-name vector-erp cp backend:/home/frappe/frappe-bench/apps/warehouse_core/warehouse_core/translation_sources/coverage.json apps/warehouse_core/warehouse_core/translation_sources/coverage.json
.\scripts\erp.ps1 start
docker compose --project-name vector-erp exec -T backend bench --site frontend execute warehouse_core.localization.verify
```

Deploy edited source files in the image before regenerating. Generation is
never performed by install/migrate hooks and requires no network service.
Refresh the source audit when upgrading ERPNext; newly introduced strings
may need translations. Keep terms such as submission = document posting
(`проведення`) and account = ledger account (`рахунок`) context-sensitive.
