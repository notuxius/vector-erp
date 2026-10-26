"""Domestic Nova Poshta API 2.0. Credentials and carrier calls stay on the server."""
import math
import re

import frappe
import requests
from frappe.utils import getdate, now_datetime, nowdate

API_URL = "https://api.novaposhta.ua/v2.0/json/"
ROLES = {"Stock User", "Stock Manager", "System Manager"}


class CarrierError(Exception):
    """Safe error without response bodies, credentials or customer data."""


def api(settings, model, method, properties):
    key = settings.get_password("api_key", raise_exception=False)
    if not settings.enabled or not key:
        raise CarrierError("Увімкніть інтеграцію та збережіть API-ключ у налаштуваннях Нової пошти.")
    try:
        response = requests.post(API_URL, json={"apiKey": key, "modelName": model,
            "calledMethod": method, "methodProperties": properties}, timeout=(5, 30), allow_redirects=False)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        raise CarrierError("Нова пошта не повернула коректну відповідь. Перевірте зв’язок і кабінет перевізника.") from None
    if not isinstance(result, dict) or result.get("success") is not True or not isinstance(result.get("data"), list):
        # Provider errors may echo PII or the API key. Do not expose raw responses.
        raise CarrierError("Нова пошта відхилила запит. Перевірте ключ, реквізити та дані відправлення в кабінеті перевізника.")
    return result["data"]


def authorize(delivery_note, write=True):
    if not ROLES.intersection(frappe.get_roles()):
        frappe.throw("Операція доступна працівникам складу.", frappe.PermissionError)
    doc = frappe.get_doc("Delivery Note", delivery_note)
    doc.check_permission("write" if write else "read")
    return doc


def settings_for(company):
    if not frappe.db.exists("Nova Poshta Settings", company):
        frappe.throw("Налаштуйте Нову пошту для цієї компанії.")
    return frappe.get_doc("Nova Poshta Settings", company)


def phone(value):
    value = re.sub(r"[\s()+-]", "", value or "")
    if re.fullmatch(r"0[0-9]{9}", value):
        value = "38" + value
    if not re.fullmatch(r"380[0-9]{9}", value):
        frappe.throw("Телефон має бути українським номером у форматі +380XXXXXXXXX.")
    return value


