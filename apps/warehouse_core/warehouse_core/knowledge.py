"""Permission-first local knowledge retrieval. No embeddings, external fetches or tools."""
import base64
import binascii
import hashlib
import json
import re
from pathlib import PurePath
from urllib.parse import quote

import frappe
from frappe.utils import cint, getdate, nowdate
from warehouse_core.assistant_routing import exact_item, item_codes, specification_subject

ARTICLE = "Knowledge Article"
ATTACHMENT = "Knowledge Attachment"
MAX_CONTENT = 100_000
MAX_FILE = 5 * 1024 * 1024
STOP_WORDS = set("як що це для про між або та і у в з на до за ми ви the a an is are how what of to and for in our".split())


def desk_user(user=None):
    user = user or frappe.session.user
    return user != "Guest" and frappe.db.get_value("User", user, ["enabled", "user_type"]) == (1, "System User")


def require_user():
    if not desk_user():
        frappe.throw("Потрібен обліковий запис працівника.", frappe.PermissionError)


def publisher(user=None):
    user = user or frappe.session.user
    return desk_user(user) and (user == "Administrator" or bool(
        {"System Manager", "Knowledge Publisher"} & set(frappe.get_roles(user))))


def editor(user=None):
    user = user or frappe.session.user
    return publisher(user) or (desk_user(user) and "Knowledge Editor" in frappe.get_roles(user))


def current(doc):
    return doc.status == "Published" and (not doc.valid_until or getdate(doc.valid_until) >= getdate(nowdate()))


def allowed(doc, user=None, write=False):
    user = user or frappe.session.user
    if not desk_user(user):
        return False
    if publisher(user):
        return True
    owns = doc.owner == user and editor(user)
    if write:
        return owns
    if owns:
        return True
    if not current(doc):
        return False
    return user in {row.user for row in doc.allowed_users} or bool(
        set(frappe.get_roles(user)) & {row.role for row in doc.allowed_roles})


def article_permission(doc, user=None, ptype=None):
    if ptype in ("share", "delete", "email", "export", "print"):
        return False
    if ptype == "create":
        return editor(user)
    return allowed(doc, user, write=ptype not in (None, "read", "select"))


def article_query(user=None):
    user = user or frappe.session.user
    if not desk_user(user):
        return "1=0"
    if publisher(user):
        return "1=1"
    esc = frappe.db.escape
    roles = ",".join(esc(role) for role in frappe.get_roles(user)) or "''"
    parent = "`tabKnowledge Article`"
    acl = f"""(exists (select 1 from `tabKnowledge Access User` u
        where u.parent={parent}.name and u.parenttype='Knowledge Article'
        and u.parentfield='allowed_users' and u.user={esc(user)})
        or exists (select 1 from `tabKnowledge Access Role` r
        where r.parent={parent}.name and r.parenttype='Knowledge Article'
        and r.parentfield='allowed_roles' and r.role in ({roles})))"""
    published = f"({parent}.status='Published' and ({parent}.valid_until is null or {parent}.valid_until >= {esc(nowdate())}) and {acl})"
    owner = f"{parent}.owner={esc(user)}" if editor(user) else "1=0"
    return f"({owner} or {published})"


def attachment_permission(doc, user=None, ptype=None):
    if ptype not in (None, "read", "select") or not doc.article:
        return False
    if not frappe.db.exists(ARTICLE, doc.article):
        return False
    parent = frappe.get_doc(ARTICLE, doc.article)
    return allowed(parent, user) and frappe.has_permission(ARTICLE, "read", doc=parent, user=user)


def attachment_query(user=None):
    return parent_query("Knowledge Attachment", "article", user)


def access_user_query(user=None):
    return parent_query("Knowledge Access User", "parent", user)


def access_role_query(user=None):
    return parent_query("Knowledge Access Role", "parent", user)


def parent_query(table, field, user=None):
    if not desk_user(user):
        return "1=0"
    from frappe.model.db_query import DatabaseQuery
    sql = DatabaseQuery(ARTICLE, user=user).execute(fields=["name"], user=user,
        limit_page_length=0, order_by="", run=False)
    return f"`tab{table}`.`{field}` in ({sql})"


def reject_standard_attachment(doc, method=None):
    old = doc.get_doc_before_save()
    if doc.attached_to_doctype in (ARTICLE, ATTACHMENT) or (old and old.attached_to_doctype in (ARTICLE, ATTACHMENT)):
        frappe.throw("Для бази знань використовуйте кнопку «Додати захищений файл» у статті.")


def reject_share(doc, method=None):
    if doc.share_doctype in (ARTICLE, ATTACHMENT, "Knowledge Query Log"):
        frappe.throw("Доступ до бази знань надається лише через список доступу статті.", frappe.PermissionError)


