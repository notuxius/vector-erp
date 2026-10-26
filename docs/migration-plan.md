# Migration plan

## Confirmed

- The client already uses MoySklad and wants to leave it.
- ERPNext is the selected foundation.
- Start locally on this laptop.
- The client speaks Ukrainian.
- The company's product domain is CNC machine tools (верстати з ЧПК), confirmed by the user.
- As of 2026-10-19: Ukraine, Ukrainian UI, manufacturing/assembly plus finished machines and spare parts, repairs and modernization of own and third-party machines; two warehouses, no retail outlets (second reply), UAH and USD, serial tracking, service history, operation accountability, dispatch lists, payment requests and delivery notes. The replies conflict on headcount (7 versus 9); the required ERP account count is unresolved. Earlier assumptions of five users and approximately three warehouses are superseded, not instructions to delete demo records.
- Migration includes products, buying/selling prices, counterparties, stock, debts, open orders and full history. This scope is approved; it is no longer an open discovery question.
- The written reply reports 4,550 products and 8–9 years of history; exact dates and transaction counts require source validation. See the [dated answers and follow-up questions](client-answers-2026-10-19.uk.md).
- Unknown values may use explicitly labeled examples. Example users must have an explicit replacement mapping to real users, preserving historical actors.
- Client: MTech / ТОВ «ЕМ ТЕК», from the two user-provided company websites. See [confirmed requirements and sources](client-requirements.uk.md).

## Not yet known

Actual MoySklad export files/schema, transaction volume and exact history dates, cutover date, warehouse names, ERP user count and staff identities/permission matrix, legal/bank/tax details, exact bills of materials and cost rules, document examples and status transitions. Basic returns and reservation workflows are now described; edge cases still need examples. Both UAH and USD are confirmed, while base currency, exchange-rate rules and the China purchasing workflow remain unresolved. Preserve original currencies and exchange rates, without silently converting historical amounts.

## CNC-specific discovery

Manufacturing and serial tracking are confirmed. Warehouse Core exposes native BOM/Work Order/Serial No/Maintenance Visit workflows, enforces serialization for explicitly marked machine items, stores configuration notes and provides service history. Example specifications are authorized; real component quantities, operations, warranty rules and costs still require source data.

The 19 October implementation adds Expected Production (separate physical stock, reservations, purchase expectations and outstanding work-order output) and Machine Assembly Installation (two-way serial links, removal/reinstallation history and duplicate prevention). Technical specifications, installation notes and physical manufacturer numbers have dedicated serial fields. Native warranty dates remain available. See [behavior and limits](production-and-assemblies.uk.md). Actual source mapping, frame-number inheritance policy, warranty/installation documents and repairs/modernization of third-party equipment still need client examples. Installation records describe genealogy and do not post stock movements; operation-level semi-finished output is explicitly excluded from the new report pending validation.

## Discovery and fit checks

Use the Ukrainian questionnaire and a screen-sharing walkthrough. Record each workflow as: current steps, required result, ERPNext equivalent, gap, acceptance example, and priority. Do not describe suggested modules as confirmed client requirements.

Implementation and migration map (validate exact source fields with export samples):

| Current need | ERPNext candidate | Validation needed |
| --- | --- | --- |
| Products and variants | Item, Item Group, Item Attribute, UOM | SKU uniqueness, variants, units, barcodes |
| Warehouses and balances | Warehouse, Stock Reconciliation | Quantity, valuation, batches/serials |
| Customers and suppliers | Customer, Supplier, Contact, Address | One counterparty may need both roles |
| Sales and fulfillment | Sales Order, Delivery Note, Sales Invoice | Statuses, reservations, partial fulfillment |
| Purchasing | Purchase Order, Purchase Receipt, Purchase Invoice | Partial receipts, returns, costing |
| Prices and discounts | Price List, Item Price, Pricing Rule | Currency, tax basis, customer pricing |
| Payments and debt | Payment Entry, opening receivables/payables | Reconciliation at cutover |
| Production history and expected output | BOM, Work Order, Job Card, Stock Entry; planning view to be validated | Source tech-operation semantics, expected versus actual quantities, partial completion/cancellation, component reservations; no assumed one-to-one mapping |
| Serialized machines, frames and assemblies | Serial No, physical manufacturer number, Machine Assembly Installation | Original identifiers, source installation/removal links, timestamps and numbering in both creation orders; warranty, installation and service history. The live installation workflow is not a historical import adapter. |

