"""Settings, read from environment variables and an optional .env file.

Every setting is HIRELOOP_<NAME>. The older WELLFOUND_<NAME> spelling still works,
so an existing .env keeps working after the rename.
"""

import os
from pathlib import Path

# Everything lives inside the project folder, and private files are gitignored.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from .env (gitignored). Real env vars win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(PROJECT_ROOT / ".env")


def env(name: str, default: str = "") -> str:
    """HIRELOOP_<name>, else the old WELLFOUND_<name>, else default."""
    for prefix in ("HIRELOOP_", "WELLFOUND_"):
        if (v := os.environ.get(prefix + name)) not in (None, ""):
            return v
    return default


# ./data by default. Holds your logged-in browser profile (treat it like a
# password; one profile holds the sessions for every site) and the application log.
DATA_DIR = Path(os.environ.get("HIRELOOP_DATA") or os.environ.get("WELLFOUND_MCP_DATA") or PROJECT_ROOT / "data")
PROFILE_DIR = DATA_DIR / "browser-profile"
DB_PATH = DATA_DIR / "applications.db"

# Visible browser by default: bot checks are less likely to trip, and you
# can watch what it does. Set HIRELOOP_HEADLESS=1 to hide it.
HEADLESS = env("HEADLESS", "0") == "1"

# Seconds to wait between page loads, picked at random in this range.
MIN_DELAY = float(env("MIN_DELAY", "3"))
MAX_DELAY = float(env("MAX_DELAY", "7"))

# Daily caps, per site. Each ramps up: it starts at START, rises by 1 for every
# past day you applied on that site without being blocked, and stops at MAX.
# Being blocked (Wellfound's limit on active applications) drops it back to START.
DAILY_START = int(env("DAILY_START", "5"))
DAILY_MAX = int(env("DAILY_LIMIT", "15"))
INDEED_DAILY_START = int(env("INDEED_DAILY_START", "10"))
INDEED_DAILY_MAX = int(env("INDEED_DAILY_LIMIT", "30"))

# Which Indeed country site to use (search results and job ids are per country).
INDEED_URL = env("INDEED_URL", "https://in.indeed.com").rstrip("/")

# Your background and standard answers, used to fill application forms.
# Copy profile.example.md to profile.md and fill it in. Gitignored, because it
# holds personal details.
PROFILE_PATH = Path(env("PROFILE") or PROJECT_ROOT / "profile.md")

# Email alert after each submitted application. Gmail needs an app password
# (Google Account > Security > App passwords), not your normal password.
# Put these in .env; it's gitignored. Alerts are off until both are set.
EMAIL_TO = env("EMAIL_TO")
SMTP_USER = env("SMTP_USER") or EMAIL_TO
SMTP_PASSWORD = env("SMTP_PASSWORD").replace(" ", "")
SMTP_HOST = env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(env("SMTP_PORT", "465"))

# Automatic applying. When on, Claude may submit without asking, but only for
# jobs that pass the fit rules and answers that pass fact_check; everything
# else is held and emailed to you. Off unless HIRELOOP_AUTO_APPLY=1.
AUTO_APPLY = env("AUTO_APPLY", "0") == "1"
# Indeed needs its own yes on top of AUTO_APPLY: until then Claude asks before each Indeed send.
INDEED_AUTO_APPLY = AUTO_APPLY and env("INDEED_AUTO_APPLY", "0") == "1"
MAX_YEARS = float(env("MAX_YEARS", "3"))
MIN_SALARY_LPA = float(env("MIN_SALARY_LPA", "4"))

# Wellfound only, off by default. When on (=1), send one application at a time: wait until
# the previous one has left Wellfound's 5-minute "Scheduled" (undo) window, give or take
# SLACK seconds. One tool call waits at most MAX_WAIT seconds, then asks to retry.
WAIT_FOR_SCHEDULED = env("WAIT_FOR_SCHEDULED", "0") == "1"
SCHEDULED_SLACK_S = int(env("SCHEDULED_SLACK_S", "15"))
SCHEDULED_MAX_WAIT_S = int(env("SCHEDULED_MAX_WAIT_S", "330"))

DASHBOARD_PORT = int(env("DASHBOARD_PORT", "8765"))
