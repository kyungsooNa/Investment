"""웹 로그인용 PBKDF2 비밀번호 해시를 생성한다."""
from getpass import getpass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from view.web.security import hash_password


def main() -> None:
    password = getpass("Web password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
