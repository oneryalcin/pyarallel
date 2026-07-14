"""Read-only checkpoint inspection: metadata without result deserialization."""

from __future__ import annotations

import os
import pickle
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pyarallel import (
    CheckpointError,
    CheckpointInfo,
    checkpoint_info,
    parallel_map,
)

type CheckpointVersion = str | int | bytes | tuple[str | int | bytes, ...] | None


def _identity(value: int) -> int:
    return value


def _create_checkpoint(
    path: Path,
    *,
    values: list[int] | None = None,
    version: CheckpointVersion = None,
) -> None:
    result = parallel_map(
        _identity,
        [] if values is None else values,
        checkpoint=path,
        checkpoint_version=version,
        sequential=True,
    )
    assert result.ok


def _create_raw_checkpoint(path: Path, *, completed: int = 0) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE results ("
        "key TEXT PRIMARY KEY, fingerprint BLOB NOT NULL, value BLOB NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        (
            ("schema_version", "2"),
            ("task_signature", "tests.work:0123456789abcdef"),
        ),
    )
    for index in range(completed):
        conn.execute(
            "INSERT INTO results (key, fingerprint, value) VALUES (?, ?, ?)",
            (f"i:{index}", b"fingerprint", pickle.dumps(index)),
        )
    conn.commit()
    return conn


class TestCheckpointInfoValues:
    def test_reports_existing_checkpoint_without_loading_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "run.ckpt"
        _create_checkpoint(path, values=[1, 2, 3], version=("model-v2", 7, b"x"))
        expected_size = os.lstat(path).st_size

        def refuse_unpickle(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("checkpoint_info must not unpickle result rows")

        monkeypatch.setattr("pyarallel.checkpoint.pickle.loads", refuse_unpickle)
        info = checkpoint_info(path)

        assert info == CheckpointInfo(
            path=path,
            schema_version="2",
            checkpoint_version=("model-v2", 7, b"x"),
            task_signature=info.task_signature,
            completed=3,
            size_bytes=expected_size,
        )
        assert info.task_signature.startswith(f"{__name__}._identity:")

    def test_zero_rows_and_missing_semantic_version(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.ckpt"
        _create_checkpoint(path)

        info = checkpoint_info(path)

        assert info.completed == 0
        assert info.checkpoint_version is None

    @pytest.mark.parametrize(
        "version",
        ["v2", 2, b"v2", (), ("v2", 2, b"x"), "x" * 100_000],
    )
    def test_decodes_every_writer_supported_version(
        self, tmp_path: Path, version: CheckpointVersion
    ) -> None:
        path = tmp_path / "version.ckpt"
        _create_checkpoint(path, version=version)

        assert checkpoint_info(path).checkpoint_version == version

    def test_identity_keyed_checkpoint_reports_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "keys.ckpt"
        result = parallel_map(
            _identity,
            [3, 1, 2],
            checkpoint=path,
            checkpoint_key=lambda value: f"customer-{value}",
            sequential=True,
        )
        assert result.ok

        assert checkpoint_info(path).completed == 3

    def test_frozen_value_is_repeatable_for_quiescent_file(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "run.ckpt"
        _create_checkpoint(path, values=[1])

        first = checkpoint_info(path)
        second = checkpoint_info(path)

        assert first == second
        with pytest.raises((AttributeError, TypeError)):
            first.completed = 2  # type: ignore[misc]


class TestCheckpointInfoPaths:
    def test_missing_file_is_not_created(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.ckpt"

        with pytest.raises(FileNotFoundError):
            checkpoint_info(path)

        assert not path.exists()

    def test_rejects_directory_and_symlink(self, tmp_path: Path) -> None:
        with pytest.raises(CheckpointError, match="not a regular file"):
            checkpoint_info(tmp_path)

        target = tmp_path / "target.ckpt"
        _create_checkpoint(target)
        link = tmp_path / "link.ckpt"
        try:
            link.symlink_to(target)
        except OSError as exc:  # Windows runner without symlink privilege
            pytest.skip(f"symlinks unavailable: {exc}")
        with pytest.raises(CheckpointError, match="not a regular file"):
            checkpoint_info(link)

    def test_reserved_unicode_relative_path_and_handle_closure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        relative = Path("space # question ? percent % café.ckpt")
        _create_checkpoint(relative, values=[1])

        info = checkpoint_info(relative)

        assert info.path == relative
        renamed = Path("renamed.ckpt")
        relative.rename(renamed)
        renamed.unlink()

    def test_translates_uri_construction_os_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "run.ckpt"
        _create_checkpoint(path)

        def refuse_absolute(_path: Path) -> Path:
            raise PermissionError("denied after validation")

        monkeypatch.setattr(Path, "absolute", refuse_absolute)
        with pytest.raises(CheckpointError, match="usable checkpoint"):
            checkpoint_info(path)


class TestCheckpointInfoRefusals:
    @pytest.mark.parametrize(
        "stored",
        [
            "True",
            "None",
            "('v2', ('nested',))",
            "[1, 2]",
            "not python",
            '"noncanonical-quotes"',
        ],
    )
    def test_rejects_malformed_version_metadata(
        self, tmp_path: Path, stored: str
    ) -> None:
        path = tmp_path / "bad-version.ckpt"
        conn = _create_raw_checkpoint(path)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('checkpoint_version', ?)",
            (stored,),
        )
        conn.commit()
        conn.close()

        with pytest.raises(CheckpointError, match="checkpoint-version"):
            checkpoint_info(path)

    def test_translates_version_parser_recursion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "recursive-version.ckpt"
        conn = _create_raw_checkpoint(path)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('checkpoint_version', ?)",
            (repr("v2"),),
        )
        conn.commit()
        conn.close()

        def recurse(_value: str) -> object:
            raise RecursionError("parser exhausted")

        monkeypatch.setattr("pyarallel.checkpoint.ast.literal_eval", recurse)
        with pytest.raises(CheckpointError, match="checkpoint-version"):
            checkpoint_info(path)

    def test_rejects_random_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "random.ckpt"
        path.write_bytes(b"not sqlite")

        with pytest.raises(CheckpointError, match="usable checkpoint"):
            checkpoint_info(path)

    @pytest.mark.parametrize("key", ["task_signature", "checkpoint_version"])
    def test_rejects_blob_metadata(self, tmp_path: Path, key: str) -> None:
        path = tmp_path / "blob-meta.ckpt"
        conn = _create_raw_checkpoint(path)
        if key == "checkpoint_version":
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)", (key, b"'v2'")
            )
        else:
            conn.execute("UPDATE meta SET value = ? WHERE key = ?", (b"sig", key))
        conn.commit()
        conn.close()

        with pytest.raises(CheckpointError, match="non-text metadata"):
            checkpoint_info(path)

    @pytest.mark.parametrize(
        "mutation",
        [
            "DROP TABLE results; CREATE VIEW results AS SELECT 'x' AS key",
            "DROP TABLE results; CREATE TABLE results (x TEXT)",
            "DELETE FROM meta WHERE key = 'schema_version'",
            "UPDATE meta SET value = '1' WHERE key = 'schema_version'",
            "DELETE FROM meta WHERE key = 'task_signature'",
            "UPDATE meta SET value = '   ' WHERE key = 'task_signature'",
            "UPDATE meta SET value = 'foreign' WHERE key = 'task_signature'",
        ],
    )
    def test_rejects_foreign_schema_and_metadata(
        self, tmp_path: Path, mutation: str
    ) -> None:
        path = tmp_path / "foreign.ckpt"
        conn = _create_raw_checkpoint(path)
        conn.executescript(mutation)
        conn.commit()
        conn.close()

        with pytest.raises(CheckpointError):
            checkpoint_info(path)


