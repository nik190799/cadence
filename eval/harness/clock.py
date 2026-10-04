"""The logical clock: every tool gets --now, every commit a fixed date.

Ticket k of epoch E in a repo with n tickets runs in slot
s = (E - 1) * n + k, which starts at T = epoch + (s - 1) * slot_hours * 3600.
Each step of the ticket has a fixed offset in minutes from T, so the same
ticket happens at the same logical time in every arm and trial, and the
settle window the harvest needs (10 minutes between merge and harvest) is
built in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Minutes from the slot start (the contract's table). Pairs are (start, end).
OFFSETS: dict[str, tuple[int, int]] = {
    "spec": (0, 5),
    "spec2": (5, 10),
    "approve": (15, 15),
    "build": (20, 60),
    "gate": (65, 65),
    "observe": (65, 65),
    "retry": (70, 110),
    "publish": (115, 115),
    "ledger": (120, 120),
    "merge": (140, 140),
    "harvest": (150, 150),
    "learn-record": (155, 155),
    "retro-plan": (160, 160),
    "retro-publish": (170, 170),
}


@dataclass(frozen=True)
class Clock:
    epoch: int
    slot_hours: int

    def slot(self, epoch_no: int, n_tickets: int, k: int) -> int:
        if epoch_no not in (1, 2) or not 1 <= k <= n_tickets:
            raise ValueError(f"bad slot: epoch {epoch_no}, ticket {k} of {n_tickets}")
        return (epoch_no - 1) * n_tickets + k

    def start(self, epoch_no: int, n_tickets: int, k: int) -> int:
        s = self.slot(epoch_no, n_tickets, k)
        return self.epoch + (s - 1) * self.slot_hours * 3600

    def at(self, epoch_no: int, n_tickets: int, k: int, step: str, which: int = 0) -> int:
        """Epoch seconds of ``step`` (its start, or its end with which=1)."""
        return self.start(epoch_no, n_tickets, k) + OFFSETS[step][which] * 60

    def reset_time(self, n_tickets: int) -> int:
        """The E2 reset: one minute before E2's first slot."""
        return self.start(2, n_tickets, 1) - 60

    def end_of_run(self, n_tickets: int, epochs: int) -> int:
        return self.start(epochs, n_tickets, n_tickets) + self.slot_hours * 3600


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_date(epoch: int) -> str:
    """For GIT_AUTHOR_DATE / GIT_COMMITTER_DATE."""
    return f"@{int(epoch)} +0000"


def day(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")