def _get_article(name, write=False):
    require_user()
    # Same error for missing and forbidden records, without leaking their titles.
    if not isinstance(name, str) or not frappe.db.exists(ARTICLE, name):
        frappe.throw("Стаття недоступна.", frappe.PermissionError)
    doc = frappe.get_doc(ARTICLE, name)
    if not allowed(doc, write=write) or not frappe.has_permission(ARTICLE, "write" if write else "read", doc=doc):
        frappe.throw("Стаття недоступна.", frappe.PermissionError)
    return doc


def tokens(query):
    return list(dict.fromkeys(word for word in re.findall(r"[^\W_]+", query.casefold(), re.UNICODE)
                              if len(word) >= 2 and word not in STOP_WORDS))[:12]


def _search(query, cloud=False, limit=20):
    require_user()
    if not isinstance(query, str) or len(query) > 4000:
        frappe.throw("Запит має містити не більше 4000 символів.")
    words = tokens(query)
    if query.strip() and not words:
        return []
    filters = []
    if cloud:
        filters = [["status", "=", "Published"], ["cloud_allowed", "=", 1]]
    alternatives = [[field, "like", f"%{word}%"] for word in words for field in ("title", "content")]
    # ACL is in SQL before reading content. No unfiltered vector store or shared cache.
    rows = frappe.get_list(ARTICLE, fields=["name"], filters=filters, or_filters=alternatives,
                           order_by="modified desc", limit_page_length=200)
    identifiers = []
    if cloud:
        identifiers = item_codes(query)
        subject = specification_subject(query)
        if subject:
            identifiers.append(subject)
        identifiers = list(dict.fromkeys(term.casefold() for term in identifiers if term.strip()))[:12]
    if identifiers:
        # A recent, generic result page can otherwise crowd out the exact model.
        exact = [[field, "like", f"%{term}%"] for term in identifiers for field in ("title", "content")]
        preferred = frappe.get_list(ARTICLE, fields=["name"], filters=filters, or_filters=exact,
                                    order_by="modified desc", limit_page_length=200)
        found = {row.name for row in preferred}
        rows = preferred + [row for row in rows if row.name not in found]
    patterns = [exact_item(term) for term in identifiers]
    matches = []
    for row in rows:
        doc = frappe.get_doc(ARTICLE, row.name)
        if not allowed(doc) or not frappe.has_permission(ARTICLE, doc=doc) or (cloud and (not current(doc) or not doc.cloud_allowed)):
            continue
        title, body = doc.title.casefold(), doc.content.casefold()
        score = sum(5 * (word in title) + (word in body) for word in words)
        matched = [pattern for pattern in patterns if pattern.search(title) or pattern.search(body)]
        pos = next((hit.start() for pattern in matched if (hit := pattern.search(body))),
                   next((body.find(word) for word in words if word in body), 0))
        start = max(0, pos - 140)
        if cloud:
            start = min(start, max(0, len(doc.content) - 2400))
        excerpt = doc.content[start:start + (2400 if cloud else 350)]
        if matched and doc.name.startswith("KB-MTECH-catalog-"):
            # These articles contain one product per paragraph. Supply only matching entries.
            entries = [entry for entry in doc.content.split("\n\n") if any(p.search(entry) for p in matched)]
            if not entries:
                continue
            excerpt = "\n\n".join(entries)[:2400]
        matches.append(((len(matched), score), {"name": doc.name, "title": doc.title, "category": doc.category,
            "status": doc.status, "revision": doc.revision, "modified": str(doc.modified),
            "cloud_allowed": bool(doc.cloud_allowed), "excerpt": excerpt,
            "url": "/desk/knowledge-article/" + quote(doc.name, safe="")}))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in matches[:limit]]


@frappe.whitelist(methods=["GET"])
def search(query=""):
    return {"articles": _search(query), "can_edit": editor(), "can_publish": publisher()}


@frappe.whitelist(methods=["GET"])
def attachments(article):
    _get_article(article)
    rows = frappe.get_all(ATTACHMENT, filters={"article": article},
        fields=["name", "file_name", "byte_size", "creation", "extraction_status", "extraction_message", "extracted_characters"],
        order_by="creation desc", limit_page_length=20)
    for row in rows:
        row["url"] = "/api/method/warehouse_core.knowledge.download?name=" + quote(row.name, safe="")
    return rows


