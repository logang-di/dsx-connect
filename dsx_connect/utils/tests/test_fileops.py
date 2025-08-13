# tests/test_file_ops_filters.py
from pathlib import Path
import pytest

# import your module
from dsx_connect.utils.file_ops import get_filepaths  # adjust import if your module name differs


def _make_files(root: Path, relpaths: list[str]):
    for rp in relpaths:
        p = root / rp
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def _paths(root: Path, files):
    """Helper: normalize to posix relative strings."""
    return sorted(str(p.relative_to(root).as_posix()) for p in files)


def run(root: Path, filt: str) -> list[str]:
    return _paths(root, get_filepaths(root, filt))


@pytest.fixture()
def tree(tmp_path: Path):
    # Minimal, focused tree to test semantics
    files = [
        "top.txt",
        "top.zip",

        "PDF/.DS_Store",
        "PDF/BadMojoResume",
        "PDF/sub1/a.txt",
        "PDF/sub1/deep/b.pdf",
        "PDF/tmp/should_be_excluded.txt",   # for -tmp tests

        "reports/x.docx",
        "exports/e.txt",
        "tmp/trash.txt",

        "test/2025-07-30/BadMojoResume",
        "test/2025-07-30/sub1/inner.txt",
        "test/2025-08-12/sub2/inner2.txt",

        "test/scan here/file with space.txt",
        "not here/ignore.txt",

        "files/file_01.txt",
        "files/file_10.txt",
    ]
    _make_files(tmp_path, files)
    return tmp_path


def test_star_top_level(tree: Path):
    got = run(tree, "*")
    assert got == sorted([
        "top.txt", "top.zip",
    ])


def test_empty_recursive(tree: Path):
    got = run(tree, "")
    # spot-check a few
    assert "PDF/sub1/deep/b.pdf" in got
    assert "test/2025-08-12/sub2/inner2.txt" in got
    assert "tmp/trash.txt" in got


def test_bare_subtree_pdf_recurse(tree: Path):
    got = run(tree, "PDF")
    assert set(got) >= {
        "PDF/.DS_Store",
        "PDF/BadMojoResume",
        "PDF/sub1/a.txt",
        "PDF/sub1/deep/b.pdf",
        "PDF/tmp/should_be_excluded.txt",
    }


def test_pdf_children_only(tree: Path):
    got = run(tree, "PDF/*")
    assert got == sorted([
        "PDF/.DS_Store",
        "PDF/BadMojoResume",
    ])


def test_any_depth_double_star(tree: Path):
    got = run(tree, "test/2025*/**")
    # rsync semantics via your _expand_rsync_dirs → includes files too
    assert "test/2025-07-30/sub1/inner.txt" in got
    assert "test/2025-08-12/sub2/inner2.txt" in got


def test_target_subdirs_by_class(tree: Path):
    got = run(tree, "test/2025-*/sub[12]/**/*")
    assert "test/2025-07-30/sub1/inner.txt" in got
    assert "test/2025-08-12/sub2/inner2.txt" in got


def test_question_mark_single_char(tree: Path):
    # '?' should not cross '/'
    got = run(tree, "files/file_??.txt")
    assert set(got) == {"files/file_01.txt", "files/file_10.txt"}


def test_exclude_tmp_any_level(tree: Path):
    got = run(tree, "PDF -tmp")
    # everything under PDF except any subtree named tmp
    assert "PDF/tmp/should_be_excluded.txt" not in got
    assert "PDF/sub1/a.txt" in got
    assert "PDF/BadMojoResume" in got


def test_mix_includes_excludes(tree: Path):
    got = run(tree, "reports exports -tmp --exclude '**/*.pdf'")
    assert "reports/x.docx" in got
    assert "exports/e.txt" in got
    assert "tmp/trash.txt" not in got
    assert "PDF/sub1/deep/b.pdf" not in got


def test_spaces_in_names_with_quotes(tree: Path):
    # shlex.split handling: keep quotes in the filter string literal
    got = run(tree, "'test/scan here' -'not here'")
    assert "test/scan here/file with space.txt" in got
    assert not any(p.startswith("not here/") for p in got)
