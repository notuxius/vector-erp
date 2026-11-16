"""Generate non-personal parity cases from all existing Python behavioral tests."""
import copy
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from services.telegram_bot.engine import Engine, RequestLimit
from services.telegram_bot import test_bot

cases = []
original = Engine.handle

def capture(self, message, callback=None):
    case = {"state": copy.deepcopy(self.s), "message": copy.deepcopy(message),
            "callback": copy.deepcopy(callback), "chatId": self.chat_id,
            "customer": copy.deepcopy(self.customer)}
    submit = self.submit
    def wrapped(draft, summary):
        try:
            number = submit(draft, summary)
            case["submission"] = {"draft": copy.deepcopy(draft), "summary": summary, "number": number}
            return number
        except RequestLimit:
            case["limited"] = True
            raise
    self.submit = wrapped
    actions = original(self, message, callback)
    case["expectedState"] = copy.deepcopy(self.s)
    case["expectedActions"] = copy.deepcopy(actions)
    cases.append(case)
    return actions

Engine.handle = capture
result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(test_bot))
assert result.wasSuccessful()
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(cases, ensure_ascii=False))
