"""Behavioral tests; no network, real credentials, or customer messages."""

import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from .__main__ import bind_identity, worker_lock
from .content import load_content
from .commands import menu_commands
from .engine import Engine, chunks, normalize_phone
from .storage import Store
from .transport import ApiError, Telegram, dispatch, preflight


class BotCase(unittest.TestCase):
    def setUp(self):
        self.content = load_content()
        self.store = Store(":memory:")
        self.chat = 123
        self.staff = -456
        self.counter = 0

    def tearDown(self):
        self.store.close()

    def test_main_menu_sections_have_only_back_navigation_and_original_labels(self):
        self.send(contact={"user_id": self.chat, "phone_number": "+380630000000", "first_name": "Тест"})
        home_items = self.content["menus"]["home"]["items"]
        for action in [target for _, target in home_items] + ["profile:open"]:
            self.send("/menu")
            home_rows = self.queued("sendMessage")[-1]["reply_markup"]["inline_keyboard"]
            self.assertEqual([len(row) for row in home_rows], [2, 2, 2, 2])
            self.assertIn("📣 Новини та пропозиції", [b["text"] for row in home_rows for b in row])
            self.assertIn("🛠️ Сервіс і підтримка", [b["text"] for row in home_rows for b in row])
            self.send(action=action)
            buttons = [b for row in self.queued("sendMessage")[-1]["reply_markup"]["inline_keyboard"] for b in row]
            self.assertNotIn("🏠 Головне меню", [b["text"] for b in buttons])
            back = [b for b in buttons if b["text"] == "⬅️ Назад"]
            self.assertEqual(len(back), 1, action)
            self.send(action=back[0]["callback_data"].split("|", 1)[1])
            self.assertEqual(self.store.state(self.chat)["screen"], "menu:home")

    def event(self, text=None, action=None, data=None, **extra):
        self.counter += 1
        message = {"chat": {"id": self.chat, "type": "private"}, "from": {"id": self.chat}}
        message.update(extra)
        if action is not None or data is not None:
            state = self.store.state(self.chat)
            if data is None:
                self.assertIn(action, state["buttons"])
                data = state["revision"] + "|" + action
            return {"update_id": self.counter, "callback_query": {
                "id": "cb" + str(self.counter), "from": {"id": self.chat}, "message": message, "data": data}}
        if text is not None:
            message["text"] = text
        return {"update_id": self.counter, "message": message}

    def send(self, text=None, action=None, data=None, **extra):
        event = self.event(text, action, data, **extra)
        self.store.process(event, self.content, self.staff)
        return event

    def start(self, flow="callback"):
        self.send("/start")
        entry = {
            "equipment": ["menu:equipment", "category:metal-lasers"],
            "parts": ["menu:parts"],
            "selection": ["menu:equipment"], "repair": ["menu:support"], "training": ["menu:support"],
            "warranty": ["menu:support", "info:warranty"],
            "returns": ["menu:support", "info:returns"], "order": ["menu:support"],
            "delivery": ["menu:delivery"], "callback": ["info:contacts"], "written": ["info:contacts"], "general": [],
            "status_request": ["menu:status"]
        }[flow]
        for action in entry:
            self.send(action=action)
        if flow == "general":
            self.send("Маю запитання")
            # Simulate the general-enquiry button issued by the previous release.
            state = self.store.state(self.chat)
            state["buttons"]["flow:general"] = self.content["labels"]["general"]
            self.store.db.execute("UPDATE sessions SET state=? WHERE chat_id=?", (json.dumps(state), self.chat))
        self.send(action="flow:" + flow)

    def complete_fields(self):
        while self.store.state(self.chat)["screen"] == "field":
            state = self.store.state(self.chat)
            draft = state["draft"]
            keys = self.content["flows"][draft["flow"]]["fields"] + self.content["contact_fields"]
            key = keys[draft["step"]]
            field = self.content["fields"][key]
            if field.get("optional"):
                self.send(action="skip")
            elif field.get("unknown_prompt"):
                self.send("Деталь до верстата")
            elif field.get("options"):
                self.send(action="option:0")
            elif key == "phone":
                self.send("+380 (63) 000-00-00")
            elif key == "time":
                self.send("09:30")
            else:
                self.send("Тестове значення")

    def queued(self, method=None):
        return [json.loads(row["payload"]) for row in self.store.db.execute(
            "SELECT method,payload FROM outbox ORDER BY id") if method is None or row["method"] == method]

    def profile(self):
        row = self.store.db.execute("SELECT data FROM customer_profiles WHERE chat_id=?", (self.chat,)).fetchone()
        return json.loads(row[0]) if row else {}

    def test_profile_back_returns_to_previous_screen_and_preserves_draft(self):
        self.start()
        self.complete_fields()
        draft = self.store.state(self.chat)["draft"]
        for typed in (False, True):
            self.send(action="profile:open")
            buttons = self.queued("sendMessage")[-1]["reply_markup"]["inline_keyboard"]
            self.assertEqual(buttons[-1][0]["text"], "⬅️ Назад")
            if typed:
                self.send("⬅️ Назад")
            else:
                self.send(action="profile:done")
            self.assertEqual(self.store.state(self.chat)["screen"], "review")
            self.assertEqual(self.store.state(self.chat)["draft"], draft)
        self.send(action="cancel")
        self.send(action="discard")
        for typed in (False, True):
            self.send(action="profile:open")
            self.assertEqual(self.store.state(self.chat)["buttons"]["profile:done"], "Назад")
            if typed:
                self.send("⬅️ Назад")
            else:
                self.send(action="profile:done")
            self.assertEqual(self.store.state(self.chat)["screen"], "menu:home")
            self.assertNotIn("draft", self.store.state(self.chat))
        self.assertEqual(self.store.requests(), [])

    def test_time_rejects_invalid_values_in_profile_and_request(self):
        self.start()
        self.complete_fields()
        self.send(action="profile:open")
        self.send(action="profile:time")
        for value in ("930", "009:30", "9:030", "24", "24:00", "12:60", "-1:00", "09.30", "9 AM", "У робочий час",
                      "завтра 09:30", "09:30-10:30", "09:30\n18:00", "٠٩:٣٠"):
            self.send(value)
            self.assertEqual(self.store.state(self.chat)["screen"], "profile_field")
            self.assertEqual(self.profile()["time"], "09:30")
            self.assertEqual(self.queued("sendMessage")[-1]["text"], self.content["texts"]["invalid_time"])
        for value in ("00:00", "23:59"):
            self.send(value)
            self.assertEqual(self.profile()["time"], value)
            self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["time"], value)
            if value == "00:00":
                self.send(action="profile:time")
        self.send(action="profile:done")
        self.send(action="submit")
        self.assertEqual(self.store.request(1)["payload"]["answers"]["time"], "23:59")

    def test_legacy_time_is_reasked_and_old_options_and_submit_cannot_bypass_validation(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO customer_profiles VALUES(?,?,?)", (self.chat, json.dumps({
                "name": "Олена", "phone": "+380630000000", "time": "У робочий час"}), time.time()))
        self.start()
        self.send("Дзвінок")
        draft = self.store.state(self.chat)["draft"]
        self.assertEqual(draft["step"], 3)
        self.assertNotIn("time", draft["answers"])
        state = self.store.state(self.chat)
        state["buttons"]["option:0"] = "У робочий час"
        with self.store.db:
            self.store.db.execute("UPDATE sessions SET state=? WHERE chat_id=?", (json.dumps(state), self.chat))
        self.send(action="option:0")
        self.assertNotIn("option:0", self.store.state(self.chat)["buttons"])
        self.assertEqual(self.profile()["time"], "У робочий час")
        self.send("18:00")
        state = self.store.state(self.chat)
        state["draft"]["answers"]["time"] = "У робочий час"
        with self.store.db:
            self.store.db.execute("UPDATE sessions SET state=? WHERE chat_id=?", (json.dumps(state), self.chat))
        self.send(action="submit")
        self.assertEqual(self.store.requests(), [])
        self.assertEqual(self.store.state(self.chat)["screen"], "field")
        self.send("12:60")
        self.assertEqual(self.store.state(self.chat)["screen"], "field")
        self.send("09:30")
        self.send(action="submit")
        self.assertEqual(self.store.request(1)["payload"]["answers"]["time"], "09:30")

    def test_time_accepts_optional_zeros_and_space_separator_and_saves_canonical_value(self):
        cases = [("9:30", "09:30"), ("9 30", "09:30"), ("09:3", "09:03"), ("9 5", "09:05"),
                 ("0:0", "00:00"), ("0 0", "00:00"), ("23 59", "23:59"), ("09 05", "09:05"),
                 ("9   5", "09:05"), ("9", "09:00"), ("09", "09:00"), ("0", "00:00"), ("23", "23:00")]
        for index, (value, expected) in enumerate(cases):
            with self.subTest(value=value):
                self.chat = 2000 + index
                self.start()
                self.send("Дзвінок")
                self.send("Олена")
                self.send("+380630000000")
                self.send(value)
                self.assertEqual(self.store.state(self.chat)["screen"], "review")
                self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["time"], expected)
                self.send(action="profile:open")
                self.send(action="profile:time")
                self.send(value)
                self.assertEqual(self.profile()["time"], expected)
                self.send(action="profile:done")
                self.send(action="submit")
                self.assertEqual(self.store.request(index + 1)["payload"]["answers"]["time"], expected)

    def test_outside_working_hours_notice_in_profile_review_and_reused_contact(self):
        for index, (value, expected_notice) in enumerate([
                ("7 59", True), ("8", False), ("16:59", False), ("17", True), ("23:59", True), ("0", True)]):
            with self.subTest(value=value):
                self.chat = 3000 + index
                self.start()
                self.complete_fields()
                self.send(action="profile:open")
                self.send(action="profile:time")
                self.send(value)
                text = self.queued("sendMessage")[-1]["text"]
                self.assertEqual("поза робочими годинами" in text, expected_notice)
                if expected_notice:
                    self.assertIn("понеділок–п’ятниця, 08:00–17:00 за Києвом", text)
                    self.assertIn("дзвінок у цей час не гарантований", text)
                self.send(action="profile:done")
                self.assertEqual("поза робочими годинами" in self.queued("sendMessage")[-1]["text"], expected_notice)
                self.send(action="submit")
                self.assertEqual(self.store.request(index + 1)["payload"]["answers"]["time"], self.profile()["time"])
                self.start()
                self.send("Інше питання")
                self.assertEqual(self.store.state(self.chat)["screen"], "review")
                self.assertEqual("поза робочими годинами" in self.queued("sendMessage")[-1]["text"], expected_notice)

    def test_saved_contacts_skip_all_repeat_questions_after_expiry_and_restart(self):
        self.start()
        self.complete_fields()
        self.send(action="submit")
        expected = self.profile()
        self.assertEqual(set(expected), {"name", "phone", "time"})
        self.assertEqual(expected["time"], "09:30")
        with self.store.db:
            self.store.db.execute("UPDATE sessions SET updated_at=0")
        self.store.maintenance()
        self.start()
        self.send("Інша тема дзвінка")
        state = self.store.state(self.chat)
        self.assertEqual(state["screen"], "review")
        self.assertEqual({k: state["draft"]["answers"][k] for k in expected}, expected)
        self.assertIn("profile:open", state["buttons"])
        self.send(action="cancel")
        self.send(action="discard")
        self.assertIn("profile:open", self.store.state(self.chat)["buttons"])
        self.chat = 789
        self.start()
        self.send("Нове питання")
        self.assertEqual(self.store.state(self.chat)["draft"]["step"], 1)
        self.assertEqual(self.profile(), {})

    def test_profile_update_changes_current_and_future_requests_but_not_history(self):
        self.start()
        self.complete_fields()
        self.send(action="submit")
        previous = self.store.request(1)["payload"]
        self.start()
        self.send("Інша тема")
        self.send(action="profile:open")
        self.send(action="profile:phone")
        self.send("123")
        self.send(contact={"user_id": 999, "phone_number": "380999999999"})
        self.assertEqual(self.profile()["phone"], previous["answers"]["phone"])
        self.send(contact={"user_id": self.chat, "phone_number": "380671111111"})
        self.assertEqual(self.store.state(self.chat)["screen"], "profile")
        self.assertEqual(self.profile()["phone"], "+380671111111")
        self.send(action="profile:time")
        self.send("18:00")
        self.send(action="profile:done")
        self.assertEqual(self.store.state(self.chat)["screen"], "review")
        self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["phone"], "+380671111111")
        self.assertEqual(self.store.request(1)["payload"], previous)
        self.assertTrue(self.queued("sendMessage")[-1]["text"].find("+380671111111") >= 0)

    def test_shared_contact_profile_can_be_updated_without_creating_request(self):
        self.send(contact={"user_id": self.chat, "phone_number": "380630000000", "first_name": "Олена", "last_name": "Тест"})
        self.assertEqual(self.profile(), {"name": "Олена Тест", "phone": "+380630000000"})
        self.send(action="profile:open")
        self.send(action="profile:name")
        self.send("x" * 81)
        self.assertEqual(self.profile()["name"], "Олена Тест")
        self.send(action="back")
        self.send(action="profile:name")
        self.send("Марія")
        self.send(action="profile:phone")
        self.send(self.content["labels"]["cancel"])
        self.assertEqual(self.store.state(self.chat)["screen"], "profile")
        self.send(action="profile:time")
        self.send("09:30")
        self.send(action="profile:done")
        self.assertEqual(self.profile()["name"], "Марія")
        self.assertEqual(self.store.requests(), [])
        self.start()
        self.send("Дзвінок")
        self.assertEqual(self.store.state(self.chat)["screen"], "review")

    def test_partial_contacts_survive_cancellation_and_failed_profile_write_rolls_back(self):
        self.start()
        self.send("Дзвінок")
        self.send("Олена")
        self.send(action="cancel")
        self.send(action="discard")
        self.assertEqual(self.profile(), {"name": "Олена"})
        self.start()
        self.send("Дзвінок")
        self.assertEqual(self.store.state(self.chat)["draft"]["step"], 2)
        event = self.event("+380630000000")
        before = self.store.state(self.chat)
        with patch.object(self.store, "enqueue", side_effect=OSError("write failure")):
            with self.assertRaises(OSError):
                self.store.process(event, self.content, self.staff)
        self.assertEqual(self.profile(), {"name": "Олена"})
        self.assertEqual(self.store.state(self.chat), before)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM updates WHERE id=?", (event["update_id"],)).fetchone())

    def test_profile_survives_store_reopen_and_legacy_draft_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.sqlite3"
            first = Store(path)
            with first.db:
                first.db.execute("INSERT INTO sessions VALUES(?,?,?)", (self.chat, json.dumps({"draft": {
                    "answers": {"name": "Олена", "phone": "+380630000000", "time": None, "topic": "not a profile field"}}}), 1))
            first.close()
            second = Store(path)
            expected = second.db.execute("SELECT data FROM customer_profiles").fetchone()[0]
            self.assertEqual(json.loads(expected), {"name": "Олена", "phone": "+380630000000", "time": None})
            second.maintenance()
            second.close()
            third = Store(path)
            self.assertEqual(third.db.execute("SELECT data FROM customer_profiles").fetchone()[0], expected)
            third.close()

    def test_every_customer_flow_reaches_staff_and_customer_confirmation(self):
        for index, flow in enumerate(self.content["flows"]):
            with self.subTest(flow=flow):
                self.chat = 1000 + index
                self.start(flow)
                self.complete_fields()
                self.assertEqual(self.store.state(self.chat)["screen"], "review")
                self.assertFalse(self.store.db.execute("SELECT 1 FROM requests WHERE chat_id=?", (self.chat,)).fetchone())
                self.send(action="submit")
                record = self.store.db.execute("SELECT * FROM requests WHERE chat_id=?", (self.chat,)).fetchone()
                self.assertEqual(record["flow"], flow)
                payload = json.loads(record["payload"])
                if flow == "written":
                    self.assertEqual(set(payload["answers"]), {"message"})
                else:
                    self.assertEqual(payload["answers"]["phone"], "+380630000000")
                self.assertEqual(payload["content_reviewed_at"], "2026-10-30")
                self.assertIsNone(self.store.state(self.chat).get("draft"))
                self.assertTrue(any(m.get("chat_id") == self.chat and "зареєстровано" in m.get("text", "")
                                    for m in self.queued("sendMessage")))
                self.assertTrue(self.store.db.execute("SELECT 1 FROM outbox WHERE request_id=? AND chat_id=?",
                                                     (record["id"], self.staff)).fetchone())

    def test_status_is_private_refreshes_and_offers_manager_enquiry(self):
        self.start("repair")
        self.complete_fields()
        self.send(action="submit")
        number = self.store.requests()[0]["id"]
        self.send("/status")
        self.assertIn("Зареєстровано", self.queued("sendMessage")[-1]["text"])
        self.store.set_status(number, "in_progress")
        self.send(action="menu:status")
        self.assertIn("В опрацюванні", self.queued("sendMessage")[-1]["text"])
        self.assertNotIn("+380", self.queued("sendMessage")[-1]["text"])
        self.chat = 456
        self.send("/status")
        text = self.queued("sendMessage")[-1]["text"]
        self.assertIn(self.content["texts"]["status_empty"], text)
        self.assertNotIn("№" + str(number), text)
        self.send(action="flow:status_request")
        self.assertEqual(self.store.state(self.chat)["draft"]["flow"], "status_request")

    def test_subscription_requires_opt_in_survives_expiry_and_can_be_revoked(self):
        self.send("/start")
        self.send(action="menu:news")
        self.assertFalse(self.store.db.execute("SELECT 1 FROM subscriptions").fetchone())
        event = self.send(action="news:on")
        self.assertFalse(self.store.process(event, self.content, self.staff))
        row = self.store.db.execute("SELECT * FROM subscriptions").fetchone()
        self.assertEqual(row["subscribed"], 1)
        self.assertEqual(row["consent_version"], "mtech-news-v1")
        self.assertGreater(row["updated_at"], 0)
        self.store.db.execute("UPDATE sessions SET updated_at=0")
        self.store.db.commit()
        self.store.maintenance()
        self.send("/news")
        self.assertIn("news:off", self.store.state(self.chat)["buttons"])
        self.send(action="news:off")
        self.send(data=event["callback_query"]["data"])
        self.assertEqual(self.store.db.execute("SELECT subscribed FROM subscriptions").fetchone()[0], 0)
        self.chat = 456
        self.send("/news")
        self.assertIn("news:on", self.store.state(self.chat)["buttons"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 1)

    def test_commands_stay_visible_while_news_content_follows_subscription(self):
        api, menus = self.menu_client()
        title = self.content["news_items"][0]["title"]

        def check(subscribed, draft=False):
            dispatch(self.store, api)
            for language in ("", "uk"):
                self.assertEqual(menus[(self.chat, language)], menu_commands(draft, subscribed))

        self.send("/news")
        self.assertNotIn(title, self.queued("sendMessage")[-1]["text"])
        check(False)
        self.send(action="news:on")
        self.assertIn(title, self.queued("sendMessage")[-1]["text"])
        check(True)
        self.start()
        check(True, True)
        self.store.reconcile_command_menus()
        check(True, True)
        with self.store.db:
            self.store.db.execute("UPDATE sessions SET updated_at=0")
        self.store.maintenance()
        check(True)
        self.send("/news")
        self.send(action="news:off")
        self.assertNotIn(title, self.queued("sendMessage")[-1]["text"])
        check(False)
        self.assertEqual([c["command"] for c in menu_commands()], ["menu", "help"])
        self.assertEqual(menu_commands(False, False), menu_commands(False, True))

    def test_new_commands_preserve_active_draft(self):
        self.start()
        self.send("Моє питання")
        draft = self.store.state(self.chat)["draft"]
        for command in ("/status", "/news"):
            self.send(command)
            self.assertEqual(self.store.state(self.chat)["draft"], draft)
            self.assertEqual(self.store.state(self.chat)["screen"], "exit")
            self.send(action="resume")

    def test_subscription_and_acknowledgement_rollback_together(self):
        self.send("/news")
        event = self.event(action="news:on")
        before = self.store.state(self.chat)
        self.store.db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'test write failure'); END;")
        self.store.db.commit()
        with self.assertRaises(Exception):
            self.store.process(event, self.content, self.staff)
        self.assertEqual(self.store.state(self.chat), before)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM subscriptions").fetchone())
        self.assertFalse(self.store.db.execute("SELECT 1 FROM updates WHERE id=?", (event["update_id"],)).fetchone())

    def test_callback_replay_and_duplicate_update_cannot_create_duplicate_request(self):
        self.start()
        self.complete_fields()
        event = self.send(action="submit")
        self.assertFalse(self.store.process(event, self.content, self.staff))
        self.send(data=event["callback_query"]["data"])
        self.assertEqual(len(self.store.requests()), 1)
        self.assertTrue(any("вже зареєстровано" in m.get("text", "") for m in self.queued()))

    def test_old_buttons_cannot_skip_steps_or_submit_early(self):
        self.start()
        state = self.store.state(self.chat)
        revision = state["revision"]
        self.send(data=revision + "|submit")
        self.assertEqual(self.store.state(self.chat)["draft"]["step"], 0)
        self.send("Потрібен дзвінок")
        self.send(data=revision + "|cancel")
        self.assertEqual(self.store.state(self.chat)["screen"], "field")
        self.assertEqual(len(self.store.requests()), 0)

    def test_cancel_requires_confirmation_and_resume_preserves_answers(self):
        self.start()
        self.send("Потрібна консультація")
        self.send("/menu")
        self.assertEqual(self.store.state(self.chat)["screen"], "exit")
        self.send(action="resume")
        self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["callback_topic"], "Потрібна консультація")
        self.send(action="cancel")
        self.send(action="discard")
        self.assertNotIn("draft", self.store.state(self.chat))
        self.assertEqual(len(self.store.requests()), 0)

    def menu_client(self):
        menus = {}

        class Client:
            def call(inner, method, payload):
                if method in {"setMyCommands", "deleteMyCommands"}:
                    key = (payload["scope"]["chat_id"], payload["language_code"])
                    if method == "setMyCommands":
                        menus[key] = payload["commands"]
                    else:
                        menus.pop(key, None)

        return Client(), menus

    def test_cancel_menu_tracks_draft_lifecycle_and_isolated_chats(self):
        api, menus = self.menu_client()

        def check(has_draft):
            dispatch(self.store, api)
            for language in ("", "uk"):
                commands = menus.get((self.chat, language), menu_commands())
                self.assertEqual([c["command"] for c in commands],
                                 ["menu", "help", "cancel"] if has_draft else ["menu", "help"])

        self.send("/start")
        check(False)
        self.start()
        check(True)
        self.chat = 456
        self.send("/start")
        check(False)
        self.chat = 123
        self.send("/cancel")
        check(True)  # Asking to cancel does not discard the draft.
        self.send(action="resume")
        check(True)
        self.send(action="cancel")
        self.send(action="discard")
        check(False)
        for language in ("", "uk"):
            self.assertEqual(menus[(self.chat, language)], menu_commands())
        self.assertEqual(self.queued("deleteMyCommands"), [])
        self.start()
        check(True)
        self.complete_fields()
        check(True)
        self.send(action="submit")
        check(False)
        for language in ("", "uk"):
            self.assertEqual(menus[(self.chat, language)], menu_commands())

    def test_menu_reconciliation_and_expiry_remove_cancel(self):
        api, menus = self.menu_client()
        self.start()
        dispatch(self.store, api)
        menus.clear()  # Simulate lost remote configuration before restarting.
        self.store.reconcile_command_menus()
        dispatch(self.store, api)
        self.assertEqual(len(menus), 2)
        with self.store.db:
            self.store.db.execute("UPDATE sessions SET updated_at=?", (time.time() - 8 * 86400,))
        self.store.maintenance()
        dispatch(self.store, api)
        self.assertEqual(self.store.state(self.chat), {})
        self.assertEqual(menus, {(self.chat, language): menu_commands() for language in ("", "uk")})

    def test_old_menu_retries_cannot_restore_cancel_after_discard(self):
        self.start()
        with self.store.db:
            self.store.db.execute("UPDATE outbox SET status='failed' WHERE method='setMyCommands'")
        self.send(action="cancel")
        self.send(action="discard")
        self.assertEqual(self.store.retry_failed(), 0)
        self.assertTrue(all(payload["commands"] == menu_commands()
                            for payload in self.queued("setMyCommands")))
        self.assertEqual(self.queued("deleteMyCommands"), [])
        # Scoped calls share the chat's delivery ordering without an invalid top-level chat_id.
        rows = self.store.db.execute("SELECT * FROM outbox WHERE method='setMyCommands'").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["chat_id"] == self.chat for row in rows))
        self.assertTrue(all("chat_id" not in json.loads(row["payload"]) for row in rows))

    def test_edit_phone_validates_and_removes_contact_keyboard(self):
        self.start()
        self.complete_fields()
        self.send(action="edit")
        self.send(action="edit:phone")
        self.send("123")
        self.assertEqual(self.store.state(self.chat)["screen"], "field")
        self.send(contact={"user_id": 999, "phone_number": "+380999999999"})
        self.assertEqual(self.store.state(self.chat)["screen"], "field")
        self.send(contact={"user_id": self.chat, "phone_number": "380671111111"})
        self.assertEqual(self.store.state(self.chat)["screen"], "review")
        self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["phone"], "+380671111111")
        self.assertTrue(any(m.get("reply_markup", {}).get("remove_keyboard") for m in self.queued()))

    def test_photos_limit_deduplication_edit_and_staff_delivery(self):
        self.start("parts")
        self.send("Лазерна трубка")
        self.send("1 шт")
        for name in ["one", "one", "two", "three", "four"]:
            self.send(photo=[{"file_id": name, "file_unique_id": name}])
        self.assertEqual(len(self.store.state(self.chat)["draft"]["answers"]["photos"]), 3)
        self.send(action="done")
        self.complete_fields()
        self.send(action="submit")
        photos = self.queued("sendPhoto")
        self.assertEqual([p["photo"] for p in photos], ["one", "two", "three"])
        self.assertTrue(all(p["chat_id"] == self.staff for p in photos))

    def test_photo_edit_skip_clears_previous_photos(self):
        self.start("repair")
        self.send("Модель")
        self.send("Помилка")
        self.send(photo=[{"file_id": "one", "file_unique_id": "one"}])
        self.send(action="done")
        self.complete_fields()
        self.send(action="edit")
        self.send(action="edit:photos")
        self.send(action="skip")
        self.send(action="submit")
        self.assertEqual(self.queued("sendPhoto"), [])

    def test_group_and_foreign_callback_messages_are_ignored(self):
        group = self.event("/start")
        group["message"]["chat"]["type"] = "supergroup"
        self.store.process(group, self.content, self.staff)
        self.assertEqual(self.store.state(self.chat), {})
        self.start()
        before = self.store.state(self.chat)
        event = self.event(action="cancel")
        event["callback_query"]["from"]["id"] = 999
        self.store.process(event, self.content, self.staff)
        self.assertEqual(self.store.state(self.chat), before)

    def test_transaction_rolls_back_request_session_cursor_and_outbox(self):
        self.start()
        self.complete_fields()
        event = self.event(action="submit")
        before_offset = self.store.offset()
        before_state = self.store.state(self.chat)
        original = self.store.enqueue

        def fail_confirmation(method, payload, request_id=None):
            if "зареєстровано" in payload.get("text", ""):
                raise OSError("simulated storage failure")
            return original(method, payload, request_id)

        with patch.object(self.store, "enqueue", side_effect=fail_confirmation):
            with self.assertRaises(OSError):
                self.store.process(event, self.content, self.staff)
        self.assertEqual(self.store.requests(), [])
        self.assertEqual(self.store.offset(), before_offset)
        self.assertEqual(self.store.state(self.chat), before_state)
        self.store.process(event, self.content, self.staff)
        self.assertEqual(len(self.store.requests()), 1)

    def test_invalid_choice_and_oversized_text_do_not_advance(self):
        self.start("selection")
        self.send("вигадана опція")
        self.assertEqual(self.store.state(self.chat)["draft"]["step"], 0)
        self.send(action="option:0")
        self.send("x" * 201)
        self.assertEqual(self.store.state(self.chat)["draft"]["step"], 1)

    def test_handoff_kyiv_boundaries_and_topic_links(self):
        for instant, closed in [("2026-11-06T14:59:00Z", False), ("2026-11-06T15:00:00Z", True),
                                ("2026-11-07T10:00:00Z", True), ("2026-11-09T05:59:00Z", True),
                                ("2026-11-09T06:00:00Z", False), ("2026-07-03T14:00:00Z", True)]:
            self.send("/ai Яка гарантія?", date=int(datetime.fromisoformat(instant).timestamp()))
            buttons = [b for row in self.queued("sendMessage")[-1]["reply_markup"]["inline_keyboard"] for b in row]
            self.assertEqual([b["url"] for b in buttons if "url" in b],
                             [self.content["self_help"][i]["url"] for i in (2, 0, 1, 3)] if closed else [])
            self.assertEqual(self.store.state(self.chat)["pending_text"], "Яка гарантія?")
            self.assertIn("flow:written", self.store.state(self.chat)["buttons"])
            self.assertIn("flow:callback", self.store.state(self.chat)["buttons"])
        self.send(action="flow:callback")
        self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["callback_topic"], "Яка гарантія?")

    def test_written_question_review_and_staff_reply(self):
        self.send("Яка гарантія?")
        self.send(action="flow:written")
        self.assertEqual(self.store.state(self.chat)["screen"], "review")
        self.send(action="edit")
        self.send(action="edit:message")
        self.send("Уточнення щодо верстата")
        self.assertIsNone(self.store.db.execute("SELECT id FROM requests").fetchone())
        self.send(action="submit")
        self.assertTrue(any("/reply 1 " in p["text"] for p in self.queued("sendMessage") if p["chat_id"] == self.staff))
        self.send("/reply 1 чужа відповідь")
        event = self.send("/reply 1 Текст менеджера", chat={"type": "supergroup", "id": self.staff})
        self.assertFalse(self.store.process(event, self.content, self.staff))
        replies = [p for p in self.queued("sendMessage") if p["text"].startswith("Менеджер MTech ·")]
        self.assertEqual([(p["chat_id"], p["text"]) for p in replies],
                         [(self.chat, "Менеджер MTech · заявка №1\n\nТекст менеджера")])

    def test_free_text_is_carried_into_general_enquiry(self):
        self.start("general")
        self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["message"], "Маю запитання")

    def test_unknown_part_requires_description_and_back_restores_question(self):
        self.start("parts")
        self.send(action="option:0")
        self.assertEqual(self.store.state(self.chat)["draft"]["step"], 0)
        self.assertIn("unknown_field", self.store.state(self.chat)["draft"])
        self.send(action="back")
        self.assertNotIn("unknown_field", self.store.state(self.chat)["draft"])
        self.send(action="option:0")
        self.send("Деталь із маркуванням ABC")
        self.assertEqual(self.store.state(self.chat)["draft"]["answers"]["part"], "Деталь із маркуванням ABC")

    def test_category_context_preserved_in_request(self):
        self.start("equipment")
        self.complete_fields()
        self.send(action="submit")
        record = self.store.request(1)
        self.assertEqual(record["payload"]["category"], "Лазерні верстати для металу")
        self.assertEqual(record["payload"]["category_url"],
                         "https://mtech-design-preview.pages.dev/catalogue.html?category=metal-lasers")

    def test_rate_limit_bounds_requests_without_losing_draft(self):
        self.start()
        for _ in range(35):
            self.send("/help")
        self.assertIn("draft", self.store.state(self.chat))
        self.assertEqual(sum(m.get("text") == self.content["texts"]["rate_limit"] for m in self.queued()), 1)

    def test_restart_preserves_draft_outbox_and_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.sqlite3"
            first = Store(path)
            first.process(self.event("/start"), self.content, self.staff)
            state = first.state(self.chat)
            event = self.event(data=state["revision"] + "|menu:equipment")
            first.process(event, self.content, self.staff)
            state = first.state(self.chat)
            event = self.event(data=state["revision"] + "|flow:selection")
            first.process(event, self.content, self.staff)
            before = first.state(self.chat)
            offset = first.offset()
            first.close()
            second = Store(path)
            self.assertEqual(second.state(self.chat), before)
            self.assertEqual(second.offset(), offset)
            self.assertTrue(second.due())
            second.close()

    def test_failed_staff_delivery_does_not_block_customer(self):
        self.start()
        self.complete_fields()
        self.send(action="submit")
        calls = []

        class Client:
            def call(inner, method, payload):
                if payload.get("chat_id") == self.staff:
                    raise ApiError(403)
                calls.append((method, payload))

        with self.assertLogs(level="ERROR"):
            dispatch(self.store, Client())
        self.assertTrue(any("зареєстровано" in p.get("text", "") for _, p in calls))
        self.assertEqual(self.store.health()["outbox"]["failed"], 1)
        self.assertEqual(self.store.retry_failed(), 1)
        self.assertEqual(len(self.store.requests()), 1)

    def test_retry_after_is_respected(self):
        self.send("/start")

        class Client:
            def call(inner, method, payload):
                raise ApiError(429, 30)

        with self.assertLogs(level="WARNING"):
            delay = dispatch(self.store, Client())
        self.assertEqual(delay, 30)
        self.assertEqual(self.store.due(), [])
        self.assertGreaterEqual(self.store.db.execute("SELECT available_at FROM outbox WHERE method='sendMessage' ORDER BY id LIMIT 1").fetchone()[0], time.time() + 28)


