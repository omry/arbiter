from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path


WHEEL_TAG = "py3-none-any"


Person = dict[str, str]


def _root() -> Path:
    return Path(__file__).resolve().parent


def _section(text: str, name: str) -> str:
    pattern = re.compile(
        rf"^\[{re.escape(name)}\]\n(?P<body>.*?)(?=^\[|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        return ""
    return match.group("body")


def _quoted_scalar(body: str, key: str) -> str:
    pattern = re.compile(rf'^{re.escape(key)}\s*=\s*"([^"]*)"$', flags=re.MULTILINE)
    match = pattern.search(body)
    if match is None:
        return ""
    return match.group(1)


def _quoted_array(body: str, key: str) -> list[str]:
    inline = re.search(
        rf"^{re.escape(key)}\s*=\s*\[(?P<items>[^\]]*)\]$",
        body,
        flags=re.MULTILINE,
    )
    if inline is not None:
        return re.findall(r'"([^"]*)"', inline.group("items"))

    pattern = re.compile(
        rf"^{re.escape(key)}\s*=\s*\[(?P<items>.*?)^\]",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body)
    if match is None:
        return []
    return re.findall(r'"([^"]*)"', match.group("items"))


def _people_array(body: str, key: str) -> list[Person]:
    pattern = re.compile(
        rf"^{re.escape(key)}\s*=\s*\[(?P<items>.*?)^\]",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body)
    if match is None:
        return []

    people: list[Person] = []
    for item in re.finditer(r"\{(?P<body>.*?)\}", match.group("items"), re.DOTALL):
        fields = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', item.group("body")))
        if fields:
            people.append(fields)
    return people


def _urls(body: str) -> dict[str, str]:
    urls: dict[str, str] = {}
    for key, value in re.findall(r'^([^=\n]+?)\s*=\s*"([^"]*)"$', body, re.MULTILINE):
        urls[key.strip()] = value
    return urls


def _project_metadata() -> dict[str, object]:
    text = (_root() / "pyproject.toml").read_text(encoding="utf-8")
    project = _section(text, "project")
    urls = _section(text, "project.urls")
    metadata: dict[str, object] = {
        "authors": _people_array(project, "authors"),
        "classifiers": _quoted_array(project, "classifiers"),
        "keywords": _quoted_array(project, "keywords"),
        "maintainers": _people_array(project, "maintainers"),
        "urls": _urls(urls),
    }
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if not in_project or "=" not in stripped:
            continue
        key, _separator, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            metadata[key] = value[1:-1]
    return metadata


def _metadata_string(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    if isinstance(value, str):
        return value
    raise KeyError(key)


def _metadata_string_list(metadata: dict[str, object], key: str) -> list[str]:
    value = metadata.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _metadata_people(metadata: dict[str, object], key: str) -> list[Person]:
    value = metadata.get(key)
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    return []


def _metadata_urls(metadata: dict[str, object]) -> dict[str, str]:
    value = metadata.get("urls")
    if isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        return value
    return {}


def _person_names(people: list[Person]) -> str:
    return ", ".join(person["name"] for person in people if person.get("name"))


def _person_emails(people: list[Person]) -> str:
    emails: list[str] = []
    for person in people:
        name = person.get("name")
        email = person.get("email")
        if name and email:
            emails.append(f"{name} <{email}>")
        elif email:
            emails.append(email)
    return ", ".join(emails)


def _distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _dist_info_name(metadata: dict[str, object]) -> str:
    return (
        f"{_distribution_name(_metadata_string(metadata, 'name'))}-"
        f"{_metadata_string(metadata, 'version')}.dist-info"
    )


def _wheel_name(metadata: dict[str, object]) -> str:
    return (
        f"{_distribution_name(_metadata_string(metadata, 'name'))}-"
        f"{_metadata_string(metadata, 'version')}-{WHEEL_TAG}.whl"
    )


def _metadata_file(metadata: dict[str, object]) -> bytes:
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {_metadata_string(metadata, 'name')}",
        f"Version: {_metadata_string(metadata, 'version')}",
    ]
    if description := metadata.get("description"):
        lines.append(f"Summary: {description}")
    if requires_python := metadata.get("requires-python"):
        lines.append(f"Requires-Python: {requires_python}")
    if license_text := metadata.get("license"):
        lines.append(f"License: {license_text}")

    authors = _metadata_people(metadata, "authors")
    if author_names := _person_names(authors):
        lines.append(f"Author: {author_names}")
    if author_emails := _person_emails(authors):
        lines.append(f"Author-email: {author_emails}")

    maintainers = _metadata_people(metadata, "maintainers")
    if maintainer_names := _person_names(maintainers):
        lines.append(f"Maintainer: {maintainer_names}")
    if maintainer_emails := _person_emails(maintainers):
        lines.append(f"Maintainer-email: {maintainer_emails}")

    if keywords := _metadata_string_list(metadata, "keywords"):
        lines.append(f"Keywords: {','.join(keywords)}")
    for classifier in _metadata_string_list(metadata, "classifiers"):
        lines.append(f"Classifier: {classifier}")
    for label, url in _metadata_urls(metadata).items():
        lines.append(f"Project-URL: {label}, {url}")
    lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _wheel_file() -> bytes:
    return (
        "Wheel-Version: 1.0\n"
        "Generator: arbiter_skill_build\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {WHEEL_TAG}\n"
    ).encode("utf-8")


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def _zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (mode & 0xFFFF) << 16
    return info


def _source_file(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mode & 0o777


def _wheel_payload(metadata: dict[str, object]) -> dict[str, tuple[bytes, int]]:
    root = _root()
    dist_info = _dist_info_name(metadata)
    version = _metadata_string(metadata, "version")
    return {
        "arbiter_skill/__init__.py": (f'__version__ = "{version}"\n'.encode(), 0o644),
        "arbiter_skill/skill/SKILL.md": _source_file(root / "SKILL.md"),
        "arbiter_skill/skill/agent-skill-installer.yaml": _source_file(
            root / "agent-skill-installer.yaml"
        ),
        f"{dist_info}/METADATA": (_metadata_file(metadata), 0o644),
        f"{dist_info}/WHEEL": (_wheel_file(), 0o644),
        f"{dist_info}/top_level.txt": (b"arbiter_skill\n", 0o644),
    }


def _write_dist_info(metadata_directory: Path, metadata: dict[str, object]) -> str:
    dist_info = _dist_info_name(metadata)
    target = metadata_directory / dist_info
    target.mkdir(parents=True, exist_ok=True)
    (target / "METADATA").write_bytes(_metadata_file(metadata))
    (target / "WHEEL").write_bytes(_wheel_file())
    (target / "top_level.txt").write_text("arbiter_skill\n", encoding="utf-8")
    return dist_info


def get_requires_for_build_wheel(config_settings=None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings=None,
) -> str:
    return _write_dist_info(Path(metadata_directory), _project_metadata())


def build_wheel(
    wheel_directory: str,
    config_settings=None,
    metadata_directory: str | None = None,
) -> str:
    metadata = _project_metadata()
    filename = _wheel_name(metadata)
    output = Path(wheel_directory) / filename
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = _wheel_payload(metadata)
    record_name = f"{_dist_info_name(metadata)}/RECORD"
    rows: list[list[str]] = []
    with zipfile.ZipFile(output, "w") as archive:
        for name in sorted(payload):
            data, mode = payload[name]
            archive.writestr(_zip_info(name, mode), data)
            rows.append([name, _record_hash(data), str(len(data))])

        rows.append([record_name, "", ""])
        record_text = io.StringIO()
        writer = csv.writer(record_text, lineterminator="\n")
        writer.writerows(rows)
        archive.writestr(_zip_info(record_name, 0o644), record_text.getvalue())
    return filename
