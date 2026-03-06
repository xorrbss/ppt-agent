/**
 * LibreOffice download URLs for automated installation.
 * Update the version when upgrading to a newer LibreOffice release.
 * See https://www.libreoffice.org/download/download-libreoffice/
 */
export const LIBREOFFICE_VERSION = "24.8.7";

export const LIBREOFFICE_DOWNLOAD_URLS = {
  win64: `https://www.libreoffice.org/donate/dl/win-x86_64/${LIBREOFFICE_VERSION}/en-US/LibreOffice_${LIBREOFFICE_VERSION}_Win_x86-64.msi`,
  macX64: `https://www.libreoffice.org/donate/dl/mac-x86_64/${LIBREOFFICE_VERSION}/en-US/LibreOffice_${LIBREOFFICE_VERSION}_MacOS_x86-64.dmg`,
  macArm64: `https://www.libreoffice.org/donate/dl/mac-aarch64/${LIBREOFFICE_VERSION}/en-US/LibreOffice_${LIBREOFFICE_VERSION}_MacOS_aarch64.dmg`,
} as const;
