from datetime import date, datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')


def eastern_now() -> datetime:
    return datetime.now(EASTERN)


def eastern_today() -> date:
    return datetime.now(EASTERN).date()
