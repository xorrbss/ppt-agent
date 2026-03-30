import os
import subprocess
from pathlib import Path
from typing import Dict, List


class DocumentConversionError(Exception):
    pass


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
                timeout=10,
                check=False,
                **_windows_hidden_subprocess_kwargs(),
            )
            return result.returncode == 0
        except Exception:
            return False

    def _resolve_imagemagick_binary(self) -> str:
        configured = (os.getenv("IMAGEMAGICK_BINARY") or "").strip()
        if configured:
            return configured

        for candidate in ["magick", "convert"]:
            if self._can_execute(candidate, ["-version"]):
                return candidate

        return "magick" if os.name == "nt" else "convert"

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
            subprocess.run(
                [
                    self.soffice_binary,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    output_dir,
                    file_path,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                **_windows_hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentConversionError(
                f"LibreOffice conversion timed out for {os.path.basename(file_path)}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or str(exc)
            raise DocumentConversionError(
                f"LibreOffice conversion failed for {os.path.basename(file_path)}: {details}"
            ) from exc
        except Exception as exc:
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

        command = [self.imagemagick_binary, file_path, str(output_path)]

        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                **_windows_hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentConversionError(
                f"ImageMagick conversion timed out for {os.path.basename(file_path)}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or str(exc)
            raise DocumentConversionError(
                f"ImageMagick conversion failed for {os.path.basename(file_path)}: {details}"
            ) from exc
        except Exception as exc:
            raise DocumentConversionError(
                f"ImageMagick conversion failed for {os.path.basename(file_path)}: {exc}"
            ) from exc

        if not output_path.is_file():
            raise DocumentConversionError(
                f"ImageMagick did not create a PNG for {os.path.basename(file_path)}"
            )

        return str(output_path)