class TestCheckpointInfoSQLiteBehavior:
    def test_sql_trace_contains_only_inspection_statements(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "trace.ckpt"
        _create_checkpoint(path, values=[1])
        statements: list[str] = []
        real_connect = sqlite3.connect

        def traced_connect(database: str, *, uri: bool = False) -> sqlite3.Connection:
            conn = real_connect(database, uri=uri)
            conn.set_trace_callback(statements.append)
            return conn

        monkeypatch.setattr("pyarallel.checkpoint.sqlite3.connect", traced_connect)
        checkpoint_info(path)

        normalized = [" ".join(statement.upper().split()) for statement in statements]
        assert any(statement == "BEGIN" for statement in normalized)
        assert any("PRAGMA QUERY_ONLY=ON" in statement for statement in normalized)
        assert any(
            "SELECT COUNT(*) FROM RESULTS" in statement for statement in normalized
        )
        assert not any("RESULTS.VALUE" in statement for statement in normalized)
        forbidden = (
            "INSERT ",
            "UPDATE ",
            "DELETE ",
            "CREATE ",
            "DROP ",
            "ALTER ",
            "ATTACH ",
            "VACUUM",
            "JOURNAL_MODE",
        )
        assert not any(
            statement.startswith(forbidden) or "PRAGMA JOURNAL_MODE" in statement
            for statement in normalized
        )

    def test_read_transaction_prevents_mixed_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "snapshot.ckpt"
        writer = _create_raw_checkpoint(path, completed=1)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.commit()
        real_connect = sqlite3.connect

        class CommitBetweenReads(sqlite3.Connection):
            triggered = False

            def execute(  # type: ignore[no-untyped-def]
                self, sql: str, parameters=(), /
            ) -> sqlite3.Cursor:
                cursor = super().execute(sql, parameters)
                if sql.startswith("SELECT key, value FROM meta") and not self.triggered:
                    self.triggered = True
                    writer.execute(
                        "INSERT INTO results (key, fingerprint, value)"
                        " VALUES (?, ?, ?)",
                        ("i:later", b"fingerprint", pickle.dumps("later")),
                    )
                    writer.commit()
                return cursor

        def instrumented_connect(
            database: str, *, uri: bool = False
        ) -> sqlite3.Connection:
            return real_connect(database, uri=uri, factory=CommitBetweenReads)

        monkeypatch.setattr(
            "pyarallel.checkpoint.sqlite3.connect", instrumented_connect
        )
        try:
            info = checkpoint_info(path)
        finally:
            writer.close()

        assert info.completed == 1
        assert sqlite3.connect(path).execute(
            "SELECT COUNT(*) FROM results"
        ).fetchone() == (2,)

    def test_reads_committed_rows_left_in_wal_after_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "crash.ckpt"
        script = """
import os
import pickle
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA wal_autocheckpoint=0')
conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
conn.execute(
    'CREATE TABLE results ('
    'key TEXT PRIMARY KEY, fingerprint BLOB NOT NULL, value BLOB NOT NULL)'
)
conn.executemany(
    'INSERT INTO meta (key, value) VALUES (?, ?)',
    [('schema_version', '2'), ('task_signature', 'crash.worker:0123456789abcdef')],
)
conn.execute(
    'INSERT INTO results (key, fingerprint, value) VALUES (?, ?, ?)',
    ('i:0', b'fingerprint', pickle.dumps(1)),
)
conn.commit()
os._exit(0)
"""
        subprocess.run([sys.executable, "-c", script, str(path)], check=True)

        assert Path(f"{path}-wal").exists()
        assert checkpoint_info(path).completed == 1
