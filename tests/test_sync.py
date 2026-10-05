from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from checkph import importer, sync
from checkph.csv_reader import pack_snapshot_zip, read_snapshot, snapshot_sha256
from conftest import HEADER, write_snapshot

R1 = '495;1000000;1009999;10000;ПАО "МГТС";г. Москва;Город Москва;7710016640'
R2 = '495;2000000;2000999;1000;ООО "Связь";г. Москва;Город Москва;7700000001'


def _write_portal_files(root: Path, rows_by_file: dict[str, list[str]] | None = None) -> None:
    rows_by_file = rows_by_file or {
        "ABC-3xx.csv": [],
        "ABC-4xx.csv": [R1, R2],
        "ABC-8xx.csv": [],
        "DEF-9xx.csv": [],
    }
    for name, rows in rows_by_file.items():
        (root / name).write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")


def _index_html(mtime: str = "05-Oct-2026 00:10", sizes: dict[str, str] | None = None) -> str:
    sizes = sizes or {n: "1K" for n in sync.CSV_FILES}
    lines = ['<html><head><title>Index of /downloads/</title></head><body><pre>']
    for name in sync.CSV_FILES:
        lines.append(f'<a href="{name}">{name}</a>                                        {mtime}     {sizes[name]}')
    lines.append("</pre></body></html>")
    return "\n".join(lines)


class _PortalHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: Path, index_html: str, **kwargs):
        self._index_html = index_html
        super().__init__(*args, directory=str(directory), **kwargs)

    def do_GET(self):
        if self.path in ("/", "/downloads/", "/downloads"):
            body = self._index_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = self.path.split("?", 1)[0]
        if path.startswith("/downloads/"):
            self.path = "/" + path[len("/downloads/"):]
        return super().do_GET()

    def log_message(self, format, *args):  # noqa: A003
        return


@pytest.fixture
def portal_ctrl(tmp_path: Path):
    """Локальный HTTP-портал с возможностью менять индекс и файлы."""
    files_dir = tmp_path / "portal"
    files_dir.mkdir()
    _write_portal_files(files_dir)
    state = {"index_html": _index_html()}

    class Handler(_PortalHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=files_dir, index_html=state["index_html"], **kwargs)

        def do_GET(self):
            self._index_html = state["index_html"]
            return super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"

    def set_mtime(mtime: str) -> None:
        state["index_html"] = _index_html(mtime=mtime)

    def set_rows(rows: list[str]) -> None:
        _write_portal_files(files_dir, {
            "ABC-3xx.csv": [],
            "ABC-4xx.csv": rows,
            "ABC-8xx.csv": [],
            "DEF-9xx.csv": [],
        })

    ctrl = {
        "base_url": base,
        "index_url": f"{base}/downloads/",
        "files_dir": files_dir,
        "set_mtime": set_mtime,
        "set_rows": set_rows,
        "archive": tmp_path / "archive",
    }
    ctrl["archive"].mkdir()
    yield ctrl
    server.shutdown()
    thread.join(timeout=2)


def test_parse_downloads_index():
    files = sync.parse_downloads_index(_index_html("05-Oct-2026 00:10"))
    assert files["ABC-3xx.csv"].remote_mtime == "05-Oct-2026 00:10"
    assert files["DEF-9xx.csv"].published_at.day == 5


def test_parse_index_mtime():
    assert sync.parse_index_mtime("05-Oct-2026 00:10").isoformat(sep=" ") == "2026-10-05 00:10:00"


def test_zip_and_dir_same_sha256(tmp_path: Path):
    d = write_snapshot(tmp_path, "20240101", [R1, R2])
    z = tmp_path / "20240101.zip"
    pack_snapshot_zip(d, z)
    assert snapshot_sha256(d) == snapshot_sha256(z)
    assert [r.key for r in read_snapshot(d)] == [r.key for r in read_snapshot(z)]


def test_backfill_reads_zip(con, tmp_path: Path):
    d = write_snapshot(tmp_path, "20240101", [R1])
    z = tmp_path / "20240101.zip"
    pack_snapshot_zip(d, z)
    import shutil
    shutil.rmtree(d)
    write_snapshot(tmp_path, "20240201", [R1, R2])
    results = importer.backfill(con, tmp_path)
    assert [r.source_date for r in results] == ["2024-01-01", "2024-02-01"]
    assert con.execute("SELECT count(*) AS n FROM number_range").fetchone()["n"] == 2


def test_date_from_zip_and_hhmm():
    assert importer.date_from_dir_name("20261005.zip") == "2026-10-05"
    assert importer.date_from_dir_name("20261005-0010.zip") == "2026-10-05T00:10"
    assert importer.date_from_dir_name("20261005-0010") == "2026-10-05T00:10"


