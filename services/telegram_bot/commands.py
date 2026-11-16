"""Telegram command menu shared by default and per-chat configuration."""


def menu_commands(has_draft=False, subscribed=False):
    commands = [
        {"command": "menu", "description": "Головне меню"},
        {"command": "help", "description": "Допомога"},
    ]
    # Subscription controls content and delivery, never command visibility.
    if has_draft:
        commands.append({"command": "cancel", "description": "Скасувати чернетку"})
    return commands