@frappe.whitelist(methods=["POST"])
def upload(article, filename, data):
    doc = _get_article(article, write=True)
    if not isinstance(filename, str) or not 1 <= len(filename) <= 180 or any(c in filename for c in "/\\\r\n\x00"):
        frappe.throw("Некоректна назва файлу.")
    suffix = PurePath(filename).suffix.lower()
    if suffix not in (".pdf", ".txt", ".md", ".docx", ".xlsx"):
        frappe.throw("Дозволені PDF, DOCX, XLSX, TXT та MD.")
    if not isinstance(data, str) or len(data) > (MAX_FILE * 4 // 3 + 4):
        frappe.throw("Максимальний розмір файлу — 5 МБ.")
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        frappe.throw("Не вдалося прочитати файл.")
    if not raw or len(raw) > MAX_FILE:
        frappe.throw("Файл порожній або перевищує 5 МБ.")
    if suffix == ".pdf" and not raw.startswith(b"%PDF-"):
        frappe.throw("Некоректний PDF.")
    if suffix in (".docx", ".xlsx") and not raw.startswith(b"PK\x03\x04"):
        frappe.throw("Некоректний DOCX/XLSX або зашифрований файл. Завантажте дозволену незашифровану копію.")
    if suffix in (".txt", ".md"):
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            frappe.throw("Текстовий файл має бути в кодуванні UTF-8.")
    from warehouse_core.knowledge_extraction import extract
    if suffix in (".pdf", ".docx", ".xlsx", ".txt"):
        # One resource-limited parser per site; no accumulation of 512 MB child processes.
        lock = frappe.cache.lock(frappe.cache.make_key("knowledge-document-extraction"), timeout=30)
        if not lock.acquire(blocking=False):
            frappe.throw("Інший документ ще обробляється. Повторіть завантаження за кілька секунд.")
        try:
            extracted = extract(raw, suffix)
        finally:
            lock.release()
    else:
        extracted = extract(raw, suffix)
    # Serialize attachment count and publication changes with article saves.
    frappe.db.get_value(ARTICLE, article, "name", for_update=True)
    doc = _get_article(article, write=True)
    if frappe.db.count(ATTACHMENT, {"article": article}) >= 10:
        frappe.throw("До однієї статті можна додати не більше 10 файлів.")
    text = extracted["text"]
    if text:
        addition = f"\n\n---\nДжерело: {filename}\n{text}"
        if len((doc.content or "") + addition) > MAX_CONTENT:
            extracted = {"status": "Limit Exceeded", "text": "", "message":
                "Файл збережено, але сумарний текст статті перевищив би 100 000 символів. Завантажте документ в окрему статтю або розділіть його."}
            text = ""
        else:
            doc.content = (doc.content or "") + addition
    item = frappe.get_doc({"doctype": ATTACHMENT, "article": article, "file_name": filename,
        "byte_size": len(raw), "payload": data, "sha256": hashlib.sha256(raw).hexdigest(),
        "extraction_status": extracted["status"], "extraction_message": extracted["message"],
        "extracted_characters": len(text)})
    item.flags.knowledge_upload = True
    item.insert(ignore_permissions=True)
    doc.flags.knowledge_attachment_changed = True
    doc.status = "Draft"
    doc.save()
    return {"name": item.name, "file_name": filename, "extraction_status": extracted["status"],
            "extraction_message": extracted["message"], "extracted_characters": len(text)}


@frappe.whitelist(methods=["GET"])
def download(name):
    require_user()
    if not isinstance(name, str) or not frappe.db.exists(ATTACHMENT, name):
        frappe.throw("Файл недоступний.", frappe.PermissionError)
    item = frappe.get_doc(ATTACHMENT, name)
    _get_article(item.article)
    frappe.local.response.update({"filename": item.file_name, "filecontent": base64.b64decode(item.payload),
        "type": "download", "display_content_as": "attachment", "content_type": "application/octet-stream"})
    frappe.local.response_headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def retrieve(query):
    return _search(query, cloud=True, limit=3)


def sources_current(sources):
    # Use fresh documents, never cached content or ACLs, before release of an answer.
    for source in sources:
        if not frappe.db.exists(ARTICLE, source["name"]):
            return False
        doc = frappe.get_doc(ARTICLE, source["name"])
        if not allowed(doc) or not frappe.has_permission(ARTICLE, doc=doc) or not current(doc) or not doc.cloud_allowed or str(doc.modified) != source["modified"]:
            return False
    return True


def audit(message, sources, mode, outcome):
    doc = frappe.get_doc({"doctype": "Knowledge Query Log", "requested_by": frappe.session.user,
        "query_hash": hashlib.sha256(message.encode()).hexdigest(), "mode": mode, "outcome": outcome,
        "sources": json.dumps([{key: source[key] for key in ("name", "revision", "modified")} for source in sources])})
    doc.flags.knowledge_audit = True
    doc.insert(ignore_permissions=True)
    return doc.name
