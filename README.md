# Vector ERP / Вектор ERP

Cloudflare quota protection is active at a 95% usage threshold for the MTech bot and both Pages sites. Private Telegram alerts notify the owner at 90%, at the 95% cutoff, and on recovery. Email alerts are intentionally disabled pending sender setup; the planned recipient is notuxius@gmail.com. See [quota thresholds and operations](docs/cloudflare-quotas.uk.md).

Vector ERP is a local business management system powered by ERPNext, with a Ukrainian interface. It supports the planned migration of an existing MoySklad client. `warehouse_core` adds a Ukrainian operations workspace, role profiles, and migration reference fields while retaining ERPNext's native transaction logic.

The **MTech Telegram bot** runs on Cloudflare Workers + D1 at `https://mtech-telegram-bot.notuxius.workers.dev`, using Telegram webhooks and a durable notification queue. Its source is in `services/telegram_worker`; shared Ukrainian content remains in `services/telegram_bot/content.json`. Run `npm test` in the Worker directory for behavioral parity, transaction, concurrency, and webhook checks. The Python simulator remains available with `python -m services.telegram_bot simulate`. The former Docker poller is stopped; do not start it while the cloud webhook is active. See the [Ukrainian setup and operations guide](docs/telegram-bot.uk.md).

The owner’s Telegram `/ai question` uses the relay and spending gateway on
Contabo VPS 6. Model calls use the configured ERP budget. Direct `/ai залишки` lookups
remain available when AI spending is blocked. It shares the ERP ledger and never puts the OpenAI
key in Cloudflare. See [pilot setup and current acceptance status](docs/telegram-ai-pilot.uk.md).
The dedicated Telegram ERP Reader account can query stock, reservations, and
permitted knowledge articles through two restricted APIs. Use `/ai залишки`
or `/ai залишки: item name`; edits, payments, and order access are blocked.
The owner's **Вектор ERP** menu provides separate buttons for direct stock,
product search, AI questions, and knowledge questions. Search/AI buttons accept
the next message without a command prefix; customer drafts remain separate.

The **Адміністрування / Administration** module contains administrative configuration and retains its existing `Setup` identity and routes. The user avatar menu offers **Платформа / Platform** for framework tools and **Налаштування / Settings → Profile** for personal preferences. The return link from Platform to ERP remains in the user menu and navigation logo. **Усі застосунки** is absent from the sidebar header dropdown. The **Вектор ERP** application tile and other application entry links open **Огляд** (`/desk/operations-overview`).

## Available now

- Products, services, units, groups, prices, customers, suppliers, contacts, and warehouses.
- Purchase order → receipt → supplier invoice → payment.
- Sales order → delivery → customer invoice → payment.
- Order readiness dashboard with due/overdue orders, stock shortage estimates, invoice payment status, assigned users, and order/delivery links.
- Read-only sales-order completion check separates shipment, invoice settlement, and return review. The form clarifies that native `Completed` means shipped and billed, not necessarily paid. Outstanding refund/credit balances are identified separately. No automatic business completion or new shipment restrictions are imposed.
- Warehouse transfers, returns, inventory counts, and stock reports.
- Sales, purchasing, warehouse, accounting, and operations-manager role profiles.
- Unique optional MoySklad reference IDs on 21 master/transaction document types.
- Serialized CNC machines, configuration notes, service visits/history, and native manufacturing workflows.
- Expected production report with work-order drilldown, separate physical stock/purchase expectations/reservations, and partial-output/process-loss handling.
- Serialized assembly installations with two-way machine links, removal/reinstallation history, duplicate-installation protection and inherited access restrictions. [Ukrainian guide](docs/production-and-assemblies.uk.md).
- Daily dispatch list with partial-shipment quantities and warehouse permissions; domestic Nova Poshta waybill creation and on-demand tracking.
- Ukrainian payment-request/invoice and delivery-note print formats; an operation audit journal records the submitting/cancelling user.
- Vector print formats use Chrome for PDF generation, with the image's bundled Chromium headless shell. Migrations upgrade their legacy wkhtmltopdf setting while preserving customized templates and other explicitly selected renderers.
- Five replaceable example users and an explicit, repeatable real-staff roster import.
- A separate opt-in demo setup with sample products, stock, and draft orders.
- Optional CNC training production: four partially completed orders, a machine with serialized assemblies, completed spindle replacement history and a spare for practice. [Scenario and expected quantities](docs/production-demo.uk.md); seed with `scripts/erp.ps1 demo-production` after basic demo setup.