def test_compress_existing_dirs(tmp_path: Path):
    d = write_snapshot(tmp_path, "20240101", [R1])
    digest = snapshot_sha256(d)
    results = sync.compress_existing_dirs(tmp_path)
    assert len(results) == 1
    z = tmp_path / "20240101.zip"
    assert z.is_file()
    assert not d.exists()
    assert snapshot_sha256(z) == digest


def test_sync_unchanged_skips_download(con, portal_ctrl, monkeypatch):
    archive = portal_ctrl["archive"]
    # первый проход — импорт
    res1 = sync.sync_once(
        con,
        archive_dir=archive,
        index_url=portal_ctrl["index_url"],
        base_url=portal_ctrl["base_url"],
    )
    assert res1.action == "imported"
    assert (archive / "20261005.zip").is_file()

    calls = {"n": 0}
    real_download = sync.download_file

    def counting_download(url, dest):
        calls["n"] += 1
        return real_download(url, dest)

    monkeypatch.setattr(sync, "download_file", counting_download)
    res2 = sync.sync_once(
        con,
        archive_dir=archive,
        index_url=portal_ctrl["index_url"],
        base_url=portal_ctrl["base_url"],
    )
    assert res2.action == "unchanged"
    assert calls["n"] == 0


def test_sync_same_hash_updates_cursor_only(con, portal_ctrl):
    archive = portal_ctrl["archive"]
    sync.sync_once(
        con,
        archive_dir=archive,
        index_url=portal_ctrl["index_url"],
        base_url=portal_ctrl["base_url"],
    )
    batches = con.execute("SELECT count(*) AS n FROM import_batch").fetchone()["n"]
    assert batches == 1

    portal_ctrl["set_mtime"]("06-Oct-2026 00:10")
    res = sync.sync_once(
        con,
        archive_dir=archive,
        index_url=portal_ctrl["index_url"],
        base_url=portal_ctrl["base_url"],
    )
    assert res.action == "republished"
    assert con.execute("SELECT count(*) AS n FROM import_batch").fetchone()["n"] == 1
    assert (archive / "20261006.zip").exists() is False
    row = con.execute("SELECT remote_mtime FROM sync_state WHERE filename='ABC-4xx.csv'").fetchone()
    assert row["remote_mtime"] == "06-Oct-2026 00:10"


def test_sync_new_hash_imports_and_writes_zip(con, portal_ctrl, tmp_path, monkeypatch):
    archive = portal_ctrl["archive"]
    latest = tmp_path / "inCSV"
    monkeypatch.setattr(sync, "INCSV_DIR", latest)

    sync.sync_once(
        con,
        archive_dir=archive,
        index_url=portal_ctrl["index_url"],
        base_url=portal_ctrl["base_url"],
    )
    portal_ctrl["set_mtime"]("07-Oct-2026 12:00")
    portal_ctrl["set_rows"]([R1])  # другой набор диапазонов
    res = sync.sync_once(
        con,
        archive_dir=archive,
        index_url=portal_ctrl["index_url"],
        base_url=portal_ctrl["base_url"],
    )
    assert res.action == "imported"
    assert (archive / "20261007.zip").is_file()
    assert con.execute("SELECT count(*) AS n FROM import_batch").fetchone()["n"] == 2
    assert (latest / "ABC-4xx.csv").is_file()


def test_sync_rejects_bad_csv(con, portal_ctrl):
    archive = portal_ctrl["archive"]
    bad = '495;1000000;1009999;10000;;г. Москва;Город Москва;7710016640'  # пустой оператор
    portal_ctrl["set_rows"]([bad])
    res = sync.sync_once(
        con,
        archive_dir=archive,
        index_url=portal_ctrl["index_url"],
        base_url=portal_ctrl["base_url"],
    )
    assert res.action == "rejected"
    rejected = list((archive / "_rejected").glob("*.zip"))
    assert len(rejected) == 1
    assert not list(archive.glob("2026*.zip"))
    assert con.execute("SELECT count(*) AS n FROM import_batch").fetchone()["n"] == 0


def test_choose_snapshot_names_same_day(con, tmp_path: Path):
    archive = tmp_path
    published = sync.parse_index_mtime("05-Oct-2026 00:10")
    stem, source_date = sync.choose_snapshot_names(con, published, archive)
    assert (stem, source_date) == ("20261005", "2026-10-05")
    (archive / "20261005.zip").write_bytes(b"PK")
    stem2, source_date2 = sync.choose_snapshot_names(con, published, archive)
    assert stem2 == "20261005-0010"
    assert source_date2 == "2026-10-05T00:10"
