"""Compatibility fixes for reports shipped with the pinned ERPNext image."""
import frappe


@frappe.whitelist()
def run(
    report_name: str,
    filters: str | dict | None = None,
    user: str | None = None,
    ignore_prepared_report: bool = False,
    custom_columns: str | list | None = None,
    is_tree: bool = False,
    parent_field: str | None = None,
    are_default_filters: bool = True,
    js_filters: str | list | None = None,
) -> dict:
    """Use the report's inline empty state instead of a redundant modal."""
    from frappe.desk.query_report import run as run_report

    message_count = len(frappe.local.message_log)
    result = run_report(
        report_name=report_name, filters=filters, user=user,
        ignore_prepared_report=ignore_prepared_report, custom_columns=custom_columns,
        is_tree=is_tree, parent_field=parent_field,
        are_default_filters=are_default_filters, js_filters=js_filters,
    )
    # Only completed, empty reports qualify. Exceptions, pending prepared
    # reports, populated reports and unrelated messages keep their normal UI.
    if result.get("result") == []:
        empty_messages = {
            "No record found", "No records found",
            frappe._("No record found"), frappe._("No records found"),
        }
        messages = frappe.local.message_log
        messages[message_count:] = [
            message for message in messages[message_count:]
            if not (
                isinstance(message.get("message"), str)
                and message["message"] in empty_messages
                and not message.get("raise_exception")
                and not message.get("indicator")
                and not message.get("primary_action")
            )
        ]
    return result


LEGACY_REVIEW_QUERY = """SELECT
  `tabQuality Action`.name as "Name:Data:200",
  `tabQuality Action`.corrective_preventive as "Action:Select/[Corrective,Preventive]:200",
  `tabQuality Action`.document_type as "Document Type:Select/[Quality Review, Quality Feedback]:200",
  `tabQuality Action`.date as "Date:Date:120",
  `tabQuality Action`.status as "Status:Select/Planned:150"
FROM
  `tabQuality Action`
WHERE
  `tabQuality Action`.document_type='Quality Review'"""

REVIEW_QUERY = """SELECT
  `tabQuality Action`.name as "Name:Link/Quality Action:200",
  `tabQuality Action`.corrective_preventive as "Action:Data:200",
  'Quality Review' as "Document Type:Data:200",
  `tabQuality Action`.date as "Date:Date:120",
  `tabQuality Action`.status as "Status:Data:150"
FROM
  `tabQuality Action`
WHERE
  COALESCE(`tabQuality Action`.review, '') != ''"""


def apply():
    """Repair only the known obsolete query; preserve roles and custom queries.

    ERPNext 16.50.0 still ships a Review query using document_type, although
    Quality Action now has separate review and feedback links. Run after each
    migration because upstream report synchronization can restore that query.
    """
    if not frappe.db.exists("Report", "Review"):
        return
    report = frappe.get_doc("Report", "Review")
    if (report.is_standard != "Yes" or report.module != "Quality Management"
            or report.ref_doctype != "Quality Action" or report.report_type != "Query Report"
            or (report.query or "").strip() != LEGACY_REVIEW_QUERY.strip()):
        return
    meta = frappe.get_meta("Quality Action")
    if meta.has_field("document_type") or not meta.has_field("review"):
        return
    # Only the query changes. Do not re-save the standard report or its roles.
    frappe.db.set_value("Report", "Review", "query", REVIEW_QUERY, update_modified=False)
    frappe.clear_document_cache("Report", "Review")


def verify():
    """Exercise the real SQL and migration repair with fixtures rolled back."""
    import uuid

    if not frappe.conf.get("warehouse_demo"):
        frappe.throw("Run these checks only on the explicitly configured demo site")
    frappe.db.savepoint("verify_review_report")
    try:
        report = frappe.get_doc("Report", "Review")
        roles = [row.as_dict() for row in report.roles]
        assert report.query == REVIEW_QUERY, "Review report repair is not installed"
        prefix = "VERIFY-REVIEW-" + uuid.uuid4().hex[:10]
        # Low-level fixtures isolate report filtering from unrelated document
        # validation. No business documents or linked records are committed.
        for suffix, review in (("linked", "VERIFY-REVIEW"), ("empty", ""), ("null", None)):
            frappe.get_doc({
                "doctype": "Quality Action", "name": f"{prefix}-{suffix}",
                "corrective_preventive": "Corrective", "review": review,
                "feedback": "VERIFY-FEEDBACK", "status": "Open", "date": "2026-01-01",
            }).db_insert()
        columns, rows = report.execute_query_report({})
        found = [row for row in rows if row[0].startswith(prefix)]
        assert len(columns) == 5
        assert len(found) == 1 and found[0][0] == f"{prefix}-linked", found
        assert found[0][2] == "Quality Review" and found[0][4] == "Open"

        # Simulate upstream synchronization, then verify repeatable repair.
        frappe.db.set_value("Report", "Review", "query", LEGACY_REVIEW_QUERY)
        apply()
        apply()
        report.reload()
        assert report.query == REVIEW_QUERY
        assert [row.as_dict() for row in report.roles] == roles
        custom_query = "SELECT name FROM `tabQuality Action`"
        frappe.db.set_value("Report", "Review", "query", custom_query)
        apply()
        assert frappe.db.get_value("Report", "Review", "query") == custom_query
    finally:
        frappe.db.rollback(save_point="verify_review_report")
        frappe.clear_document_cache("Report", "Review")
    return {"status": "PASS", "checks": [
        "Review includes linked actions and excludes empty/null review links",
        "Migration repair is repeatable and preserves report roles and custom queries",
        "All fixtures and temporary report changes rolled back",
    ]}
