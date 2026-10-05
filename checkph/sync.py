"""Периодическая синхронизация CSV реестра с портала Минцифры.

Проверяет листинг ``/downloads/``, при новой публикации скачивает четыре файла,
валидирует, импортирует через ``importer.import_snapshot`` и сохраняет снимок
как ``inCSV/archive/YYYYMMDD.zip``.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import logging
import re
import shutil
import sqlite3
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .csv_reader import (
    SnapshotError,
    pack_snapshot_zip,
    read_snapshot,
    remove_dir,
    snapshot_files,
    snapshot_sha256,
)
from .importer import ARCHIVE_DIR, import_snapshot

log = logging.getLogger(__name__)

BASE_URL = "https://opendata.digital.gov.ru/downloads"
INDEX_URL = f"{BASE_URL}/"
CSV_FILES = ("ABC-3xx.csv", "ABC-4xx.csv", "ABC-8xx.csv", "DEF-9xx.csv")
INCSV_DIR = Path("inCSV")
CONTENT_CHECK_DAYS = 7
USER_AGENT = (
    "Mozilla/5.0 (compatible; CheckPhNumberBot/1.0; +https://t.me/ProverkaNomeraTelefonaBot)"
)

_INDEX_LINE_RE = re.compile(
    r'<a href="(?P<name>[^"]+\.csv)">[^<]*</a>\s+'
    r"(?P<mtime>\d{2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2})\s+\S+",
    re.IGNORECASE,
)
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Как getcsv.sh: портал иногда отдаёт цепочку с проблемами проверки.
_SSL_CONTEXT = ssl._create_unverified_context()


@dataclass(frozen=True, slots=True)
class RemoteFile:
    name: str
    remote_mtime: str  # как в индексе: "05-Oct-2026 00:10"

    @property
    def published_at(self) -> datetime:
        return parse_index_mtime(self.remote_mtime)


@dataclass(slots=True)
class SyncResult:
    action: str  # unchanged | republished | imported | rejected | compressed
    detail: str = ""

    def summary(self) -> str:
        return f"{self.action}: {self.detail}" if self.detail else self.action


def parse_index_mtime(text: str) -> datetime:
    """``05-Oct-2026 00:10`` → datetime (naive, дата публикации на сервере)."""
    parts = text.replace("  ", " ").strip().split()
    if len(parts) != 2:
        raise ValueError(f"Неожиданный формат даты индекса: {text!r}")
    date_s, time_s = parts
    day_s, mon_s, year_s = date_s.split("-")
    mon = _MONTHS.get(mon_s[:3].title())
    if mon is None:
        raise ValueError(f"Неизвестный месяц в дате индекса: {text!r}")
    hour_s, minute_s = time_s.split(":")
    return datetime(int(year_s), mon, int(day_s), int(hour_s), int(minute_s))


def parse_downloads_index(html: str) -> dict[str, RemoteFile]:
    found: dict[str, RemoteFile] = {}
    for m in _INDEX_LINE_RE.finditer(html):
        name = m.group("name")
        if name in CSV_FILES:
            found[name] = RemoteFile(name=name, remote_mtime=m.group("mtime"))
    missing = [n for n in CSV_FILES if n not in found]
    if missing:
        raise SnapshotError(f"В индексе нет файлов: {', '.join(missing)}")
    return found


def _urlopen(url: str, *, timeout: float = 120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    kwargs: dict = {"timeout": timeout}
    if url.lower().startswith("https://"):
        kwargs["context"] = _SSL_CONTEXT
    return urllib.request.urlopen(req, **kwargs)


def fetch_index(url: str = INDEX_URL) -> dict[str, RemoteFile]:
    with _urlopen(url, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return parse_downloads_index(html)


def download_file(url: str, dest: Path) -> int:
    """Скачивает файл; возвращает число записанных байт."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    try:
        with _urlopen(url) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        size = tmp.stat().st_size
        tmp.replace(dest)
        return size
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_sync_state(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = con.execute("SELECT * FROM sync_state").fetchall()
    return {r["filename"]: r for r in rows}


def _upsert_sync_state(
    con: sqlite3.Connection,
    *,
    filename: str,
    remote_mtime: str,
    size_bytes: int | None,
    content_sha256: str | None,
    content_checked_at: str,
) -> None:
    con.execute(
        """
        INSERT INTO sync_state (filename, remote_mtime, size_bytes, content_sha256, content_checked_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (filename) DO UPDATE SET
            remote_mtime = excluded.remote_mtime,
            size_bytes = excluded.size_bytes,
            content_sha256 = excluded.content_sha256,
            content_checked_at = excluded.content_checked_at
        """,
        (filename, remote_mtime, size_bytes, content_sha256, content_checked_at),
    )


def cursor_matches_index(con: sqlite3.Connection, listing: dict[str, RemoteFile]) -> bool:
    state = _load_sync_state(con)
    return all(
        name in state and state[name]["remote_mtime"] == listing[name].remote_mtime
        for name in CSV_FILES
    )


def content_check_is_fresh(con: sqlite3.Connection, *, max_age_days: int = CONTENT_CHECK_DAYS) -> bool:
    state = _load_sync_state(con)
    if len(state) < len(CSV_FILES):
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for name in CSV_FILES:
        raw = state[name]["content_checked_at"]
        try:
            checked = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if checked < cutoff:
            return False
    return True


def latest_batch_sha256(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        "SELECT files_sha256 FROM import_batch ORDER BY source_date DESC, id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else row["files_sha256"]


def choose_snapshot_names(
    con: sqlite3.Connection, published_at: datetime, archive_dir: Path
) -> tuple[str, str]:
    """Возвращает (stem архива, source_date)."""
    day = published_at.strftime("%Y%m%d")
    source_date = published_at.strftime("%Y-%m-%d")
    zip_path = archive_dir / f"{day}.zip"
    dir_path = archive_dir / day
    exists_day = zip_path.exists() or dir_path.exists()
    already = con.execute("SELECT 1 FROM import_batch WHERE source_date = ?", (source_date,)).fetchone()
    if already is not None or exists_day:
        stem = published_at.strftime("%Y%m%d-%H%M")
        source_date = published_at.strftime("%Y-%m-%dT%H:%M")
        return stem, source_date
    return day, source_date


def snapshot_published_at(listing: dict[str, RemoteFile]) -> datetime:
    times = {listing[n].published_at for n in CSV_FILES}
    if len(times) != 1:
        log.warning("Даты файлов в индексе различаются: %s", {n: listing[n].remote_mtime for n in CSV_FILES})
    return max(times)


def update_latest_copies(snapshot_dir: Path, latest_dir: Path | None = None) -> None:
    dest = latest_dir if latest_dir is not None else INCSV_DIR
    dest.mkdir(parents=True, exist_ok=True)
    for f in snapshot_files(snapshot_dir):
        shutil.copy2(f, dest / f.name)


def compress_existing_dirs(archive_dir: str | Path = ARCHIVE_DIR) -> list[SyncResult]:
    """Упаковывает каталоги ``YYYYMMDD`` в zip; каталог удаляет после сверки хеша."""
    root = Path(archive_dir)
    results: list[SyncResult] = []
    if not root.is_dir():
        return results
    dirs = sorted(
        p for p in root.iterdir()
        if p.is_dir() and re.fullmatch(r"\d{8}(?:-\d{4})?", p.name)
    )
    for d in dirs:
        zip_path = root / f"{d.name}.zip"
        dir_digest = snapshot_sha256(d)
        if zip_path.exists():
            zip_digest = snapshot_sha256(zip_path)
            if zip_digest != dir_digest:
                raise SnapshotError(
                    f"Уже есть {zip_path.name} с другим хешем, чем каталог {d.name}; "
                    "каталог не удаляю"
                )
            remove_dir(d)
            results.append(SyncResult("compressed", f"{d.name}: zip уже был, каталог удалён"))
            log.info(results[-1].summary())
            continue
        pack_snapshot_zip(d, zip_path)
        remove_dir(d)
        results.append(SyncResult("compressed", f"{d.name} → {zip_path.name}"))
        log.info(results[-1].summary())
    return results


def _download_snapshot(
    listing: dict[str, RemoteFile], incoming: Path, *, index_url: str = INDEX_URL, base_url: str = BASE_URL
) -> dict[str, RemoteFile]:
    """Скачивает четыре CSV; при смене индекса во время загрузки повторяет один раз."""
    incoming.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        for name in CSV_FILES:
            url = f"{base_url.rstrip('/')}/{name}"
            dest = incoming / name
            size = download_file(url, dest)
            log.info("скачан %s (%d байт)", name, size)
        listing_after = fetch_index(index_url) if index_url else listing
        if all(listing_after[n].remote_mtime == listing[n].remote_mtime for n in CSV_FILES):
            return listing_after
        log.warning("индекс изменился во время загрузки, повтор")
        listing = listing_after
        for name in CSV_FILES:
            (incoming / name).unlink(missing_ok=True)
    raise SnapshotError("Индекс менялся при повторной загрузке; снимок не согласован")


def _reject_incoming(incoming: Path, archive_dir: Path, reason: str) -> Path:
    rejected_dir = archive_dir / "_rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = rejected_dir / f"{stamp}.zip"
    pack_snapshot_zip(incoming, zip_path)
    shutil.rmtree(incoming, ignore_errors=True)
    log.error("отклонён снимок %s: %s", zip_path, reason)
    return zip_path


def _write_cursor(
    con: sqlite3.Connection,
    listing: dict[str, RemoteFile],
    incoming: Path,
    *,
    checked_at: str | None = None,
) -> None:
    checked_at = checked_at or _utc_now_iso()
    with db.transaction(con):
        for name in CSV_FILES:
            path = incoming / name
            _upsert_sync_state(
                con,
                filename=name,
                remote_mtime=listing[name].remote_mtime,
                size_bytes=path.stat().st_size,
                content_sha256=file_sha256(path),
                content_checked_at=checked_at,
            )


def sync_once(
    con: sqlite3.Connection,
    *,
    archive_dir: str | Path = ARCHIVE_DIR,
    index_url: str = INDEX_URL,
    base_url: str = BASE_URL,
    force: bool = False,
) -> SyncResult:
    """Один проход синхронизации. Без сетевых вызовов при неизменном курсоре (если не force)."""
    archive = Path(archive_dir)
    archive.mkdir(parents=True, exist_ok=True)
    listing = fetch_index(index_url)

    if not force and cursor_matches_index(con, listing) and content_check_is_fresh(con):
        return SyncResult("unchanged", "даты индекса совпадают, содержимое свежее")

    incoming = archive / ".incoming"
    if incoming.exists():
        shutil.rmtree(incoming)
    incoming.mkdir(parents=True)

    zip_path: Path | None = None
    imported = False
    try:
        listing = _download_snapshot(listing, incoming, index_url=index_url, base_url=base_url)
        try:
            read_snapshot(incoming)
        except SnapshotError as exc:
            _reject_incoming(incoming, archive, str(exc))
            return SyncResult("rejected", str(exc))

        digest = snapshot_sha256(incoming)
        last = latest_batch_sha256(con)
        if last is not None and digest == last:
            _write_cursor(con, listing, incoming)
            shutil.rmtree(incoming, ignore_errors=True)
            return SyncResult("republished", "тот же хеш, batch не создан")

        published = snapshot_published_at(listing)
        stem, source_date = choose_snapshot_names(con, published, archive)
        zip_path = archive / f"{stem}.zip"
        if zip_path.exists():
            raise SnapshotError(f"Целевой архив уже существует: {zip_path}")

        result = import_snapshot(con, incoming, source_date)
        if result.skipped:
            raise SnapshotError(f"import_snapshot пропустил дату {source_date}, хотя хеш новый")
        imported = True

        pack_snapshot_zip(incoming, zip_path)
        update_latest_copies(incoming)
        _write_cursor(con, listing, incoming)
        shutil.rmtree(incoming, ignore_errors=True)
        return SyncResult("imported", f"{result.summary()}; архив={zip_path.name}")
    except Exception:
        if incoming.exists() and any(incoming.glob("*.csv")):
            try:
                if imported and zip_path is not None and not zip_path.exists():
                    pack_snapshot_zip(incoming, zip_path)
                    shutil.rmtree(incoming, ignore_errors=True)
                else:
                    _reject_incoming(incoming, archive, "ошибка синхронизации")
            except Exception:
                shutil.rmtree(incoming, ignore_errors=True)
        else:
            shutil.rmtree(incoming, ignore_errors=True)
        raise


class ArchiveLock:
    """Эксклюзивная блокировка каталога архива через flock."""

    def __init__(self, archive_dir: Path) -> None:
        self.path = archive_dir / ".sync.lock"
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fh.close()
            self._fh = None
            raise RuntimeError(f"Синхронизация уже запущена (lock {self.path})") from exc
        return self

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синхронизация CSV реестра с портала Минцифры")
    parser.add_argument("--db", default=db.DEFAULT_DB_PATH, help="путь к базе")
    parser.add_argument("--archive", default=str(ARCHIVE_DIR), help="каталог архива снимков")
    parser.add_argument("--index-url", default=INDEX_URL, help="URL индекса downloads/")
    parser.add_argument("--base-url", default=BASE_URL, help="базовый URL файлов CSV")
    parser.add_argument("--force", action="store_true", help="скачать даже при совпадении курсора")
    parser.add_argument(
        "--compress-existing",
        action="store_true",
        help="только упаковать каталоги YYYYMMDD в zip",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    archive = Path(args.archive)

    with ArchiveLock(archive):
        if args.compress_existing:
            for r in compress_existing_dirs(archive):
                print(r.summary())
            return 0

        con = db.connect(args.db)
        db.init_schema(con)
        try:
            # сначала сжать старые каталоги, чтобы backfill/архив были в одном формате
            for r in compress_existing_dirs(archive):
                log.info(r.summary())
            result = sync_once(
                con,
                archive_dir=archive,
                index_url=args.index_url,
                base_url=args.base_url,
                force=args.force,
            )
            print(result.summary())
            return 1 if result.action == "rejected" else 0
        finally:
            con.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SnapshotError, urllib.error.URLError, RuntimeError, ValueError) as exc:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        log.error("%s", exc)
        sys.exit(1)
