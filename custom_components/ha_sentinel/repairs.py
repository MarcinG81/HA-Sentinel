"""Repairs issues for Sentinel's own problems only (spec 2.1, 19.2-19.3).

Sentinel never creates, edits or deletes issues of other domains.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

STORAGE_ISSUES = ("unsupported_schema", "database_corrupted", "migration_failed",
                  "insufficient_space_for_migration")


@callback
def async_update_storage_issue(hass: HomeAssistant, reason: str | None, file_name: str) -> None:
    for issue in STORAGE_ISSUES:
        if issue != reason:
            ir.async_delete_issue(hass, DOMAIN, issue)
    if reason in STORAGE_ISSUES:
        ir.async_create_issue(
            hass, DOMAIN, reason, is_fixable=False, is_persistent=False,
            severity=ir.IssueSeverity.ERROR, translation_key=reason,
            translation_placeholders={"file": file_name},
            learn_more_url=None,
        )
