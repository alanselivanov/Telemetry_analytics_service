import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main():
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")

    apps_dir = BASE_DIR / "apps"
    sys.path.insert(0, str(apps_dir))
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE", "telemetry_analytics_service.settings"
    )
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()