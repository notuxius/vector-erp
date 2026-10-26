"""Create local credentials once; never overwrite an existing environment."""
from pathlib import Path
import secrets


def main():
    target = Path(__file__).resolve().parents[1] / ".env"
    try:
        with target.open("x", encoding="utf-8") as output:
            output.write("HTTP_PORT=8080\n")
            output.write(f"DB_PASSWORD={secrets.token_hex(24)}\n")
            output.write(f"ADMIN_PASSWORD={secrets.token_hex(24)}\n")
        print("Created .env with local credentials. This file is excluded from Git.")
    except FileExistsError:
        print("Using existing .env; credentials unchanged.")


if __name__ == "__main__":
    main()
