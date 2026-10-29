"""Errors a non-technical user can act on."""


class UserError(Exception):
    """A problem the user can fix. `hint` is the next thing to do."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        text = self.message
        if self.hint:
            text += "\n" + self.hint
        return text
