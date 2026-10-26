# Project conventions

- Product name: **Vector ERP** in English, **Вектор ERP** in Ukrainian; Docker project and image: `vector-erp`. The internal Frappe package remains `warehouse_core`.

- Use natural English for English app names, modules, role profiles, package names, and routes (for example, `Warehouse Core`, `warehouse_core`, and `warehouse-core`). Do not transliterate Ukrainian words into English identifiers.
- Use Ukrainian normally in Ukrainian interface labels and client documentation.
- Use the app's styled tooltip component whenever a tooltip is needed; do not use browser-default `title` tooltips. The top-panel Search and Notifications buttons should have no tooltip; retain their accessible labels.
- Keep the proper name MoySklad when referring to the source product and its external IDs. Legacy identifiers may remain only where needed for migration or existing persistent infrastructure.
- Preserve existing business records and role assignments when renaming configuration. Verify the installed application and transaction checks after a deployment change.
