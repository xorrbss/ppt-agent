import os
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional

from utils.runtime_limits import log_memory


class DocumentConversionError(Exception):
    """Actionable document conversion failure safe to expose to API clients.

    The optional metadata keeps legacy ``DocumentConversionError(message)``
    call sites working while allowing the upload endpoint to return a stable,
    file-scoped error contract.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "DOCUMENT_CONVERSION_FAILED",
        filename: Optional[str] = None,
        retryable: bool = False,
        public_message: Optional[str] = None,
    ):
        super().__init__(message)
        self.code = code
        self.filename = filename
        self.retryable = retryable
        self.public_message = public_message

    def to_detail(self) -> Dict[str, object]:
        detail: Dict[str, object] = {
            "code": self.code,
            # Exception text can contain subprocess stderr/stdout, commands, or
            # local paths. Only an explicitly reviewed public message crosses
            # the API boundary.
            "message": self.public_message
            or "Document conversion failed. Check the file and try again.",
            "retryable": self.retryable,
        }
        if self.filename:
            detail["filename"] = self.filename
        return detail


LOGGER = logging.getLogger(__name__)
_LOG_SNIPPET_LIMIT = 600


def _snippet(value: str, limit: int = _LOG_SNIPPET_LIMIT) -> str:
    text = (value or "").strip()
    if not text:
        return "<empty>"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _command_str(parts: list[str]) -> str:
    return " ".join(repr(part) for part in parts)


def _windows_hidden_subprocess_kwargs() -> Dict[str, object]:
    if os.name != "nt":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


class DocumentConversionService:
    def __init__(self):
        self.soffice_binary = self._resolve_soffice_binary()
        self.imagemagick_binary = self._resolve_imagemagick_binary()
        self._soffice_available: Optional[bool] = None

    def is_imagemagick_available(self) -> bool:
        """Whether a verified ImageMagick executable was found at startup."""
        return self.imagemagick_binary is not None

    def is_soffice_available(self) -> bool:
        """Whether the LibreOffice ``soffice`` binary can be found (cached).

        ``shutil.which`` validates a full ``SOFFICE_PATH`` and searches PATH for a
        bare name, without spawning a process. Absent on plain Windows installs of
        this fork, so callers fall back to native extraction.
        """
        if self._soffice_available is None:
            self._soffice_available = shutil.which(self.soffice_binary) is not None
        return self._soffice_available

    @staticmethod
    def _resolve_soffice_binary() -> str:
        configured = (os.getenv("SOFFICE_PATH") or "").strip()
        if configured:
            return configured
        return "soffice.exe" if os.name == "nt" else "soffice"

    @staticmethod
    def _can_execute(command: str, args: List[str]) -> bool:
        try:
            result = subprocess.run(
                [command, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                **_windows_hidden_subprocess_kwargs(),
            )
            return result.returncode == 0
        except Exception:
            return False

    def _resolve_imagemagick_binary(self) -> Optional[str]:
        configured = (os.getenv("IMAGEMAGICK_BINARY") or "").strip()
        if configured:
            resolved = shutil.which(configured)
            if resolved and self._can_execute(resolved, ["-version"]):
                return resolved
            LOGGER.warning(
                "[DocumentConversion] Configured ImageMagick executable is unavailable: %s",
                configured,
            )
            return None

        for candidate in ["magick", "convert"]:
            resolved = shutil.which(candidate)
            if resolved and self._can_execute(resolved, ["-version"]):
                return resolved

        return None

    def convert_office_to_pdf(
        self,
        file_path: str,
        output_dir: str,
        timeout_seconds: int = 180,
    ) -> str:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        existing_pdfs = {
            p.name for p in Path(output_dir).glob("*.pdf") if p.is_file()
        }

        try:
            command = [
                self.soffice_binary,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                output_dir,
                file_path,
            ]
            LOGGER.info(
                "[DocumentConversion] LibreOffice conversion start input=%s output_dir=%s",
                file_path,
                output_dir,
            )
            log_memory(LOGGER, "document_conversion.office.start", input=file_path)
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                **_windows_hidden_subprocess_kwargs(),
            )
            LOGGER.info(
                "[DocumentConversion] LibreOffice conversion complete input=%s",
                file_path,
            )
            log_memory(LOGGER, "document_conversion.office.finish", input=file_path)
        except subprocess.TimeoutExpired as exc:
            LOGGER.error(
                "[DocumentConversion] LibreOffice timed out command=%s",
                _command_str(exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)]),
            )
            raise DocumentConversionError(
                f"LibreOffice conversion timed out for {os.path.basename(file_path)}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or str(exc)
            LOGGER.error(
                "[DocumentConversion] LibreOffice failed code=%s command=%s stderr=%s stdout=%s",
                exc.returncode,
                _command_str(exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)]),
                _snippet(stderr),
                _snippet(stdout),
            )
            raise DocumentConversionError(
                f"LibreOffice conversion failed for {os.path.basename(file_path)}: {details} "
                f"(stderr={_snippet(stderr)}; stdout={_snippet(stdout)})"
            ) from exc
        except Exception as exc:
            LOGGER.exception("[DocumentConversion] LibreOffice conversion unexpected error")
            raise DocumentConversionError(
                f"LibreOffice conversion failed for {os.path.basename(file_path)}: {exc}"
            ) from exc

        expected_pdf = Path(output_dir) / f"{Path(file_path).stem}.pdf"
        if expected_pdf.is_file():
            return str(expected_pdf)

        generated_pdfs = [
            p
            for p in Path(output_dir).glob("*.pdf")
            if p.is_file() and p.name not in existing_pdfs
        ]
        if generated_pdfs:
            newest = max(generated_pdfs, key=lambda p: p.stat().st_mtime)
            return str(newest)

        raise DocumentConversionError(
            f"LibreOffice did not create a PDF for {os.path.basename(file_path)}"
        )

    def convert_image_to_png(
        self,
        file_path: str,
        output_dir: str,
        timeout_seconds: int = 120,
    ) -> str:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        output_path = Path(output_dir) / f"{Path(file_path).stem}_converted.png"

        if not self.is_imagemagick_available():
            filename = os.path.basename(file_path)
            raise DocumentConversionError(
                f"Image conversion is unavailable for '{filename}' because ImageMagick "
                "is not installed or IMAGEMAGICK_BINARY is invalid.",
                code="IMAGE_CONVERTER_UNAVAILABLE",
                filename=filename,
                public_message=(
                    "Image conversion is unavailable. Install ImageMagick or configure "
                    "IMAGEMAGICK_BINARY, then try again."
                ),
            )

        # Guarded above, so the optional value is a verified executable here.
        assert self.imagemagick_binary is not None
        command = [self.imagemagick_binary, file_path, str(output_path)]

        try:
            LOGGER.info(
                "[DocumentConversion] ImageMagick conversion start input=%s output=%s command=%s",
                file_path,
                output_path,
                _command_str(command),
            )
            log_memory(LOGGER, "document_conversion.image.start", input=file_path)
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                **_windows_hidden_subprocess_kwargs(),
            )
            LOGGER.info(
                "[DocumentConversion] ImageMagick conversion complete output=%s",
                output_path,
            )
            log_memory(LOGGER, "document_conversion.image.finish", input=file_path)
        except subprocess.TimeoutExpired as exc:
            LOGGER.error(
                "[DocumentConversion] ImageMagick timed out command=%s",
                _command_str(exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)]),
            )
            raise DocumentConversionError(
                f"ImageMagick conversion timed out for {os.path.basename(file_path)}",
                filename=os.path.basename(file_path),
                retryable=True,
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or str(exc)
            LOGGER.error(
                "[DocumentConversion] ImageMagick failed code=%s command=%s stderr=%s stdout=%s",
                exc.returncode,
                _command_str(exc.cmd if isinstance(exc.cmd, list) else [str(exc.cmd)]),
                _snippet(stderr),
                _snippet(stdout),
            )
            raise DocumentConversionError(
                f"ImageMagick conversion failed for {os.path.basename(file_path)}: {details} "
                f"(stderr={_snippet(stderr)}; stdout={_snippet(stdout)})",
                filename=os.path.basename(file_path),
            ) from exc
        except Exception as exc:
            LOGGER.exception("[DocumentConversion] ImageMagick conversion unexpected error")
            raise DocumentConversionError(
                f"ImageMagick conversion failed for {os.path.basename(file_path)}: {exc}",
                filename=os.path.basename(file_path),
            ) from exc

        if not output_path.is_file():
            raise DocumentConversionError(
                f"ImageMagick did not create a PNG for {os.path.basename(file_path)}",
                filename=os.path.basename(file_path),
            )

        return str(output_path)
