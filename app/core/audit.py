"""Structured audit log: one JSON line per security-relevant action.

Never log tokens, env values or request bodies here.
"""
import json
import logging

from app.core.security import Principal

_log = logging.getLogger("audit")


def audit(principal: Principal, action: str, namespace: str | None, name_or_id: str | None, outcome: str = "ok") -> None:
    _log.info(
        json.dumps(
            {
                "actor": principal.sub,
                "role": principal.role,
                "action": action,
                "namespace": namespace,
                "resource": name_or_id,
                "outcome": outcome,
            }
        )
    )
