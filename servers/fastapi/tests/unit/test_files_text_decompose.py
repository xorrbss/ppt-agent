import asyncio
from pathlib import Path

from api.v1.ppt.endpoints import files as files_endpoint


def test_decompose_normalizes_markdown_to_utf8_and_preserves_body(
    tmp_path, monkeypatch
):
    source = tmp_path / "원본.markdown"
    source.write_bytes("# 제목\r\n\r\n- 내용<br>유지".encode("cp949"))
    monkeypatch.setattr(
        files_endpoint.TEMP_FILE_SERVICE,
        "base_dir",
        str(tmp_path),
    )

    response = asyncio.run(
        files_endpoint.decompose_files(
            file_paths=[str(source)],
            language="ko",
        )
    )

    assert len(response) == 1
    assert response[0].name == "원본.markdown"
    normalized_path = Path(response[0].file_path)
    assert normalized_path.suffix == ".txt"
    assert normalized_path.read_bytes().decode("utf-8") == "# 제목\n\n- 내용<br>유지"

    # The outline path loads the normalized file again through DocumentsLoader.
    outline_loader = files_endpoint.DocumentsLoader(
        file_paths=[response[0].file_path],
        presentation_language="ko",
    )
    asyncio.run(outline_loader.load_documents())
    assert outline_loader.documents == ["# 제목\n\n- 내용<br>유지"]
