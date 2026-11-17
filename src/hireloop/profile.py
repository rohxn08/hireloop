"""profile.md: your background and standard answers, read once per session.

Answers to questions the profile didn't cover are appended under
"## Saved answers", so each new question only has to be asked once.
"""

from datetime import date

from . import config

SAVED = "## Saved answers"
MISSING = {"error": "No profile.md yet.",
           "next": "Ask the user to copy profile.example.md to profile.md in the project folder and fill it in."}


def read() -> dict:
    p = config.PROFILE_PATH
    if not p.exists():
        return {**MISSING, "path": str(p)}
    return {"path": str(p), "profile": p.read_text().strip()}


def save_answer(question: str, answer: str) -> dict:
    p = config.PROFILE_PATH
    if not p.exists():
        return {**MISSING, "path": str(p)}
    text = p.read_text()
    if SAVED not in text:
        text = text.rstrip() + f"\n\n{SAVED}\n"
    entry = f"- **{question.strip()}** ({date.today().isoformat()}): {answer.strip()}\n"
    p.write_text(text.rstrip("\n") + "\n" + entry)
    return {"saved": True, "path": str(p)}
