"""
Prints a fresh VAPID key pair for web push. Run once:  python3 -m web.api.make_vapid_keys

Needs only the `cryptography` package (pip3 install cryptography, or pip3 install -r requirements.txt).
"""
import base64
import sys

try:
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:
    sys.exit("This needs the 'cryptography' package. Install it with:  pip3 install cryptography\n"
             "(or everything at once:  pip3 install -r requirements.txt), then run this again.")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_keys() -> tuple:
    """(public, private) as unpadded url-safe base64: the 65-byte uncompressed public point and the 32-byte secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(ser.Encoding.X962, ser.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return _b64(public), _b64(private)


if __name__ == "__main__":
    pub, priv = make_keys()
    print("Set these three environment variables on the API server (replace the email with your own):\n")
    print(f"VAPID_PUBLIC_KEY={pub}")
    print(f"VAPID_PRIVATE_KEY={priv}")
    print("VAPID_SUBJECT=mailto:you@example.com")
