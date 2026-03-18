"""
Utility script for generating and verifying password hashes.

Usage:
    python hash.py <password>

This now uses Werkzeug's PBKDF2 (salted + iterated) instead of plain SHA-256.
Legacy SHA-256 hashes are shown for reference only.
"""

import sys
import hashlib
from werkzeug.security import generate_password_hash, check_password_hash


def main():
    pw = sys.argv[1] if len(sys.argv) > 1 else "John@12345"

    # --- New secure hash (use this for new users) ---
    secure_hash = generate_password_hash(pw)
    print(f"Password       : {pw}")
    print(f"Werkzeug hash  : {secure_hash}")
    print(f"Verify OK      : {check_password_hash(secure_hash, pw)}")
    print()

    # --- Legacy hashes (for reference / migration debugging) ---
    sha_utf8 = hashlib.sha256(pw.encode("utf-8")).hexdigest().upper()
    sha_utf16 = hashlib.sha256(pw.encode("utf-16le")).hexdigest().upper()
    print(f"Legacy UTF-8   : {sha_utf8}")
    print(f"Legacy UTF-16LE: {sha_utf16}")


if __name__ == "__main__":
    main()