POS, fiscal and ecommerce integrations are not part of the current implementation. The existing Nova Poshta implementation supports domestic shipment creation, directories and on-demand status checks; real-account validation still requires credentials and sender settings. See [integration scope](nova-poshta.uk.md). The unanswered integration question in the new reply neither confirms additional integrations nor cancels the previously approved Nova Poshta scope.

## Migration sequence

1. Obtain source exports for the approved full scope and a cutover timestamp. Obtain a small anonymized sample first to implement the adapter.
2. Preserve original exports privately in `data/`; never commit client records or credentials.
3. Capture source entity type and immutable source ID in a mapping table so retries cannot silently create duplicates. Do not use names as identifiers.
4. Configure company, fiscal settings, units, warehouses, groups, taxes, and accounts based on confirmed values.
5. Transform and validate master data. Download import templates from the actual installed ERPNext version, including required fields and linked records.
6. Rehearse master imports and compare record counts, duplicates, references, variants, units, and prices.
7. Load opening stock at an agreed timestamp using ERPNext's supported stock workflow; validate quantity and valuation per item and warehouse. Decide how batches and serial numbers are preserved.
8. Migrate full history, open transactions and balances. First rehearse replaying historical transactions in chronological dependency order; calculate balances from those transactions. If an opening balance is required for an uncovered period, bound it by a documented date. Never replay history and add the same closing balances again. Unsupported history requires an explicit exception/mapping; do not silently replace the approved full history with opening balances or an archive.
9. Reconcile inventory, receivables/payables, open orders, and representative reports with the client. Run required integrations against test environments.
10. Rehearse backup/restore, train users in Ukrainian, agree the write-freeze window, and perform a final delta export and validation before cutover.

For the full-history rehearsal, include manufacturing documents and source relationships, machine/assembly serials, linked order payments and refunds, partial receipts/shipments, and open component reservations and production expectations. Preserve original source statuses in the mapping and agree their target meaning; do not infer workflow states solely from a single payment or shipment flag. Reconcile both warehouses and currencies. Record missing source links explicitly instead of inventing them. Historical manufacturing replay and opening stock must follow the same no-double-counting rule as other transactions.

## Acceptance before client use

- A user with the intended role can complete the client's purchase-to-stock and order-to-payment examples.
- Partial shipments, returns, reservations, and other confirmed exceptions behave correctly.
- Advance and deferred payments, partial receipts and shipments, and linked refunds work across the purchasing/sales, warehouse and accounting roles using agreed statuses.
- Expected production output is visible separately from physical stock and purchasing expectations; partial completion and cancellation do not double-count stock or reservations.
- Serialized assemblies can be traced to machines; both frame/machine numbering sequences preserve source identities. Warranty, installation and service records pass a real client example.
- Counts and totals reconcile; every difference has an explanation and approval.
- Re-running a migration batch does not create duplicates.
- Warehouse and role restrictions work with non-administrator users.
- Required integrations and document outputs pass client review.
- A backup has been restored successfully and the cutover rollback decision point is documented.

Warehouse Core provides operations, manufacturing and service navigation, role profiles, and unique optional `custom_moysklad_id` references on 21 master/transaction types, including prices, serial numbers, stock adjustments, payments, journals, manufacturing and service. Native ERPNext Data Import is available. The MoySklad export adapter, dependency mapping and reconciliation still require actual source files. Fields and templates alone do not mean that client data has been migrated.

The demo company and its sample records are explicitly illustrative. The production site must be provisioned separately using confirmed client settings.
