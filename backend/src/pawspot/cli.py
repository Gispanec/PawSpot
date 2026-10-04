import argparse

from sqlalchemy.orm import Session

from pawspot.cleanup import cleanup_expired_drafts_and_photos
from pawspot.config import get_settings
from pawspot.db import get_engine
from pawspot.storage import LocalPhotoStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="PawSpot maintenance")
    parser.add_argument("command", choices=["cleanup"])
    args = parser.parse_args()
    if args.command == "cleanup":
        settings = get_settings()
        with Session(get_engine()) as session:
            count = cleanup_expired_drafts_and_photos(
                session, LocalPhotoStorage(settings.media_dir), settings
            )
        print(f"Removed {count} orphan photos")


if __name__ == "__main__":
    main()
