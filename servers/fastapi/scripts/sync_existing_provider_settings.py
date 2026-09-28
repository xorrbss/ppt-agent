"""Import an explicitly selected local provider config without logging secrets."""

import sys

from services.provider_settings import sync_legacy_file_to_provider_settings
from services.provider_settings_sync import ProviderSettingsImportError


def main() -> int:
    try:
        committed = sync_legacy_file_to_provider_settings(managed_updates=None)
    except ProviderSettingsImportError as error:
        print(f"[sync_existing_provider_settings] {error}", file=sys.stderr)
        return 1
    return 0 if committed is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
