"""Deterministic conversation engine, independent of Telegram transport/storage."""

import re
import secrets
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo


class RequestLimit(Exception):
    pass


def valid_time(value):
    return isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is not None


def normalize_time(value):
    match = re.fullmatch(r"([0-9]{1,2})(?:(?::| +)([0-9]{1,2}))?", value) if isinstance(value, str) else None
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or "0")
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def chunks(text, limit=3500):
    # Telegram counts UTF-16 code units; 3500 code points alone is unsafe for emoji.
    current, size = [], 0
    for character in text:
        width = 2 if ord(character) > 0xFFFF else 1
        if size + width > limit:
            yield "".join(current)
            current, size = [], 0
        current.append(character)
        size += width
    if current:
        yield "".join(current)


def normalize_phone(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    value = re.sub(r"[\s()\-]", "", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    return value if re.fullmatch(r"\+[1-9][0-9]{7,14}", value) else None


class Engine:
    def __init__(self, content, state, chat_id, submit, customer=None, subscribe=None, save_profile=None):
        self.c, self.s, self.chat_id, self.submit = content, state, chat_id, submit
        self.customer = customer or {}
        self.subscribe = subscribe or (lambda enabled: None)
        self.save_profile = save_profile or (lambda profile: None)
        self.actions = []

    def text(self, key, **values):
        return self.c["texts"][key].format(**values)

    def label(self, key):
        return self.c["labels"][key]

    def button_label(self, label):
        icon = self.c.get("button_icons", {}).get(label)
        return f"{icon} {label}" if icon else label

    def send(self, text, markup=None):
        parts = list(chunks(text))
        for index, part in enumerate(parts):
            payload = {"chat_id": self.chat_id, "text": part,
                       "link_preview_options": {"is_disabled": True}}
            if markup and index == len(parts) - 1:
                payload["reply_markup"] = markup
            self.actions.append(("sendMessage", payload))

    def screen(self, text, items, columns=1, phone=False):
        if self.s.pop("phone_keyboard", False) and not phone:
            self.send(self.text("continue_form"), {"remove_keyboard": True})
        revision = secrets.token_hex(4)
        self.s["revision"] = revision
        self.s["buttons"] = {action: label for label, action in items if not action.startswith("https:")}
        if phone:
            self.s["phone_keyboard"] = True
            self.send(text, {"keyboard": [[{"text": self.button_label(self.label("share_phone")), "request_contact": True}],
                                         [self.button_label(label) for label, action in items if action in {"back", "cancel"}]],
                             "resize_keyboard": True, "one_time_keyboard": True})
            return
        buttons = [{"text": self.button_label(label), **({"url": action} if action.startswith("https:")
                    else {"callback_data": revision + "|" + action})} for label, action in items]
        rows = [buttons[i:i + columns] for i in range(0, len(buttons), columns)]
        self.send(text, {"inline_keyboard": rows})

    def nav(self, parent="home"):
        items = [(self.label("back"), "menu:" + parent)]
        if parent != "home":
            items.append((self.label("home"), "menu:home"))
        return items

    def fallback(self, question="", notice="", timestamp=None):
        previous = self.s.get("screen", "")
        kind, _, key = previous.partition(":")
        topic = self.c["info"].get(key, {}).get("source") if kind == "info" else key
        if kind == "category":
            topic = "catalogue"
        self.s["screen"] = "fallback"
        limit = self.c["fields"]["message"]["max_length"]
        self.s["pending_text"] = question if len(question) <= limit else ""
        if len(question) > limit:
            notice = self.text("handoff_long", limit=limit)
        hours = self.c["working_hours"]
        tz = ZoneInfo("Europe/Kyiv")
        local = datetime.now(tz) if timestamp is None else datetime.fromtimestamp(timestamp, tz)
        closed = local.weekday() >= 5 or not hours["start"] <= local.strftime("%H:%M") < hours["end"]
        items = [(self.label("callback"), "flow:callback"), (self.label("written"), "flow:written")]
        text = self.text("fallback")
        if closed:
            text = self.text("fallback_closed", **hours)
            def priority(link):
                if any(word in question.lower() for word in link["keywords"]):
                    return 0
                return 1 if topic in link["topics"] else 2
            items.extend((link["label"], link["url"]) for link in sorted(self.c["self_help"], key=priority))
        self.screen((notice + "\n\n" if notice else "") + text, items + [(self.label("home"), "menu:home")])

    def menu(self, name="home"):
        self.s.pop("pending_text", None)
        self.s.pop("profile_field", None)
        self.s["screen"] = "menu:" + name
        if name == "status":
            rows = [self.text("status_row", number=r["id"], date=r["created_at"][:10],
                              topic=self.c["flows"].get(r["flow"], {}).get("title", self.label("request")),
                              status=self.c["texts"].get("status_" + r["status"], self.text("status_unknown")))
                    for r in self.customer.get("requests", [])]
            self.screen(self.text("status") + "\n\n" + ("\n\n".join(rows) or self.text("status_empty")),
                        [(self.label("status_refresh"), "menu:status"),
                         (self.label("status_ask"), "flow:status_request")] + self.nav())
            return
        if name == "news":
            subscribed = self.customer.get("subscribed", False)
            items = [f"{item['date']} · {item['title']}\n{item['body']}"
                     for item in self.c.get("news_items", [])] if subscribed else []
            self.screen("\n\n".join([self.text("news"), self.text("news_on" if subscribed else "news_off")] + items),
                        [(self.label("unsubscribe" if subscribed else "subscribe"),
                          "news:off" if subscribed else "news:on")] + self.nav())
            return
        menu = self.c["menus"][name]
        items = list(menu["items"])
        if name == "home" and self.customer.get("profile"):
            status_index = next(i for i, (_, action) in enumerate(items) if action == "menu:status")
            items.insert(status_index, (self.label("update_info"), "profile:open"))
        if name != "home":
            items += self.nav()
        self.screen(self.text(menu["text"]), items, menu["columns"])

    def info(self, name):
        self.s["screen"] = "info:" + name
        info = self.c["info"][name]
        self.screen(info["text"], info["items"] + self.nav(info["parent"]))

    def category(self, name):
        self.s["screen"] = "category:" + name
        title, parent, url = self.c["categories"][name]
        self.screen(self.text("category", name=title), [
            (self.label("website"), url), (self.label("ask"), "flow:" + parent)
        ] + self.nav(parent))

    def fields(self):
        flow = self.c["flows"][self.s["draft"]["flow"]]
        return flow["fields"] + flow.get("contact_fields", self.c["contact_fields"])

    def remember(self, key, value):
        profile = self.customer.setdefault("profile", {})
        profile[key] = value
        self.save_profile(dict(profile))

    def time_notice(self, value):
        hours = self.c["working_hours"]
        if valid_time(value) and not hours["start"] <= value < hours["end"]:
            return "\n\n" + self.text("outside_working_hours", time=value, **hours)
        return ""

    def shared_contact(self, contact):
        if contact.get("user_id") != self.chat_id:
            self.send(self.text("foreign_contact"))
            return
        phone = contact.get("phone_number", "")
        phone = normalize_phone(phone if phone.startswith("+") else "+" + phone)
        if not phone:
            self.send(self.text("invalid_phone"))
            return
        self.remember("phone", phone)
        name = " ".join(filter(None, [contact.get("first_name"), contact.get("last_name")])).strip()
        if (name and len(name) <= self.c["fields"]["name"]["max_length"]
                and not any(ord(c) < 32 for c in name) and not self.customer["profile"].get("name")):
            self.remember("name", name)
        self.send(self.text("profile_saved"))
        self.menu()

    def profile(self):
        self.s.pop("profile_field", None)
        self.s["screen"] = "profile"
        profile = self.customer.get("profile", {})
        summary = "\n".join(self.c["fields"][key]["label"] + ": " + str(profile.get(key) or self.label("not_provided"))
                            for key in self.c["contact_fields"])
        self.screen(self.text("profile", summary=summary) + self.time_notice(profile.get("time")),
                    [(self.c["fields"][key]["label"], "profile:" + key) for key in self.c["contact_fields"]]
                    + [(self.label("back"), "profile:done")])

    def profile_field(self, key):
        self.s["profile_field"] = key
        self.s["screen"] = "profile_field"
        field = self.c["fields"][key]
        items = [(value, "option:" + str(i)) for i, value in enumerate(field.get("options", []))]
        if field.get("optional"):
            items.append((self.label("skip"), "skip"))
        self.screen(field["prompt"], items + [(self.label("back"), "back")], phone=field.get("type") == "phone")

    def start_flow(self, name):
        previous = self.s.get("screen", "")
        draft = {"key": uuid.uuid4().hex, "flow": name, "step": 0, "answers": {}}
        profile = self.customer.get("profile", {})
        draft["reused_contacts"] = [key for key in self.c["flows"][name].get("contact_fields", self.c["contact_fields"]) if key in profile
                                    and (key != "time" or valid_time(profile[key]))]
        draft["answers"].update({key: profile[key] for key in draft["reused_contacts"]})
        if previous.startswith("category:"):
            category = previous.split(":", 1)[1]
            title, parent, url = self.c["categories"][category]
            if parent == name:
                draft["category"] = title
                draft["category_url"] = url
        elif previous.startswith("info:"):
            draft["origin"] = previous
        pending = self.s.pop("pending_text", None)
        if previous == "fallback" and name in {"general", "written", "callback"} and pending:
            field = self.c["flows"][name]["fields"][0]
            draft["answers"][field] = pending[:self.c["fields"][field]["max_length"]]
            draft["step"] = 1
        self.s["draft"] = draft
        intro = self.c["flows"][name].get("intro")
        if intro:
            self.send(self.text(intro))
        self.prompt()

    def prompt(self):
        draft = self.s["draft"]
        while (not draft.get("editing") and draft["step"] < len(self.fields())
               and self.fields()[draft["step"]] in draft.get("reused_contacts", [])):
            draft["step"] += 1
        if draft["step"] >= len(self.fields()):
            self.review()
            return
        self.s["screen"] = "field"
        key = self.fields()[draft["step"]]
        field = self.c["fields"][key]
        unknown = draft.get("unknown_field") == key
        text = field["unknown_prompt"] if unknown else field["prompt"]
        items = [] if unknown else [(value, "option:" + str(index)) for index, value in enumerate(field.get("options", []))]
        if field.get("type") == "photos":
            text += "\n" + self.text("photo_prompt")
            if draft["answers"].get(key):
                text += "\n" + self.text("photo_added", count=len(draft["answers"][key]))
                items.append((self.label("done"), "done"))
        if field.get("optional"):
            items.append((self.label("skip"), "skip"))
        items += [(self.label("back"), "back"), (self.label("cancel"), "cancel")]
        self.screen(text, items, phone=field.get("type") == "phone")

    def summary(self):
        draft = self.s["draft"]
        lines = [self.label("topic") + ": " + self.c["flows"][draft["flow"]]["title"]]
        if draft.get("category"):
            lines += [self.label("category") + ": " + draft["category"], draft["category_url"]]
        for key in self.fields():
            field = self.c["fields"][key]
            value = draft["answers"].get(key)
            if field.get("type") == "photos":
                value = str(len(value or []))
            lines.append(field["label"] + ": " + str(value or self.label("not_provided")))
        return "\n".join(lines)

    def review(self):
        if "time" in self.fields() and not valid_time(self.s["draft"]["answers"].get("time")):
            self.s["draft"]["step"] = self.fields().index("time")
            self.s["draft"]["editing"] = True
            self.prompt()
            return
        self.s["screen"] = "review"
        items = [(self.label("submit"), "submit")]
        if "phone" in self.fields():
            items.append((self.label("update_info"), "profile:open"))
        self.screen(self.text("review", summary=self.summary()) + self.time_notice(self.s["draft"]["answers"].get("time")), items + [
            (self.label("edit"), "edit"),
            (self.label("cancel"), "cancel")])

    def edit(self):
        self.s["screen"] = "edit"
        self.screen(self.text("edit"), [(self.c["fields"][key]["label"], "edit:" + key)
                    for key in self.fields()] + [(self.label("return_review"), "review")])

    def exit_draft(self):
        self.s.pop("profile_field", None)
        self.s["screen"] = "exit"
        self.screen(self.text("draft_exit"), [
            (self.label("resume"), "resume"), (self.label("discard"), "discard")])

    def accept(self, value):
        profile_key = self.s.get("profile_field")
        key = profile_key or self.fields()[self.s["draft"]["step"]]
        if self.c["fields"][key].get("type") == "time":
            value = normalize_time(value)
            if value is None:
                self.send(self.text("invalid_time"))
                return
        if profile_key:
            self.remember(profile_key, value)
            if self.s.get("draft"):
                self.s["draft"]["answers"][profile_key] = value
            self.profile()
            return
        draft = self.s["draft"]
        draft.pop("unknown_field", None)
        draft["answers"][self.fields()[draft["step"]]] = value
        if self.fields()[draft["step"]] in self.c["contact_fields"]:
            self.remember(self.fields()[draft["step"]], value)
        if draft.pop("editing", False):
            draft["step"] = len(self.fields())
        else:
            draft["step"] += 1
        self.prompt()

    def handle(self, message, callback=None):
        if callback:
            data = callback.get("data", "")
            self.actions.append(("answerCallbackQuery", {"callback_query_id": callback["id"]}))
            last = self.s.get("last_submission", {})
            if data == last.get("callback"):
                self.send(self.text("already_saved", number=last["number"]))
                return self.actions
            revision, separator, action = data.partition("|")
            if not separator or revision != self.s.get("revision") or action not in self.s.get("buttons", {}):
                self.send(self.text("stale"))
                return self.actions
            self.action(action, data)
            return self.actions

        raw_text = message.get("text", "")
        text = raw_text.strip()
        # Reply keyboards send their visible labels as text. Accept both decorated
        # and legacy labels while keeping saved answers and callback IDs unchanged.
        button_labels = list(self.s.get("buttons", {}).values())
        button_labels += [self.label(key) for key in ("home", "back", "cancel")]
        text = next((label for label in button_labels if self.button_label(label) == text), text)
        command = text.split(" ", 1)[0].split("@", 1)[0].lower()
        if command in {"/start", "/menu", "/cancel"} or text == self.label("home"):
            self.exit_draft() if self.s.get("draft") else self.menu()
        elif command == "/help":
            self.send(self.text("help"))
            if self.customer.get("staff"):
                self.send(self.text("help_staff"))
        elif command in {"/status", "/news"}:
            self.exit_draft() if self.s.get("draft") else self.menu(command[1:])
        elif text in {self.label("back"), self.label("cancel")} and (self.s.get("draft") or self.s.get("profile_field")):
            self.action("back" if text == self.label("back") else "cancel")
        elif message.get("voice") or message.get("audio") or message.get("video_note"):
            if self.s.get("draft"):
                self.send(self.text("voice"))
            else:
                self.fallback(notice=self.text("voice"), timestamp=message.get("date"))
        elif self.s.get("profile_field") or (self.s.get("draft") and self.s.get("screen") == "field"):
            self.answer(message, text)
        elif self.s.get("draft"):
            # Never consume text at a confirmation or cancellation screen.
            self.send(self.text("invalid_choice"))
        elif message.get("contact"):
            self.shared_contact(message["contact"])
        else:
            # Reply-keyboard users and typed menu labels may navigate, too.
            action = next((action for action, label in self.s.get("buttons", {}).items() if label == text), None)
            if action:
                self.action(action)
            else:
                question = re.sub(r"^/(?:ai|erp)(?:@mtech_customer_support_bot)?(?:\s+|$)", "", text, flags=re.I)
                self.fallback(question, timestamp=message.get("date"))
        return self.actions

    def action(self, action, callback_data=None):
        if action == "back" and self.s.get("screen") == "profile":
            self.review() if self.s.get("draft") else self.menu()
        elif action.startswith("profile:"):
            key = action.split(":", 1)[1]
            if key == "open":
                self.profile()
            elif key == "done":
                self.review() if self.s.get("draft") else self.menu()
            elif key in self.c["contact_fields"]:
                self.profile_field(key)
        elif self.s.get("profile_field") and action in {"back", "cancel"}:
            self.profile()
        elif action in {"cancel", "menu:home"} and self.s.get("draft"):
            self.exit_draft()
        elif action == "discard":
            self.s.pop("draft", None)
            self.send(self.text("cancelled"))
            self.menu()
        elif action == "resume":
            self.prompt()
        elif action == "back":
            draft = self.s["draft"]
            if draft.pop("unknown_field", None):
                self.prompt()
            elif draft.pop("editing", False):
                draft["step"] = len(self.fields())
                self.review()
            elif draft["step"] == 0:
                self.exit_draft()
            else:
                draft["step"] -= 1
                while draft["step"] > 0 and self.fields()[draft["step"]] in draft.get("reused_contacts", []):
                    draft["step"] -= 1
                self.prompt()
        elif action == "submit":
            if "time" in self.fields() and not valid_time(self.s["draft"]["answers"].get("time")):
                self.review()
                return
            try:
                number = self.submit(self.s["draft"], self.summary())
            except RequestLimit:
                self.send(self.text("request_limit"))
                return
            self.s["last_submission"] = {"number": number, "callback": callback_data}
            draft = self.s.pop("draft")
            self.send(self.text("written_saved" if draft["flow"] == "written" else "saved", number=number))
            self.menu()
        elif action == "edit":
            self.edit()
        elif action == "review":
            self.review()
        elif action.startswith("edit:"):
            self.s["draft"]["step"] = self.fields().index(action.split(":", 1)[1])
            self.s["draft"]["editing"] = True
            self.prompt()
        elif action == "skip":
            self.accept(None)
        elif action == "done":
            self.accept(self.s["draft"]["answers"].get("photos", []))
        elif action.startswith("option:"):
            key = self.s.get("profile_field") or self.fields()[self.s["draft"]["step"]]
            field = self.c["fields"][key]
            if field.get("type") == "time":
                # A pre-deployment keyboard may still offer the old free-text choice.
                self.send(self.text("invalid_time"))
                self.profile_field(key) if self.s.get("profile_field") else self.prompt()
            elif field.get("unknown_prompt"):
                self.s["draft"]["unknown_field"] = key
                self.prompt()
            else:
                self.accept(field["options"][int(action.split(":", 1)[1])])
        elif not self.s.get("draft"):
            if action in {"news:on", "news:off"}:
                self.customer["subscribed"] = action == "news:on"
                self.subscribe(self.customer["subscribed"])
                self.send(self.text("news_subscribed" if self.customer["subscribed"] else "news_unsubscribed"))
                self.menu("news")
                return
            kind, key = action.split(":", 1)
            {"menu": self.menu, "info": self.info, "category": self.category, "flow": self.start_flow}[kind](key)

    def answer(self, message, text):
        draft = self.s.get("draft", {})
        key = self.s.get("profile_field") or self.fields()[draft["step"]]
        field = self.c["fields"][key]
        kind = field.get("type", "text")
        if kind == "photos":
            photos = draft["answers"].get(key) or []
            if not message.get("photo"):
                self.send(self.text("photo_required"))
            elif len(photos) >= 3:
                self.send(self.text("photo_limit"))
            else:
                photo = message["photo"][-1]
                if not any(p["file_unique_id"] == photo["file_unique_id"] for p in photos):
                    photos.append({"file_id": photo["file_id"], "file_unique_id": photo["file_unique_id"]})
                draft["answers"][key] = photos
                self.prompt()
            return
        if kind == "phone":
            contact = message.get("contact")
            if contact:
                if contact.get("user_id") != self.chat_id:
                    self.send(self.text("foreign_contact"))
                    return
                text = contact.get("phone_number", "")
                if not text.startswith("+"):
                    text = "+" + text
            phone = normalize_phone(text)
            if phone:
                self.accept(phone)
            else:
                self.send(self.text("invalid_phone"))
        elif kind == "time":
            self.accept(text)
        elif kind == "choice":
            if text in field["options"]:
                self.accept(text)
            else:
                self.send(self.text("invalid_choice"))
        elif field.get("unknown_prompt") and text in field.get("options", []):
            draft["unknown_field"] = key
            self.prompt()
        elif not text or len(text) > field.get("max_length", 700) or any(ord(c) < 32 and c not in "\n\t" for c in text):
            self.send(self.text("invalid_text", limit=field.get("max_length", 700)))
        else:
            self.accept(text)
