"""Generate an Ekrano rašiklis license key after a Stripe payment.

Usage:
    python make_license.py client@email.com
"""

import sys

from license_gate import make_license_key


def main():
    if len(sys.argv) < 2:
        print("Naudojimas: python make_license.py klientas@pastas.lt")
        return 1
    email = sys.argv[1]
    key = make_license_key(email)
    print(email.strip().lower())
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
