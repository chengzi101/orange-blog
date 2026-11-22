from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from werkzeug.utils import secure_filename


MAX_NOTE_MB = 30
MAX_NOTE_PAGES = 60
MAX_RENDER_SECONDS = 22


class StudyNoteError(ValueError):
    pass


def init_study_notes(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS study_notes (
            slug TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            topic TEXT NOT NULL,
            summary TEXT NOT NULL,
            pdf_filename TEXT NOT NULL,
            pdf_sha256 TEXT NOT NULL UNIQUE,
            page_count INTEGER NOT NULL CHECK (page_count > 0),
            page_sizes TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_study_notes_recent
        ON study_notes (created_at DESC, slug DESC)
    """)


def publish_study_note(
    db: sqlite3.Connection,
    storage: Path,
    stream: BinaryIO,
    filename: str,
    created_at: str,
    *,
    title: str = "",
    topic: str = "",
    summary: str = "",
    slug: str | None = None,
) -> tuple[str, bool]:
    pdf_filename = PureWindowsPath(filename).name
    if Path(pdf_filename).suffix.lower() != ".pdf" or len(pdf_filename) > 200:
        raise StudyNoteError("请选择文件名不超过 200 个字符的 PDF。")
    if not all(character.isprintable() for character in pdf_filename):
        raise StudyNoteError("PDF 文件名含有无效字符，请改名后重试。")
    title = title.strip() or Path(pdf_filename).stem.strip()
    topic = topic.strip() or "未分类"
    summary = summary.strip()
    if not title or len(title) > 120 or len(topic) > 40 or len(summary) > 500:
        raise StudyNoteError("标题须为 1–120 个字符，主题最多 40 个字符，简介最多 500 个字符。")
    if slug is None:
        base = secure_filename(title).lower().replace("_", "-")[:48].strip("-") or "note"
        slug = f"{base}-{secrets.token_hex(8)}"

    storage.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".incoming-", dir=storage) as temporary:
        staged = Path(temporary) / "assets"
        staged.mkdir()
        digest = _save_pdf(stream, staged / "original.pdf")
        existing = db.execute("SELECT slug FROM study_notes WHERE pdf_sha256 = ?", (digest,)).fetchone()
        if existing:
            return existing[0], False

        pages = _render_pages(staged)
        target = storage / slug
        moved = False
        try:
            with db:
                db.execute("""
                    INSERT INTO study_notes
                        (slug, title, topic, summary, pdf_filename, pdf_sha256,
                         page_count, page_sizes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (slug, title, topic, summary, pdf_filename, digest,
                      len(pages), json.dumps(pages), created_at))
                staged.rename(target)
                moved = True
        except BaseException as error:
            if moved:
                shutil.rmtree(target)
            if isinstance(error, sqlite3.IntegrityError):
                existing = db.execute("SELECT slug FROM study_notes WHERE pdf_sha256 = ?", (digest,)).fetchone()
                if existing:
                    return existing[0], False
            raise
    return slug, True


def _save_pdf(stream: BinaryIO, target: Path) -> str:
    header = stream.read(5)
    if header != b"%PDF-":
        raise StudyNoteError("文件内容无效，请重新导出 PDF 后上传。")
    digest = hashlib.sha256(header)
    size = len(header)
    with target.open("wb") as output:
        output.write(header)
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_NOTE_MB * 1024 * 1024:
                raise StudyNoteError(f"PDF 大小超过 {MAX_NOTE_MB} MB。")
            digest.update(chunk)
            output.write(chunk)
    return digest.hexdigest()


def _render_pages(directory: Path) -> list[list[int]]:
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("study_note_render.py")), str(directory)],
            capture_output=True, check=False, timeout=MAX_RENDER_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise StudyNoteError("生成预览超时，请压缩 PDF 或分成几份后再试。") from error
    if result.returncode == 2:
        raise StudyNoteError("PDF 已损坏、加密或含无法读取的页面，请重新导出。")
    if result.returncode == 3:
        raise StudyNoteError(f"单份笔记最多支持 {MAX_NOTE_PAGES} 页。")
    result.check_returncode()
    return json.loads(result.stdout)
