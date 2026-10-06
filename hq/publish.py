"""Publishers: where an approved newsletter goes.

Today there is one, `OutboxPublisher`: it prepares ready-to-paste files and, once the Captain
approves, marks the issue ready. It never posts anything anywhere. The interface exists so a
publisher that posts for the Captain (Substack, a mail service) can be added later without
touching Client Relations' tools; such a publisher would still run only after his approval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from hq import outbox

if TYPE_CHECKING:
    from hq.engine.runtime import Office


class Publisher(Protocol):
    name: str

    def prepare(self, office: Office, issue_id: str) -> dict:
        """Build everything needed to publish the issue; returns its meta (with `files`)."""

    def publish(self, office: Office, issue_id: str) -> dict:
        """Called once the Captain approves. Returns the issue's meta."""


class OutboxPublisher:
    """Files in outbox/, pasted into Substack by the Captain himself."""

    name = "outbox"

    def prepare(self, office: Office, issue_id: str) -> dict:
        return outbox.render_issue(office, issue_id)

    def publish(self, office: Office, issue_id: str) -> dict:
        return outbox.set_status(
            office.outbox_dir, issue_id, "approved",
            next_step="Open issue.html, copy the article into Substack, upload header.png, and post "
                      "the two snippets from social.md.")


PUBLISHERS: dict[str, type] = {"outbox": OutboxPublisher}


def get_publisher(name: str | None = None) -> Publisher:
    name = name or outbox.settings()["publisher"]
    try:
        return PUBLISHERS[name]()
    except KeyError as e:
        raise ValueError(f"Unknown publisher {name!r} in office.yaml (client_relations.publisher). "
                         f"Available: {', '.join(PUBLISHERS)}.") from e


# ---- outreach mailers ---------------------------------------------------------------------------
class DraftMailer:
    """Phase A: nothing is sent. An approved email is marked ready, and the Captain sends it
    himself from contact@consciousinvestments.org with the text from the Outbox."""

    name = "draft"
    on_approval = "On approval it is marked ready for you to send yourself."

    def prepare(self, office: Office, email_id: str) -> dict:
        from hq import outreach

        return outreach.render(office, email_id)

    def deliver(self, office: Office, email_id: str) -> dict:
        import time

        from hq import outreach

        return outreach.set_status(
            office, email_id, "approved", approved_at=time.time(),
            next_step="Copy the To, Subject and body into your mail app and send it from "
                      f"{outreach.settings()['from_address']}.")


MAILERS: dict[str, type] = {"draft": DraftMailer}


def get_mailer(name: str | None = None):
    from hq import outreach

    name = name or outreach.settings()["mailer"]
    try:
        return MAILERS[name]()
    except KeyError as e:
        raise ValueError(f"Unknown mailer {name!r} in office.yaml (client_relations.outreach.mailer). "
                         f"Available: {', '.join(MAILERS)}.") from e