Open [Vector ERP → Dashboard](http://localhost:8080/desk/operations-overview). This shared homepage covers Sales, Accounting, Purchasing, and Stock with its own overview sidebar and no module prefix. Needs attention appears first in both the sidebar and page content, followed by Sales, Accounting, Purchasing, Stock, and Quick access. The chart period is selected from the toolbar button immediately after Appearance (3, 6, or 12 months). Sidebar links scroll to these sections and highlight the current section. The Needs attention badge totals all permitted overdue sales and purchase orders, independently of the six-row previews, and updates on refresh. Unavailable sections are omitted. The sidebar uses the standard collapse/expand control and mobile drawer; module pages retain their own navigation. It shows open and overdue sales/purchase orders, unpaid customer/supplier invoice counts, and draft delivery/stock documents, across all accessible companies, with record drilldowns. Counts and lists respect the signed-in user's permissions. Refresh on demand or return to the page for current data. The Stock workspace remains at [Склад](http://localhost:8080/desk/stock). See [the Ukrainian walkthrough](docs/basic-workflows.uk.md).

ERP page headers show the module and sidebar section before the existing page title (for example, **Склад → Звіти операцій → Готовність замовлень до відправлення**). The path follows the user's available navigation; module destinations are clickable when configured. Overflowing labels use an ellipsis, with their full text available in styled tooltips on hover or keyboard focus and to assistive technology. Row and breadcrumb tooltips appear only while their text is clipped; fitting labels stay quiet, including after resizing.

The location path and page title stay on one line at every width, preventing breadcrumb wrapping from moving the title and controls vertically during resizing. Header actions move together to a separate row when the title and controls need more space, including after sidebar resizing or switching between compact and full-width layouts. Search and notifications share the page action row when outside the top module bar; their text labels appear when space permits and collapse to accessible icons when needed. Long titles use the available width on phones. Run `node scripts/check_header_layout.cjs` with Playwright available to check header resizing across lists, forms, workspaces, and reports.

**Список** and **Вигляд** stay together beside the document actions on the right when the title and controls fit on one row. When controls wrap below the title, the preferences stay at the start of that row, with spare space before the document actions. The view selector remains available when resizing through desktop, tablet, and phone widths; its label stays visible below the native 768px breakpoint when the header has at least 480px of space. Narrower mobile layouts use its native icon button. The header check also covers fresh mobile loads and keyboard access to both menus.

The MTech header logo opens Settings → Home (`/desk/home`), including the logo beside the sidebar's module title. Its tooltip and accessible label identify that destination. Other apps retain All apps (`/desk`). The title and dropdown arrow still open the sidebar menu.

The POS opening popup includes **Назад** and a Close button; Escape also returns to the previous Desk page (or the configured Overview homepage when opened directly). Leaving does not submit an opening entry. Browser navigation dismisses the popup, and returning to POS checks for an opening entry again. `public/js/pos_navigation.js` keeps native submission and validation intact; run `node scripts/check_pos_navigation.cjs` against the running site to verify cancellation, re-entry, browser navigation, and mobile controls.

Desktop popups keep the module navigation and inner side subpane visible behind the modal backdrop, including on POS and other pages that normally hide them. Closing the last popup or minimizing it restores the page's normal navigation visibility without changing saved sidebar preferences. Mobile retains its drawer layout. `public/js/dialog_navigation.js` applies this shared policy; run `node scripts/check_dialog_navigation.cjs` to check POS, nested dialogs, Settings, responsive controls, and layout restoration.

All Desk dialogs open horizontally and vertically centered in the viewport, including Edit Sidebar and Profile Settings. Tall dialogs remain reachable by scrolling from the top margin, and minimized dialogs keep their native bottom-edge position. All Desk dialogs use normal-width scrollbars when their content overflows, including Profile Settings and nested popup panels on smaller screens. Scrollbar space is reserved by the native page scroll area and settings panels to reduce layout shifts; the outer document does not reserve a second gutter. Background scrolling stays locked while a popup is open. Settings uses the available desktop width up to 1024px, wraps header actions, and stacks profile details and fields as the content pane narrows. Below 768px, settings tabs move above the content in a horizontally scrollable row. `public/css/dialog_scrollbars.css` applies the shared styling.

Every Desk page header includes **Вигляд**, with **Тема оформлення** (light, dark, automatic) and **Макет** (compact, full width). Changes apply immediately through the same native controls as User Settings: theme is saved for the signed-in user and layout uses the existing browser preference. The menu reads current preferences each time it opens, supports keyboard navigation, and closes when switching pages. `public/js/page_appearance.js` keeps the button outside page-specific actions so form/list refreshes retain it, and places it in the separate All apps navbar too. Run `node scripts/check_page_appearance.cjs` with Playwright available to verify header coverage, Settings synchronization, persistence and keyboard/mobile behavior against the running site.

The desktop module menu is fixed at the top. It keeps module labels visible and scrolls horizontally with its arrows, mouse wheel, or trackpad when needed. Saved left-panel and floating preferences are ignored without changing User records. The inner module sidebar stays on the left.

**Пошук** and **Сповіщення** stay in the top panel at every screen width and never appear in the left menu. The top module layout places them beside the user avatar; mobile places them at the top right of the page header. These are the native controls, retaining search, notification counts, and access settings. Notifications open beneath the controls on the right. Narrow screens use icons with accessible labels.

Inner sidebar sections remember their expanded or collapsed choices in this browser, separately for each module. Choices survive reloads, module switches, and collapsing/reopening the panel; highlighting the current page respects an explicitly collapsed group. `public/js/navigation_sections.js` repairs upstream section-state overwrites. Run `node scripts/check_navigation_sections.cjs` against the running local container for regression checks.

The inner sidebar's module dropdown sits at the bottom, outside the scrolling navigation list, and opens upward. Account access stays in the inner left sidebar on mobile and in the module panel on desktop. The module trigger's chevron points upward. **Допомога / Help** lives in the user menu, below **Reload**, with the existing page-specific and site help links; it is absent from the module dropdown beside **Редагувати бічну панель**.

The **Огляд** dropdown also offers **Редагувати бічну панель**, with the shared width and automatic text-fitting settings; its section links remain generated from the overview. Sidebar width is adjusted only through the **Максимальна ширина бічної панелі (пікселі)** slider in **Редагувати бічну панель**. The expanded panel has a 100px minimum and a 400px maximum. The default selected maximum is 400px with text fitting enabled. Desktop window space can reduce the displayed width to reserve at least 320px for page content. Mobile uses the same saved width and text fitting; its overlay drawer only reduces the displayed width when necessary to leave a 60px backdrop area for click-away dismissal. A narrow viewport never overwrites the saved maximum. The adjacent pixel input supports exact values and up/down arrows; it stays synchronized with the slider. Changes preview immediately and are remembered per user in this browser. The **Підлаштовувати ширину під найдовший текст, не перевищуючи вибраний максимум** checkbox is enabled by default and fits the current module's labels, including nested entries, within the slider's maximum. Existing preferences receive the 400px maximum and enabled text fitting once; later width and checkbox choices are remembered. Turning it off restores the selected width. The editor stays centered while adjusting the sidebar. The right edge has no resize control; the arrow still expands and collapses the panel.

Opening a submenu group from the collapsed sidebar keeps it near the cursor and reveals its entries directly below, within the panel's scrolling limits. Near the bottom edge it moves up enough to show the first entry. Keyboard activation uses the same positioning. Select an entry to open its page. Run `node scripts/check_navigation_group_reveal.cjs` with Playwright available to verify positioning, group access, and destination selection in Sales and Accounting.

Below 768px, the workspace picker becomes a left overlay drawer with full-width labeled module rows. Its 44px menu button stays beside the current page title. Tap the button or swipe right from the first 32px of the left edge to open it; swipe left, tap Close or the backdrop, select a module, or press Escape to dismiss it. The drawer begins below the title row, leaving the current page name visible without shifting the page. Vertical scrolling, swipes within page content, and multi-touch gestures do not open it. Keyboard focus stays within the open drawer and returns to its menu button on dismissal. The module list scrolls independently on short screens. Returning to desktop restores the fixed top bar.

On mobile, the avatar and user-name link stay together at the bottom of the inner left sidebar. The workspace drawer contains module navigation without a duplicate account row. The account menu opens above the sidebar footer and retains native keyboard access.

Every module also retains its existing inner-sidebar toggle beside the page title: right to expand and left to collapse. On mobile, dismiss the separate sidebar by tapping its backdrop or using the sidebar keyboard shortcut. `public/js/navigation_mobile.js` supplies the responsive workspace behavior; run `node scripts/check_mobile_dock.cjs` to verify overlay placement, swipe direction, title visibility, keyboard, touch, routes, and attached desktop restoration.

Both navigation panels scroll independently with the mouse wheel or trackpad, without visible scrollbars. Up/down buttons appear above and below an overflowing list and scroll by most of the visible list height; they disable at the corresponding end. The expanded inner sidebar keeps its labels and cannot be resized below 100px. Its native collapsed rail retains 40px square targets; the arrow restores the expanded panel. Navigation tooltips show the full item text when a label is truncated; fully visible labels stay quiet, and icon-only controls keep their tooltips. On mobile, the user name stays to the right of the avatar in the inner left sidebar, truncating when needed; the mobile workspace drawer contains no duplicate account row. The top panel also shows the name to the left of the avatar when it fits without making the module menu scroll; narrower widths keep the avatar alone. User labels preserve the full surname and abbreviate the first and middle names: **Шевченко Т. Г.** in Ukrainian and **T. H. Shevchenko** in English. Formatting uses the profile’s separate name fields, preserves their spelling, and leaves accounts without a surname unchanged. Hovering a truncated name reveals the full name; an abbreviated name that fits stays quiet. Run `node scripts/check_user_navigation.cjs` to verify language order, initials, responsive placement and menus without changing User records.

The inner sidebar keeps its saved width when reopened and collapses to a 56px icon rail on desktop.

Chart redraws safely replace temporary animation SVGs when the window or chart container resizes. `public/js/chart_rendering.js` prevents Frappe Charts from removing a detached SVG and stops old animation timers from restoring an obsolete drawing. Animation options and chart values are preserved. Run `node scripts/check_chart_rendering.cjs` to exercise entry/update animations, resize observers, and orientation changes, and `node scripts/check_header_layout.cjs` for the responsive Desk regression.

Tutorial videos are removed across Desk: onboarding thumbnails/players, video help links, and Item/BOM video help. Written onboarding instructions, actions, and progress remain available. The install/migrate hook clears both onboarding video fields, and `public/js/tutorial_media.js` prevents tutorial media from rendering or loading again, including lazily loaded help.

The demo uses Ukrainian (`uk`) for the system and Administrator interface. Vector ERP includes over 18,600 catalog entries covering navigation, forms, reports, settings, imports, printing, help text and validation messages across Frappe and ERPNext. Technical identifiers, product names and business records remain unchanged. The catalog combines a pinned community translation with the installed Ukrainian pack and project corrections; some inherited wording is machine-translated and may need proofreading. See [catalog provenance, coverage and maintenance](apps/warehouse_core/warehouse_core/translation_sources/PROVENANCE.md). Hard-refresh the browser after an update to load new translations. Each user can select their language in **Мої налаштування → Мова**; **Налаштування системи → Мова** controls the fallback.

In **Налаштування варіантів товару → Копіювати поля до варіанта**, the field table and editor display translated Item labels. The saved `Variant Field.field_name` values remain internal identifiers; the unlabeled UOM conversion table uses **Дані перерахунку одиниць виміру**.

Settings forms also translate sidebar headings, field hints, navigation/portal captions and POS field selectors. Displayed captions round-trip their original stored keys when left unchanged. Routes, API parameters, naming patterns, business records and role assignments remain intact. The 2026-10-14 settings review covers metadata for 41 settings forms and records 36 wording corrections in `translation_sources/settings_review.json`. Run `node scripts/check_settings_ui.cjs` to verify caption editing and identifier preservation.

The product name is **Vector ERP** in English and **Вектор ERP** in Ukrainian. The internal Frappe app/package remains `warehouse_core`. Its operations screens now live under **Вектор ERP → Склад**, in **Відправлення та контроль** and **Звіти операцій**. The former Warehouse Core workspace is retained as hidden configuration; document identities and permissions are preserved. MoySklad references describe the external source system and its migration IDs.

The Desk home screen displays the ERPNext app as **Вектор ERP** in Ukrainian and **Vector ERP** in English, beside **Фреймворк**. This display title is applied through Warehouse Core's session boot hook; ERPNext's app identifier, routes, permissions, and business records stay unchanged.

Interface references to ERPNext use **Vector ERP** in English and **Вектор ERP** in Ukrainian, including settings, onboarding, About headings and field help. These are catalog overrides applied during translation builds; upstream source keys, package/version identifiers, documentation links and example code remain intact. The company MTech icon is retained on the Vector ERP tile.

The framework app and sidebar heading use **Framework** in English and **Адміністрування** in Ukrainian. Its link appears directly above Settings in both ERP user menus instead of inside the settings dialog. In Framework, that entry becomes **Return to MTech ERP / Повернутися до MTech ERP** and opens the ERP dashboard. The top-left **Return / Повернутися** button has the same destination; both controls show the MTech mark with a small return arrow in its bottom-right corner. The internal `frappe` application and existing navigation identities are retained.

The Vector ERP app icon uses the blue M from the [company's original logo](https://mtech.com.ua/wp-content/uploads/2015/09/mtech-logo.png). `public/images/mtech-mark.svg` selects the M with an SVG viewport and embeds the original artwork, so the icon needs no external request. The unmodified source is kept as `mtech-source-logo.png` beside it. The boot hook supplies a content-versioned asset URL.

Selected module icons and the Dashboard mark use the exact dominant colour sampled from the displayed app logo: **#248CC8** for MTech. Both icon tones use the same opaque colour, without lightening or darkening it. Unselected icons remain neutral. The Framework return button uses the original 24px MTech logo and a separate theme-aware corner arrow, so the M has the same proportions as the ERP icon. `public/js/navigation_logo.js` ignores transparent pixels and neutral backgrounds, caches colours per image URL for the browser session, and follows logo source changes; monochrome or unreadable images fall back to the theme's neutral ink. Run `node scripts/check_navigation_logo.cjs` with Playwright available to check exact logo colours, light/dark themes, stale image loads and navigation without saving branding changes.

Vector ERP uses the **Flow** mark, a blue and teal ribbon V, in `public/images/vector-mark.svg` for the All apps loading screen and login/app logo. The browser favicon uses the MTech ERP company M from `public/images/mtech-mark.svg`. When opening or refreshing an app page, the loading screen uses that app's icon: the company M for Вектор ERP and the Framework icon for Framework. The website-context hook resolves the requested route from the user's permitted navigation and embeds the icon in the initial server HTML, before Desk JavaScript loads. Pages without a selected app retain the Vector mark.

## Run locally on Windows

Requires Docker Desktop running Linux containers, Docker Compose, and Python 3.

```powershell
.\scripts\erp.ps1 start
.\scripts\erp.ps1 status
.\scripts\erp.ps1 logs
python scripts/check_local.py
```

The first run downloads ERPNext, builds our app into the image, and creates the site. The `create-site` service installs/upgrades Warehouse Core and runs migrations. Wait for it to exit successfully and `http://localhost:8080/login` to respond before using the site.

- Address: http://localhost:8080
- Username: `Administrator`
- Password: `ADMIN_PASSWORD` in the local `.env` file
- Site name inside containers: `frontend`
- Local image: `vector-erp:0.1.0`. The Dockerfile pins the upstream image digest for ERPNext 16.50.0 and Frappe 16.52.0; the version tag alone has served different framework versions.

**Local testing and remote releases.** On the configured Windows demo, run `.\scripts\erp.ps1 autodeploy` once. The watcher starts now and at sign-in. The watcher needs Git, Node.js and Python with Kyiv time zone data (the project virtual environment includes it). While Docker Desktop and the demo are running, it checks the ERP app, infrastructure, Compose, gateway, relay, Telegram Worker and shared bot content every 20 seconds, including uncommitted edits. Two matching scans trigger one build. It backs up the site with files and the spending ledger, applies the local migration, and verifies login, installed code, rollback-only transactions and the spending guard. Only the VPS runs the live Telegram relay and enables AI spending.

Test at [the local dashboard](http://localhost:8080/desk/operations-overview). Get the verified release ID with `python scripts/local_release.py --candidate`. A failed build has no release ID; inspect `.artifacts/local-release/deploy.log`, fix the issue or run `python scripts/local_release.py` to retry. The watcher leaves a stopped demo stopped. To disable it, remove **Vector ERP local deploy** from the Windows Startup folder and sign out.

After the owner accepts that ID, use `python scripts/local_release.py --pack RELEASE_ID` to export the **tested images**, their digests and the matching ERP and Telegram Worker sources into `.artifacts/local-release/RELEASE_ID/`. A stale ID is rejected. On Contabo (`vector-erp-vps6`, project `/opt/vector-erp`):

1. Back up the site with files, the SQLite spending ledger and current images. Check for server-only source changes before syncing the reviewed source archive. Keep the VPS environment files, Compose override and volumes.
2. Load `images.tar` with `docker image load -i`; check the Linux/amd64 platform digest of each loaded tested tag (`docker image inspect --platform linux/amd64 --format "{{.Id}}" TAG`) against `release.json` before tagging the images as `0.1.0`. Use the existing VPS Compose files (`compose.yaml`, `compose.override.yaml`, `compose.ai-telegram.yaml`). Enter maintenance, drain workers and the relay and wait for active model requests to finish. Recreate the backend and gateway without rebuilding or pulling, migrate the existing site, then recreate the frontend, websocket, scheduler, both queues and relay and leave maintenance.
3. Verify the running images, installed apps, login and [spending guard acceptance](docs/ai-spending-control.uk.md) with the existing receipt. Run rollback-only transactions on a restored demo copy if production is not marked as a demo. Telegram Worker code and shared bot content have a separate release: run the Worker tests, review the matching content changes, then publish the approved Worker. Never point a local poller at the live bot.

The proxy preserves the browser's Origin for realtime authentication. When the initial Socket.IO polling request omits Origin, the proxy reconstructs it only for requests marked `Sec-Fetch-Site: same-origin`. `infra/patch_realtime.py` adds the trusted `FRAPPE_REALTIME_BACKEND_URL` override so the websocket service contacts `http://frontend:8080` inside Docker while retaining Frappe's origin validation. `check_local.py` checks both local hostnames and rejects a mismatched origin. The build deliberately fails if the upstream URL helper changes and the patch needs review.

The current laptop company is **MTech**, matching its brand and logo. Ukraine, the Ukrainian interface, two warehouses, and UAH/USD are confirmed by the latest written answers; the demo's base currency remains UAH. Real-user count (7 versus 9), base-currency policy, legal/bank/tax details and actual records are still pending. The user has authorized clearly labeled examples for unknown settings. A new production site will use the actual client records and accounting settings. See [confirmed requirements](docs/client-requirements.uk.md) and the original [questionnaire](docs/client-discovery.uk.md).

The existing company name is restored from **ТОВ «ЕМ ТЕК»** to **MTech** with the explicit, repeatable `warehouse_core.company_identity.apply(dry_run=False)` migration. The app remains **Vector ERP / Вектор ERP** and the logo/brand remains **MTech**. The company abbreviation `MTech` is retained in account and warehouse identifiers. The three working warehouses are **Основний склад**, **Додатковий склад**, and **Виробничий склад**. Native renames preserve links and defaults, refuse collisions and merges, and compare record identities, ledger amounts, stock quantities, external MoySklad IDs, hierarchy, roles, user permissions and sharing before and after. Use `apply()` to preview and `apply(dry_run=False, rehearsal=True)` to test with rollback; the migration never runs automatically during installation. Legacy demo rename entry points delegate to this checked migration. Historical audit records retain former names.

The explicit local migration `warehouse_core.sales_directory_names.apply(dry_run=False)` renames the sales root to **Відділ продажів**, the sample service person to **Сервісний спеціаліст** (removing DEMO), and the territory records to **Усі території**, **Україна**, and **Решта світу**. Native renames preserve document links; the migration checks tree structure, native links, balances, quantities and roles, refuses collisions, and can be repeated. No records are deleted. Sample seeding reuses these localized names when present.

On a new empty sandbox, opt in to the same demo setup with:

```powershell
.\scripts\erp.ps1 demo
.\scripts\erp.ps1 verify
```

`demo` refuses to run if the site contains another company. It reuses existing demo records and never resets stock or duplicates the opening entry. `verify` runs real submitted transactions using a temporary product, then rolls them back. It also verifies source-ID uniqueness, insufficient-stock rejection, and role boundaries. Run it only on the demo site. It does not test jurisdiction-specific accounting compliance or client integrations.

```powershell
.\scripts\erp.ps1 stop
.\scripts\erp.ps1 backup
```

Stopping preserves data. Database and site files live in Docker volumes under the `vector-erp` project. `backup` copies database, files, and the generated site configuration backup to an ignored local directory. Keep `.env` and backups private; verify a restore before relying on backups for production. Do not run `docker compose down -v` unless deliberately deleting this sandbox.

The existing local sandbox was migrated to `vector-erp` on 2026-10-13 by copying its stopped data volumes after a full backup. The original `moj-sklad-erp_*` volumes remain as recovery snapshots and are not used by the running project. Fresh installations use only `vector-erp_*` volumes.

Only the web interface is published, bound to `127.0.0.1`. This is a local evaluation environment, not a production deployment. Production hosting, HTTPS, recovery, and integration secrets will be handled after requirements are known.

## Implementation approach

The local AI gateway uses a $7 usage budget. It sends private Telegram alerts at
75% and 95% and reserves estimated costs before accepting a request. The 95%
cutoff is $6.65. The gateway uses its persistent usage ledger without checking
provider funding or grant dates. It also enforces pricing verification and
holds for uncertain requests. See [AI spending control](docs/ai-spending-control.uk.md).
The round **✦** button at the bottom right of Desk opens **Vectoria ✦ AI-assistant** (Ukrainian: **Векторія ✦ ШІ-помічник**).
Its default **Залишки та база знань** mode routes quantities to live stock and specifications or procedures to approved articles. A mixed question checks both sources. A product name alone prompts for a choice. Stock cards show on-hand quantities,
physical reservations, production demand and incoming purchase quantities within
the user's record and field permissions. Filter searches work without AI; natural
language filter extraction uses the existing spending gateway and receives no
inventory rows. See [product and parts availability](docs/product-availability.uk.md).
Simple stock requests and item codes bypass the model. Article answers and
customer drafts use accessible, current, published articles approved for AI
use. The article lookup skips generation when it finds no permitted sources.
General chat and business-record writes remain disabled. See [AI chat](docs/ai-chat.uk.md).
Follow-up questions use up to five recent exchanges in session-bound server
memory with a 30-minute TTL. Historical answers are context, not factual sources;
changed or revoked KB sources invalidate the conversation before reuse.
All AI features must use `warehouse_core.ai.generate_text`; do not add direct
OpenAI clients to the ERP or expose the key to browsers.

The current client questionnaire is [open questions in Ukrainian](docs/moysklad-questionnaire.uk.md); [confirmed sales and reservation answers](docs/client-answers-2026-10-20.uk.md) are kept separately. Preserve question IDs when recording subsequent answers.

The custom Frappe app is in `apps/warehouse_core`. Company-independent configuration is applied during installation and migrations. Existing workspace and role-profile edits are preserved; app-owned custom fields are reconciled. Demo data is never created by install/migrate hooks. App code is baked into the same image used by web, workers, scheduler, and websocket containers; restarting does not discard it.

ERPNext's existing document validation, permissions, stock ledger, and accounting ledger remain authoritative. No separate stock counter or parallel accounting engine is introduced. The full MoySklad migration scope is approved; implementing and rehearsing the source adapter requires the export files. Print layouts are ready for review; actual legal/bank/tax details remain to be supplied. Domestic Nova Poshta integration requires the client's API key and sender setup; see [setup and scope](docs/nova-poshta.uk.md). Carrier calls have mocked integration coverage; live account acceptance is still required.

Warehouse Core repairs the obsolete Quality Management **Review** query bundled with ERPNext 16.50.0: it filters actions by their current `review` link instead of the removed `document_type` field. The install/migrate hook changes only the known legacy query, preserving report roles and custom queries. `verify` checks filtering with rolled-back fixtures; `check_local.py` checks the authenticated report endpoint.

[Migration plan](docs/migration-plan.md) distinguishes confirmed requirements from assumptions and describes the first acceptance checks.

## Upstream provenance

`compose.yaml` is adapted from Frappe Docker's `pwd.yml` at commit `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`. The original is retained in `infra/upstream-pwd.yml` with its license. Changes add generated environment credentials, localhost-only publishing, database readiness dependencies, fail-fast bootstrap commands, and the custom app image/install/migration lifecycle.

- https://github.com/frappe/frappe_docker
- https://docs.frappe.io/erpnext/data-import
- https://docs.frappe.io/erpnext/stock-reconciliation

