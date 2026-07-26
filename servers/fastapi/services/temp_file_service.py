import os
import uuid
from typing import Optional, Union

from fastapi import HTTPException

from utils.get_env import get_temp_directory_env


class TempFileService:

    def __init__(self, base_dir: Optional[str] = None):
        configured_base_dir = base_dir or get_temp_directory_env() or "/tmp/presenton"
        self.base_dir = os.path.realpath(os.path.abspath(configured_base_dir))
        self.cleanup_base_dir()
        os.makedirs(self.base_dir, exist_ok=True)

    def resolve_temp_path(self, file_path: str, *, must_exist: bool = False) -> str:
        if not isinstance(file_path, str) or not file_path.strip():
            raise HTTPException(status_code=400, detail="Invalid temp file path")

        candidate = file_path
        if not os.path.isabs(candidate):
            candidate = os.path.join(self.base_dir, candidate)
        resolved_path = os.path.realpath(os.path.abspath(candidate))

        try:
            is_within_base_dir = (
                os.path.commonpath((self.base_dir, resolved_path)) == self.base_dir
            )
        except ValueError:
            # Windows paths on different drives do not have a common path.
            is_within_base_dir = False

        if not is_within_base_dir:
            raise HTTPException(
                status_code=400,
                detail="Temp file path must stay within the temp directory",
            )
        if must_exist and not os.path.exists(resolved_path):
            raise HTTPException(status_code=404, detail="Temp file not found")
        return resolved_path

    def resolve_existing_temp_paths(
        self,
        file_paths: Optional[list[str]],
    ) -> list[str]:
        return [
            self.resolve_temp_path(file_path, must_exist=True)
            for file_path in (file_paths or [])
        ]

    @staticmethod
    def _safe_name(name: str) -> str:
        if not isinstance(name, str):
            raise HTTPException(status_code=400, detail="Invalid temp file name")
        safe_name = os.path.basename(name.replace("\\", "/")).strip()
        if not safe_name or safe_name in {".", ".."}:
            raise HTTPException(status_code=400, detail="Invalid temp file name")
        return safe_name

    def create_dir_in_dir(self, base_dir: str, dir_name: Optional[str] = None) -> str:
        resolved_base_dir = self.resolve_temp_path(base_dir)
        safe_dir_name = self._safe_name(dir_name) if dir_name else str(uuid.uuid4())
        temp_dir = self.resolve_temp_path(
            os.path.join(resolved_base_dir, safe_dir_name)
        )
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    def create_temp_dir(self, dir_name: Optional[str] = None) -> str:
        return self.create_dir_in_dir(self.base_dir, dir_name)

    def create_temp_file_path(
        self, file_path: str, dir_path: Optional[str] = None
    ) -> str:
        if dir_path is None:
            dir_path = self.base_dir

        resolved_dir_path = self.resolve_temp_path(dir_path)
        safe_file_name = self._safe_name(file_path)
        full_path = self.resolve_temp_path(
            os.path.join(resolved_dir_path, safe_file_name)
        )

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        return full_path

    def create_temp_file(
        self, file_path: str, content: Union[bytes, str], dir_path: Optional[str] = None
    ) -> str:
        file_path = self.create_temp_file_path(file_path, dir_path)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(file_path, mode) as f:
            f.write(content)

        return file_path

    def read_temp_file(self, file_path: str, binary: bool = True) -> Union[bytes, str]:
        file_path = self.resolve_temp_path(file_path, must_exist=True)
        mode = "rb" if binary else "r"
        with open(file_path, mode) as f:
            return f.read()

    def cleanup_temp_file(self, file_path: str):
        file_path = self.resolve_temp_path(file_path)
        if os.path.exists(file_path):
            os.remove(file_path)

    def delete_dir_files(self, dir_path: str):
        dir_path = self.resolve_temp_path(dir_path)
        if not os.path.exists(dir_path):
            return
        for root, dirs, files in os.walk(dir_path, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                child_dir = os.path.join(root, name)
                if os.path.islink(child_dir):
                    os.unlink(child_dir)
                else:
                    os.rmdir(child_dir)

    def cleanup_temp_dir(self, dir_path: str):
        dir_path = self.resolve_temp_path(dir_path)
        if os.path.exists(dir_path):
            self.delete_dir_files(dir_path)
            os.rmdir(dir_path)

    def cleanup_base_dir(self):
        self.cleanup_temp_dir(self.base_dir)


TEMP_FILE_SERVICE = TempFileService()
