"""Load and validate the manually reviewed content. No runtime website scraping."""

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CONTENT = Path(__file__).with_name("content.json")


def load_content(path=DEFAULT_CONTENT):
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    if content["version"] != 1:
        raise ValueError("Unsupported content version")
    date.fromisoformat(content["reviewed_at"])

    def url(value):
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "mtech-design-preview.pages.dev":
            raise ValueError("Content links must use approved MTech HTTPS domains")

    def items(entries):
        for label, action in entries:
            if not label or len(label) > 64:
                raise ValueError("Invalid button label")
            if action.startswith("https:"):
                url(action)
            else:
                kind, key = action.split(":", 1)
                collection = {"menu": "menus", "flow": "flows", "info": "info", "category": "categories"}[kind]
                if key not in content[collection] or len(action.encode()) + 9 > 64:
                    raise ValueError("Invalid button target")

    for link in content["self_help"]:
        target = urlparse(link["url"])
        if target.scheme != "https" or target.netloc not in {"mtech-cnc.com.ua", "mtech.com.ua"}:
            raise ValueError("Invalid public self-help URL")
    for value in content["sources"].values():
        url(value)
    for menu in content["menus"].values():
        content["texts"][menu["text"]]
        items(menu["items"])
    for info in content["info"].values():
        content["menus"][info["parent"]]
        content["sources"][info["source"]]
        items(info["items"])
    for _, parent, target in content["categories"].values():
        content["menus"][parent]
        url(target)
    for flow in content["flows"].values():
        content["menus"][flow["parent"]]
        fields = flow["fields"] + flow.get("contact_fields", content["contact_fields"])
        if len(fields) != len(set(fields)):
            raise ValueError("Duplicate flow fields")
        for key in fields:
            field = content["fields"][key]
            if field.get("type", "text") not in {"text", "choice", "photos", "phone", "time"}:
                raise ValueError("Invalid field type")
            if field.get("type") == "choice" and not field.get("options"):
                raise ValueError("Choice field has no options")
    return content
