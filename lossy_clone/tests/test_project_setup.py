from pathlib import Path

import lossy_clone


def test_lossy_clone_package_imports():
    assert lossy_clone.__file__ is not None


def test_lossy_clone_subdirectories_exist():
    package_dir = Path(lossy_clone.__file__).resolve().parent

    assert (package_dir / "memory").is_dir()
    assert (package_dir / "tests").is_dir()
    assert (package_dir / "data").is_dir()


def test_lossy_clone_requirements_and_readme_exist():
    package_dir = Path(lossy_clone.__file__).resolve().parent

    assert (package_dir / "requirements.txt").is_file()
    assert (package_dir / "README.md").is_file()
