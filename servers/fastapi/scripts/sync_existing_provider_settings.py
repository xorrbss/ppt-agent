"""Import an explicitly selected local provider config without logging secrets."""

from services.provider_settings import sync_legacy_file_to_provider_settings


def main() -> int:
    committed = sync_legacy_file_to_provider_settings(managed_updates=None)
    return 0 if committed is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
