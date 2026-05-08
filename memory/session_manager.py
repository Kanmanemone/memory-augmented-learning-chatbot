"""
Session Manager — lifecycle and exit-command handling.

Responsibilities
----------------
1. Create / track a conversation session (session_id, state).
2. Parse incoming user text for explicit exit commands
   ('exit', 'quit', '/end', ':q', 'bye') and fire a registered
   on_session_end callback when one is detected.
3. Expose a thin event-handler interface so the chatbot loop
   can attach LTM-consolidation logic without tight coupling.

Usage example
-------------
    from memory.session_manager import SessionManager

    def on_end(session_id: str) -> None:
        print(f"Session {session_id} ended – consolidate to LTM here")

    mgr = SessionManager(on_session_end=on_end)
    session_id = mgr.start_session()

    while True:
        user_input = input("You: ")
        if mgr.handle_input(user_input):
            break   # exit command was detected; session already ended
        # … process normally …
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Exit-command vocabulary
# ---------------------------------------------------------------------------

#: Set of normalised strings that signal the user wants to end the session.
EXIT_COMMANDS: frozenset[str] = frozenset(
    {"exit", "quit", "/end", ":q", "bye", "goodbye", "종료", "끝"}
)


class SessionEndEventType(str, Enum):
    """Supported reasons a conversation session can be considered ended."""

    EXPLICIT_EXIT = "explicit_exit"
    MANUAL_END = "manual_end"
    MAX_TURNS_REACHED = "max_turns_reached"
    INACTIVITY_TIMEOUT = "inactivity_timeout"


@dataclass(frozen=True)
class SessionEndCriteria:
    """Thresholds used to decide whether a session should end automatically."""

    max_turns: Optional[int] = None
    inactivity_timeout_seconds: Optional[int] = None


@dataclass(frozen=True)
class SessionEndEvent:
    """Serializable metadata emitted when a session ends."""

    session_id: str
    event_type: SessionEndEventType
    reason: str
    trigger_text: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            object.__setattr__(
                self, "timestamp", datetime.now(timezone.utc).isoformat()
            )

    def to_dict(self) -> dict:
        """Return a SQLite/vector-metadata-friendly representation."""
        return {
            "session_id": self.session_id,
            "event_type": self.event_type.value,
            "reason": self.reason,
            "trigger_text": self.trigger_text,
            "timestamp": self.timestamp,
        }


def is_exit_command(text: str) -> bool:
    """
    Return True if *text* is (or starts with) a recognised exit command.

    Matching rules
    ~~~~~~~~~~~~~~
    * Case-insensitive
    * Leading/trailing whitespace is stripped
    * A command may be the entire message OR the very first token on the line
      (e.g. "exit now", "quit please" still trigger)
    * Inline occurrences do NOT trigger (e.g. "I don't want to exit yet" → False)

    Parameters
    ----------
    text:
        Raw user input string.

    Returns
    -------
    bool
    """
    if not text or not text.strip():
        return False

    normalised = text.strip().lower()

    # Exact match
    if normalised in EXIT_COMMANDS:
        return True

    # First-token match: "exit <anything>", "/end session", etc.
    first_token = re.split(r"\s+", normalised, maxsplit=1)[0]
    return first_token in EXIT_COMMANDS


def should_end_session(
    text: str = "",
    turn_count: Optional[int] = None,
    inactive_seconds: Optional[int] = None,
    criteria: Optional[SessionEndCriteria] = None,
) -> Optional[SessionEndEventType]:
    """
    Return the session-end event type when a termination criterion is met.

    Criteria order is intentional: explicit user intent wins over automatic
    thresholds, then max-turn capping, then inactivity timeout.
    """
    if is_exit_command(text):
        return SessionEndEventType.EXPLICIT_EXIT

    active_criteria = criteria or SessionEndCriteria()

    if (
        active_criteria.max_turns is not None
        and turn_count is not None
        and turn_count >= active_criteria.max_turns
    ):
        return SessionEndEventType.MAX_TURNS_REACHED

    if (
        active_criteria.inactivity_timeout_seconds is not None
        and inactive_seconds is not None
        and inactive_seconds >= active_criteria.inactivity_timeout_seconds
    ):
        return SessionEndEventType.INACTIVITY_TIMEOUT

    return None


def _default_reason(event_type: SessionEndEventType) -> str:
    reasons = {
        SessionEndEventType.EXPLICIT_EXIT: "User entered an exit command.",
        SessionEndEventType.MANUAL_END: "Session ended manually.",
        SessionEndEventType.MAX_TURNS_REACHED: "Configured maximum turn count reached.",
        SessionEndEventType.INACTIVITY_TIMEOUT: "Configured inactivity timeout reached.",
    }
    return reasons[event_type]


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class SessionState:
    """Immutable-ish record describing a single conversation session."""

    __slots__ = ("session_id", "started_at", "ended_at", "is_active")

    def __init__(self, session_id: str) -> None:
        self.session_id: str = session_id
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self.ended_at: Optional[str] = None
        self.is_active: bool = True

    def mark_ended(self) -> None:
        self.ended_at = datetime.now(timezone.utc).isoformat()
        self.is_active = False

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SessionState(session_id={self.session_id!r}, "
            f"started_at={self.started_at!r}, is_active={self.is_active})"
        )


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Manage the lifecycle of a single-user conversation session.

    Parameters
    ----------
    on_session_end:
        Optional callback invoked with the ``session_id`` when an exit
        command is detected.  Typically wires in the LTM consolidation
        pipeline.
    session_id:
        If supplied, resume an existing session instead of creating a new
        one on ``start_session()``.

    Attributes
    ----------
    current_session : SessionState | None
        The currently active session, or None if no session has started.
    """

    def __init__(
        self,
        on_session_end: Optional[Callable[[str], None]] = None,
        on_session_end_event: Optional[Callable[[SessionEndEvent], None]] = None,
        session_id: Optional[str] = None,
        criteria: Optional[SessionEndCriteria] = None,
    ) -> None:
        self._on_session_end = on_session_end
        self._on_session_end_event = on_session_end_event
        self._initial_session_id = session_id
        self._criteria = criteria or SessionEndCriteria()
        self.current_session: Optional[SessionState] = None
        self.last_end_event: Optional[SessionEndEvent] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, session_id: Optional[str] = None) -> str:
        """
        Start a new session (or resume the one given at construction time).

        Returns
        -------
        str
            The session_id of the new session.

        Raises
        ------
        RuntimeError
            If a session is already active.
        """
        if self.current_session and self.current_session.is_active:
            raise RuntimeError(
                f"A session is already active: {self.current_session.session_id!r}. "
                "Call end_session() before starting a new one."
            )

        sid = session_id or self._initial_session_id or str(uuid.uuid4())
        self.current_session = SessionState(sid)
        return sid

    def end_session(
        self,
        event_type: SessionEndEventType = SessionEndEventType.MANUAL_END,
        reason: Optional[str] = None,
        trigger_text: Optional[str] = None,
    ) -> Optional[str]:
        """
        Explicitly end the current session and fire the ``on_session_end``
        callback if one is registered.

        Returns
        -------
        str | None
            The session_id of the session that was ended, or None if no
            session was active.
        """
        if not self.current_session or not self.current_session.is_active:
            return None

        session_id = self.current_session.session_id
        self.current_session.mark_ended()
        event = SessionEndEvent(
            session_id=session_id,
            event_type=event_type,
            reason=reason or _default_reason(event_type),
            trigger_text=trigger_text,
            timestamp=self.current_session.ended_at or "",
        )
        self.last_end_event = event

        if self._on_session_end is not None:
            self._on_session_end(session_id)
        if self._on_session_end_event is not None:
            self._on_session_end_event(event)

        return session_id

    def handle_input(
        self,
        text: str,
        turn_count: Optional[int] = None,
        inactive_seconds: Optional[int] = None,
    ) -> bool:
        """
        Parse a user input string and check for exit commands.

        If an exit command is detected:
        1. ``end_session()`` is called (which fires the callback).
        2. Returns ``True`` so the caller can break its input loop.

        If the input is NOT an exit command, returns ``False`` and the
        caller should process the message normally.

        Parameters
        ----------
        text:
            Raw user input.

        Returns
        -------
        bool
            True  → exit command detected; session has been ended.
            False → normal input; no action taken by this manager.
        """
        event_type = should_end_session(
            text=text,
            turn_count=turn_count,
            inactive_seconds=inactive_seconds,
            criteria=self._criteria,
        )
        if event_type is not None:
            return (
                self.end_session(event_type=event_type, trigger_text=text)
                is not None
            )
        return False

    def register_on_session_end(self, callback: Callable[[str], None]) -> None:
        """
        Register (or replace) the callback fired when a session ends.

        Parameters
        ----------
        callback:
            Callable that receives the ``session_id`` string.
        """
        self._on_session_end = callback

    def register_on_session_end_event(
        self, callback: Callable[[SessionEndEvent], None]
    ) -> None:
        """Register (or replace) the typed event callback fired at session end."""
        self._on_session_end_event = callback

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> Optional[str]:
        """Return the current session_id, or None if no session is active."""
        return self.current_session.session_id if self.current_session else None

    @property
    def is_active(self) -> bool:
        """True if a session is currently active."""
        return bool(self.current_session and self.current_session.is_active)