class InfrastructureCase(unittest.TestCase):
    def test_phone_formats(self):
        self.assertEqual(normalize_phone("00380 (63) 123-45-67"), "+380631234567")
        for value in ["0631234567", "+0123456789", "+380abcdefghi", "https://example.org", "+" + "1" * 16]:
            self.assertIsNone(normalize_phone(value))

    def test_utf16_splitting_preserves_long_unicode_text(self):
        text = "🙂" * 4000 + "Україна"
        parts = list(chunks(text))
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts))

    def test_telegram_errors_never_expose_token(self):
        token = "123:SECRET_TOKEN_SHOULD_NEVER_BE_LOGGED"
        with patch("urllib.request.urlopen", side_effect=OSError("https://api.telegram.org/bot" + token)):
            with self.assertRaises(ApiError) as caught:
                Telegram(token).call("getMe")
        self.assertNotIn(token, str(caught.exception))

    def test_preflight_refuses_existing_webhook_without_deleting_it(self):
        calls = []

        class Client:
            def call(self, method, payload=None):
                calls.append(method)
                return {"id": 1} if method == "getMe" else {"url": "https://existing.example"}

        with self.assertRaises(ValueError):
            preflight(Client(), 12)
        self.assertEqual(calls, ["getMe", "getWebhookInfo"])

    def test_database_identity_and_exclusive_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.sqlite3"
            store = Store(path)
            bind_identity(store, 1)
            with self.assertRaises(ValueError):
                bind_identity(store, 2)
            store.close()
            with worker_lock(path):
                with self.assertRaises(ValueError):
                    with worker_lock(path):
                        self.fail("Second worker acquired the lock")

    def test_content_links_are_constrained_to_mtech(self):
        content = load_content()
        content["categories"]["metal-lasers"][2] = "https://untrusted.example/"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "content.json"
            path.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_content(path)


if __name__ == "__main__":
    unittest.main()
