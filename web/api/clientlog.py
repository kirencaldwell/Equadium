"""
Where the app sends diagnostics instead of showing them to players: things like sign-in configuration problems,
server errors, or failed notification deliveries. They land in the server's log (on Render: the service's Logs
tab), which only the person running the service can read. Anonymous, rate limited and size capped.
"""
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger("equadium_client")
router = APIRouter()

MAX_PER_MINUTE = 20          # per client address
_recent: dict = defaultdict(deque)


class ClientLog(BaseModel):
    kind: str
    detail: str = ""


def _allowed(who: str, now: float) -> bool:
    q = _recent[who]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= MAX_PER_MINUTE:
        return False
    q.append(now)
    if len(_recent) > 5000:                      # forget addresses that went quiet
        for k in [k for k, v in _recent.items() if not v or now - v[-1] > 60]:
            del _recent[k]
    return True


@router.post("/client-log")
def client_log(body: ClientLog, request: Request):
    who = request.client.host if request.client else "?"
    if _allowed(who, time.time()):
        clean = lambda s, n: "".join(ch for ch in s[:n] if ch.isprintable())
        logger.warning("client issue: %s | %s | ua=%s", clean(body.kind, 60), clean(body.detail, 500),
                       clean(request.headers.get("user-agent", ""), 120))
    return {"ok": True}
