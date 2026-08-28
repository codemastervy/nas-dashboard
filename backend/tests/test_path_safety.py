"""Path containment -- the chokepoint that keeps the API inside its volumes."""
import pytest

from app.services import fs


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    (root / "data" / "Documents").mkdir(parents=True)
    (root / "media").mkdir(parents=True)
    (root / "data" / "readme.txt").write_text("hello")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not read me")

    monkeypatch.setattr(fs, "STORAGE_ROOT", root)
    return root, outside


def test_volumes_are_listed_separately(storage):
    names = {v["name"] for v in fs.volumes()}
    assert names == {"data", "media"}


def test_ordinary_path_resolves(storage):
    node = fs.resolve("/data/Documents")
    assert node.volume == "data"
    assert node.real.is_dir()


@pytest.mark.parametrize("attempt", [
    "/data/../../etc/passwd",
    "/data/../..",
    "../etc",
    "/data/./../../root",
    "/data/subdir/../../../etc",
])
def test_dotdot_is_rejected(storage, attempt):
    with pytest.raises(fs.FsError) as exc:
        fs.resolve(attempt)
    assert exc.value.status == 400


def test_unknown_volume_is_404(storage):
    with pytest.raises(fs.FsError) as exc:
        fs.resolve("/etc")
    assert exc.value.status == 404


def test_symlink_out_of_the_volume_is_refused(storage):
    root, outside = storage
    (root / "data" / "escape").symlink_to(outside)
    with pytest.raises(fs.FsError) as exc:
        fs.resolve("/data/escape")
    assert exc.value.status == 403
    assert "escapes" in exc.value.message


def test_symlink_to_a_file_outside_is_refused(storage):
    root, outside = storage
    (root / "data" / "leak.txt").symlink_to(outside / "secret.txt")
    with pytest.raises(fs.FsError) as exc:
        fs.resolve("/data/leak.txt")
    assert exc.value.status == 403


def test_symlink_within_the_volume_is_allowed(storage):
    root, _ = storage
    (root / "data" / "inside").symlink_to(root / "data" / "Documents")
    node = fs.resolve("/data/inside")
    assert node.real.is_dir()


def test_null_byte_is_rejected(storage):
    with pytest.raises(fs.FsError):
        fs.resolve("/data/read\x00me.txt")


def test_volume_root_cannot_be_deleted(storage):
    result = fs.delete(["/data"])
    assert result["deleted"] == []
    assert "volume root" in result["failed"][0]["error"]


def test_collision_deduplicates_rather_than_overwriting(storage):
    root, _ = storage
    target = fs.unique_destination(root / "data", "readme.txt")
    assert target.name == "readme 2.txt"
    assert (root / "data" / "readme.txt").read_text() == "hello"


def test_bad_names_are_rejected(storage):
    for bad in ["", ".", "..", "a/b"]:
        with pytest.raises(fs.FsError):
            fs.mkdir("/data", bad)


def test_moving_a_folder_into_itself_is_refused(storage):
    result = fs.transfer(["/data/Documents"], "/data/Documents", move=True)
    assert result["failed"]
    assert "into itself" in result["failed"][0]["error"]
