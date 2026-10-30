from datetime import datetime

from app.domain.dates import MSK


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(MSK).replace(second=0, microsecond=0)
