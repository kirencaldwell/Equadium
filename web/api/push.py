"""
Web Push: tell a signed-in player when the other player has moved in an online game.

Setup (once): `python -m web.api.make_vapid_keys`, then set VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and
VAPID_SUBJECT (a mailto: address) on the API server. Without them the feature is simply off: the app hides
the bell and /push/config reports `enabled: false`.

A browser subscription belongs to a signed-in user (stored by the store, one doc per device). After every
accepted move in a room, the other seat's user gets a notification. Sending happens on a small thread pool
so a slow push service never delays the move itself.
"""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.api.auth import AuthUser, require_user
from web.api.store import get_store

logger = logging.getLogger("equadium_push")
router = APIRouter(prefix="/push", tags=["push"])

MAX_DEVICES = 10   # notifications go to at most this many of a player's devices

# The server POSTs to whatever endpoint a client registers, so only the browsers' own push services are allowed
# (otherwise a subscription could point the server at an internal address).
_PUSH_HOSTS = (
    "fcm.googleapis.com",                 # Chrome, Edge, Opera, Samsung
    "updates.push.services.mozilla.com",  # Firefox
    ".push.apple.com",                    # Safari / iOS home-screen apps
    ".notify.windows.com",                # legacy Edge
)


def vapid() -> Optional[dict]:
    pub, priv = os.getenv("VAPID_PUBLIC_KEY"), os.getenv("VAPID_PRIVATE_KEY")
    if not (pub and priv):
        return None
    return {"public_key": pub, "private_key": priv, "subject": os.getenv("VAPID_SUBJECT") or "mailto:admin@example.com"}


def enabled() -> bool:
    return vapid() is not None


def endpoint_allowed(endpoint: str) -> bool:
    try:
        u = urlparse(endpoint)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.port not in (None, 443) or u.username or u.password:
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in _PUSH_HOSTS)


class Keys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionModel(BaseModel):
    endpoint: str
    keys: Keys


class EndpointModel(BaseModel):
    endpoint: str


@router.get("/config")
def config():
    v = vapid()
    return {"enabled": v is not None, "public_key": v["public_key"] if v else None}


@router.post("/subscribe")
def subscribe(body: SubscriptionModel, user: AuthUser = Depends(require_user)):
    if not enabled():
        raise HTTPException(status_code=503, detail="Notifications are not set up on this server")
    if len(body.endpoint) > 2000 or not endpoint_allowed(body.endpoint):
        raise HTTPException(status_code=400, detail="Unsupported push service")
    try:
        get_store().save_push_subscription(user.id, {"endpoint": body.endpoint, "keys": body.keys.model_dump()})
    except Exception:
        logger.exception("Could not save push subscription")
        raise HTTPException(status_code=503, detail="Couldn't save that right now")
    return {"subscribed": True}


@router.post("/unsubscribe")
def unsubscribe(body: EndpointModel, user: AuthUser = Depends(require_user)):
    try:
        get_store().delete_push_subscription(user.id, body.endpoint)
    except Exception:
        logger.exception("Could not remove push subscription")
        raise HTTPException(status_code=503, detail="Couldn't save that right now")
    return {"subscribed": False}


# ── sending ─────────────────────────────────────────────────────────────────
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")


def _send_one(sub: dict, payload: str, v: dict) -> None:
    """Deliver one notification. Raises on failure (see _deliver). Replaced in tests."""
    from pywebpush import webpush
    webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]}, data=payload,
            vapid_private_key=v["private_key"], vapid_claims={"sub": v["subject"]}, ttl=60 * 60 * 12, timeout=8)


def _deliver(user_id: str, payload: dict) -> None:
    v = vapid()
    if not v:
        return
    store = get_store()
    try:
        subs = store.list_push_subscriptions(user_id)[:MAX_DEVICES]
    except Exception:
        logger.exception("Could not list push subscriptions")
        return
    body = json.dumps(payload)
    for sub in subs:
        try:
            _send_one(sub, body, v)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):          # the browser dropped this subscription: stop sending to it
                try:
                    store.delete_push_subscription(user_id, sub["endpoint"])
                except Exception:
                    logger.exception("Could not remove a dead push subscription")
            else:
                logger.warning("Push failed (%s): %s", status, exc)


def notify(user_id: str, payload: dict) -> None:
    """Fire and forget."""
    if enabled():
        _pool.submit(_deliver, user_id, payload)


def move_message(actor: str, action: str, score_delta: int, game_over: bool) -> str:
    if action == "forfeit":
        return f"{actor} forfeited. You win!"
    if game_over:
        return f"{actor} made the last move. The game is over."
    if action == "play":
        return f"{actor} played for {score_delta} points. Your turn!"
    if action == "swap":
        return f"{actor} swapped tiles. Your turn!"
    return f"{actor} passed. Your turn!"


def notify_room_move(room, actor_seat: str, record) -> None:
    """Tell the other seat's signed-in player about the move just made. Call with the room lock held."""
    if not enabled():
        return
    actor = room.labels.get(actor_seat, "Your opponent")
    msg = move_message(actor, record.action, record.score_delta, room.session.is_over)
    for seat, user_id in room.seat_users.items():
        if seat != actor_seat:
            notify(user_id, {"title": "Equadium", "body": msg, "tag": f"room-{room.code}",
                             "url": f"/#/room/{room.code}", "code": room.code})
