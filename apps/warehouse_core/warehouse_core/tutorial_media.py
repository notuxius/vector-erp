"""Remove built-in onboarding video references after installation and upgrades."""

import frappe


def apply():
    changed = []
    for step in frappe.get_all("Onboarding Step", fields=["name", "intro_video_url", "video_url"]):
        if step.intro_video_url or step.video_url:
            frappe.db.set_value("Onboarding Step", step.name,
                                {"intro_video_url": "", "video_url": ""}, update_modified=False)
            frappe.clear_document_cache("Onboarding Step", step.name)
            changed.append(step.name)
    return {"cleared_onboarding_steps": changed}


def verify():
    steps = frappe.get_all("Onboarding Step", fields=["name", "intro_video_url", "video_url"])
    remaining = [step.name for step in steps if step.intro_video_url or step.video_url]
    assert not remaining, remaining
    return {"onboarding_steps_checked": len(steps), "remaining_video_references": len(remaining)}