def positive(value, label, integer=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0
    if not math.isfinite(number) or number <= 0 or (integer and not number.is_integer()):
        frappe.throw(f"{label}: вкажіть додатне {'ціле ' if integer else ''}число.")
    return int(number) if integer else number


def ref(value):
    if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", value or ""):
        frappe.throw("Виберіть місто, відділення та контакт із довідника Нової пошти.")
    return value


@frappe.whitelist(methods=["POST"])
def lookup(kind, query="", city="", counterparty="", delivery_note=None, company=None, page=1):
    if delivery_note:
        company = authorize(delivery_note).company
    else:
        frappe.only_for("System Manager")
        frappe.get_doc("Company", company).check_permission("read")
    settings = settings_for(company)
    page = max(1, min(int(page), 1000))
    if kind == "cities":
        model, method, props = "Address", "getCities", {"FindByString": str(query)[:100], "Limit": 50, "Page": page}
    elif kind == "warehouses":
        model, method, props = "Address", "getWarehouses", {"CityRef": ref(city), "FindByString": str(query)[:100], "Limit": 50, "Page": page, "Language": "UA"}
    elif kind in ("senders", "contacts") and not delivery_note:
        model = "Counterparty"
        method, props = ("getCounterparties", {"CounterpartyProperty": "Sender", "Page": page, "FindByString": str(query)[:100]}) if kind == "senders" else (
            "getCounterpartyContactPersons", {"Ref": ref(counterparty), "Page": page})
    else:
        frappe.throw("Непідтримуваний довідник.")
    try:
        rows = api(settings, model, method, props)
    except CarrierError as exc:
        frappe.throw(str(exc))
    return [{"value": row.get("Ref"), "label": row.get("Description") or " ".join(
        str(row.get(k) or "") for k in ("LastName", "FirstName", "MiddleName")),
        "phone": row.get("Phones") or ""} for row in rows if row.get("Ref") and row.get("CategoryOfWarehouse") != "Postomat"]


def lock_shipment(name):
    frappe.db.get_value("Nova Poshta Shipment", name, "name", for_update=True)
    shipment = frappe.get_doc("Nova Poshta Shipment", name)
    shipment.check_permission("write")
    # Serialize with native Delivery Note edits/cancellation too.
    frappe.db.get_value("Delivery Note", shipment.delivery_note, "name", for_update=True)
    delivery = authorize(shipment.delivery_note)
    if shipment.company != delivery.company:
        frappe.throw("Компанія відправлення не відповідає відвантаженню.")
    return shipment, delivery


def payload(shipment, settings):
    for field in ("sender_ref", "sender_contact", "sender_city", "sender_warehouse"):
        ref(settings.get(field))
    for field in ("recipient_city", "recipient_warehouse"):
        ref(shipment.get(field))
    if not shipment.first_name or not shipment.last_name or not shipment.description:
        frappe.throw("Заповніть ім’я, прізвище отримувача та опис вантажу.")
    if getdate(shipment.shipment_date) < getdate(nowdate()):
        frappe.throw("Дата відправлення не може бути в минулому.")
    if shipment.payer not in ("Sender", "Recipient") or shipment.payment_method not in ("Cash", "NonCash"):
        frappe.throw("Виберіть платника та спосіб оплати доставки.")
    return {"PayerType": shipment.payer, "PaymentMethod": shipment.payment_method,
        "DateTime": getdate(shipment.shipment_date).strftime("%d.%m.%Y"),
        "CargoType": "Parcel", "ServiceType": "WarehouseWarehouse",
        "Weight": positive(shipment.weight, "Вага"), "SeatsAmount": positive(shipment.seats, "Кількість місць", True),
        "Cost": positive(shipment.declared_value, "Оголошена вартість"),
        "Description": shipment.description[:100],
        "CitySender": settings.sender_city, "Sender": settings.sender_ref,
        "SenderAddress": settings.sender_warehouse, "ContactSender": settings.sender_contact,
        "SendersPhone": phone(settings.sender_phone), "CityRecipient": shipment.recipient_city,
        "RecipientAddress": shipment.recipient_warehouse, "RecipientsPhone": phone(shipment.recipient_phone),
        "InfoRegClientBarcodes": shipment.name}


@frappe.whitelist(methods=["POST"])
def create_waybill(name):
    shipment, delivery = lock_shipment(name)
    if shipment.state == "Created":
        return {"waybill": shipment.waybill}
    if shipment.state != "Draft":
        frappe.throw("Результат попереднього запиту невідомий. Спочатку звірте його з кабінетом Нової пошти.")
    if delivery.docstatus != 1 or delivery.is_return or delivery.custom_nova_poshta_ttn:
        frappe.throw("Потрібне проведене відвантаження без ТТН, яке не є поверненням.")
    settings = settings_for(delivery.company)
    if not settings.enabled or not settings.get_password("api_key", raise_exception=False):
        frappe.throw("Спочатку увімкніть інтеграцію та збережіть API-ключ.")
    props = payload(shipment, settings)
    # Durable intent BEFORE any external write. A timeout/process crash must never
    # cause an automatic second save. This endpoint is explicitly POST-only.
    shipment.db_set({"state": "Uncertain", "last_error": "Запит розпочато; якщо результат не з’явився, звірте кабінет перевізника.",
        "requested_by": frappe.session.user, "requested_at": now_datetime()})
    frappe.db.commit()
    try:
        recipient = api(settings, "Counterparty", "save", {"CounterpartyProperty": "Recipient",
            "CounterpartyType": "PrivatePerson", "FirstName": shipment.first_name,
            "LastName": shipment.last_name, "MiddleName": shipment.middle_name or "",
            "Phone": props["RecipientsPhone"], "CityRef": shipment.recipient_city})
        person = recipient[0]
        contact = person["ContactPerson"]["data"][0]["Ref"]
        props.update({"Recipient": person["Ref"], "ContactRecipient": contact})
        result = api(settings, "InternetDocument", "save", props)
        row = result[0]
        number = str(row.get("IntDocNumber") or "")
        if not re.fullmatch(r"[0-9]{14}", number) or not row.get("Ref"):
            raise CarrierError("Нова пошта не повернула номер ТТН. Звірте результат у кабінеті перевізника.")
    except (CarrierError, KeyError, IndexError, TypeError) as exc:
        error = str(exc) if isinstance(exc, CarrierError) else "Неповна відповідь Нової пошти. Звірте результат у кабінеті перевізника."
        frappe.db.set_value("Nova Poshta Shipment", name, "last_error", error)
        frappe.db.commit()
        return {"state": "Uncertain", "error": error}
    # Lock again after the external request; generic edits are barred while Uncertain.
    shipment, delivery = lock_shipment(name)
    _record_waybill(shipment, delivery, number, row["Ref"], row.get("CostOnSite"))
    frappe.db.commit()
    return {"state": "Created", "waybill": number}


def _record_waybill(shipment, delivery, number, carrier_ref, cost=None):
    values = {"state": "Created", "waybill": number, "carrier_ref": carrier_ref, "last_error": ""}
    if cost is not None:
        values["delivery_cost"] = cost
    shipment.db_set(values)
    delivery.custom_nova_poshta_ttn = number
    delivery.save()


@frappe.whitelist(methods=["POST"])
def reconcile(name, waybill=None, confirmed_absent=0):
    frappe.only_for("System Manager")
    shipment, delivery = lock_shipment(name)
    if shipment.state != "Uncertain":
        frappe.throw("Звірка потрібна лише для запиту з невідомим результатом.")
    # Avoid an operator reset while the original HTTP request can still be running.
    if shipment.requested_at and (now_datetime() - shipment.requested_at).total_seconds() < 120:
        frappe.throw("Зачекайте дві хвилини після запиту перед звіркою.")
    if str(confirmed_absent) == "1":
        if waybill:
            frappe.throw("Вкажіть ТТН або підтвердьте її відсутність.")
        shipment.db_set({"state": "Draft", "last_error": ""})
        shipment.add_comment("Comment", "Адміністратор перевірив кабінет перевізника: ТТН не створено. Дозволено нову спробу.")
        return {"state": "Draft"}
    if not re.fullmatch(r"[0-9]{14}", waybill or ""):
        frappe.throw("Вкажіть 14-значну ТТН з кабінету перевізника.")
    try:
        rows = api(settings_for(delivery.company), "InternetDocument", "getDocumentList", {
            "DateTimeFrom": getdate(shipment.shipment_date).strftime("%d.%m.%Y"),
            "DateTimeTo": getdate(shipment.shipment_date).strftime("%d.%m.%Y"), "GetFullList": 1})
    except CarrierError as exc:
        frappe.throw(str(exc))
    matches = [row for row in rows if str(row.get("IntDocNumber")) == waybill
               and row.get("InfoRegClientBarcodes") == shipment.name and row.get("Ref")]
    if len(matches) != 1:
        frappe.throw("У цьому кабінеті не знайдено ТТН з номером саме цього відправлення в полі клієнтського номера.")
    _record_waybill(shipment, delivery, waybill, matches[0]["Ref"], matches[0].get("CostOnSite"))
    shipment.add_comment("Comment", "ТТН відновлено після звірки з кабінетом перевізника.")
    return {"state": "Created", "waybill": waybill}


@frappe.whitelist(methods=["POST"])
def refresh_tracking(delivery_note):
    delivery = authorize(delivery_note)
    number = delivery.custom_nova_poshta_ttn or ""
    if not re.fullmatch(r"[0-9]{14}", number) or number == "00000000000000":
        frappe.throw("Вкажіть справжню 14-значну ТТН.")
    try:
        rows = api(settings_for(delivery.company), "TrackingDocument", "getStatusDocuments", {
            "Documents": [{"DocumentNumber": number}]})
    except CarrierError as exc:
        frappe.throw(str(exc))
    matches = [row for row in rows if str(row.get("Number")) == number]
    if len(matches) != 1 or not matches[0].get("Status"):
        frappe.throw("Нова пошта не повернула статус цієї ТТН.")
    row = matches[0]
    # Do not attach a stale response if another user changed the manual TTN.
    frappe.db.get_value("Delivery Note", delivery.name, "name", for_update=True)
    delivery.reload()
    if delivery.custom_nova_poshta_ttn != number:
        frappe.throw("ТТН змінено. Оновіть документ та повторіть перевірку.")
    delivery.db_set({"custom_nova_poshta_status": str(row["Status"])[:140],
        "custom_nova_poshta_status_code": str(row.get("StatusCode") or "")[:20],
        "custom_nova_poshta_checked_at": now_datetime()})
    return {"status": delivery.custom_nova_poshta_status}


def protect_delivery(doc, method=None):
    previous = doc.get_doc_before_save()
    changed = previous and previous.custom_nova_poshta_ttn != doc.custom_nova_poshta_ttn
    shipment = frappe.db.get_value("Nova Poshta Shipment", {"delivery_note": doc.name}, ["state", "waybill"], as_dict=True)
    if shipment and shipment.state in ("Created", "Uncertain"):
        if (method == "before_cancel" and shipment.state == "Uncertain") or doc.is_return:
            frappe.throw("Спочатку узгодьте скасування з Новою поштою. ERP не скасовує ТТН автоматично.")
        if method == "before_cancel" and shipment.state == "Created":
            frappe.msgprint("Відвантаження скасовано лише в ERP. Скасуйте ТТН окремо в кабінеті Нової пошти.")
        if changed and (shipment.state == "Uncertain" or doc.custom_nova_poshta_ttn != shipment.waybill):
            frappe.throw("ТТН пов’язана з відправленням Нової пошти; її не можна замінити вручну.")
    if changed:
        doc.custom_nova_poshta_status = ""
        doc.custom_nova_poshta_status_code = ""
        doc.custom_nova_poshta_checked_at = None
