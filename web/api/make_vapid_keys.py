"""Prints a fresh VAPID key pair for web push. Run once: python -m web.api.make_vapid_keys"""
import base64

from cryptography.hazmat.primitives import serialization as ser
from py_vapid import Vapid


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


if __name__ == "__main__":
    v = Vapid()
    v.generate_keys()
    pub = v.public_key.public_bytes(ser.Encoding.X962, ser.PublicFormat.UncompressedPoint)
    priv = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    print(f"VAPID_PUBLIC_KEY={_b64(pub)}")
    print(f"VAPID_PRIVATE_KEY={_b64(priv)}")
    print("VAPID_SUBJECT=mailto:you@example.com   # replace with your email")
