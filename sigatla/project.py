"""The project folder: atlas.json, sources/, build/, notes/ and site/ (see docs/DATA_FORMAT.md)."""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "atlas.json"
KINDS = ("paper", "slides", "notes")
DEFAULT_MODEL = "claude-opus-5-5"


class AtlasError(Exception):
    """An error the command line reports without a traceback."""


def slugify(text: str, max_len: int = 40) -> str:
    """Lowercase ASCII slug: letters, digits and single hyphens (Vietnamese diacritics are folded)."""
    text = unicodedata.normalize("NFD", str(text)).replace("đ", "d").replace("Đ", "D")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "source"


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AtlasError(f"{path} is not valid JSON: {e}") from e


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass
class Source:
    id: str
    kind: str
    file: str
    label: str
    short: str = ""   # optional short name for the jump bar; the label is used when empty

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "file": self.file, "label": self.label}
        if self.short:
            d["short"] = self.short
        return d


@dataclass
class Project:
    root: Path
    config: dict = field(default_factory=dict)

    # ---- loading and saving
    @classmethod
    def load(cls, root: str | Path = ".") -> "Project":
        root = Path(root).resolve()
        for candidate in (root, *root.parents):
            if (candidate / CONFIG_NAME).exists():
                return cls(candidate, read_json(candidate / CONFIG_NAME, {}))
        raise AtlasError(f"No {CONFIG_NAME} here or in a parent folder. Run `sigatla init` first.")

    @classmethod
    def create(cls, root: str | Path, title: str, language: str = "en", subtitle: str = "") -> "Project":
        root = Path(root).resolve()
        if (root / CONFIG_NAME).exists():
            raise AtlasError(f"{root / CONFIG_NAME} already exists.")
        for sub in ("sources", "notes"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        project = cls(root, {"title": title, "subtitle": subtitle, "language": language,
                             "model": DEFAULT_MODEL, "sources": []})
        project.save()
        (root / ".gitignore").write_text("build/\nsite/\n", encoding="utf-8")
        return project

    def save(self) -> None:
        write_json(self.root / CONFIG_NAME, self.config)

    # ---- settings
    @property
    def title(self) -> str:
        return self.config.get("title") or "Sigatla"

    @property
    def language(self) -> str:
        return self.config.get("language") or "en"

    @property
    def model(self) -> str:
        return self.config.get("model") or DEFAULT_MODEL

    # ---- sources
    @property
    def sources(self) -> list[Source]:
        return [Source(s["id"], s.get("kind", "paper"), s["file"], s.get("label") or s["id"], s.get("short") or "")
                for s in self.config.get("sources", [])]

    def source(self, sid: str) -> Source:
        for s in self.sources:
            if s.id == sid:
                return s
        raise AtlasError(f"No source with id '{sid}'. Known: {', '.join(s.id for s in self.sources) or 'none'}.")

    def pick(self, ids: list[str] | None) -> list[Source]:
        return [self.source(i) for i in ids] if ids else self.sources

    def add_source(self, file: str | Path, kind: str, sid: str | None = None, label: str | None = None,
                   copy: bool = True, short: str | None = None) -> Source:
        path = Path(file).expanduser().resolve()
        if not path.exists():
            raise AtlasError(f"File not found: {path}")
        if path.suffix.lower() != ".pdf":
            raise AtlasError("Only PDF files are supported. Export slides (PowerPoint, Keynote, Google Slides) to PDF first.")
        if kind not in KINDS:
            raise AtlasError(f"--kind must be one of {', '.join(KINDS)}.")
        sid = slugify(sid or path.stem)
        if sid in ("axis", "shared", "related", "rw") or any(s.id == sid for s in self.sources):
            raise AtlasError(f"The id '{sid}' is taken or reserved; pass another with --id.")
        dest = path
        if copy:
            dest = self.root / "sources" / f"{sid}.pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path != dest:
                shutil.copy2(path, dest)
        try:
            rel = str(dest.relative_to(self.root))
        except ValueError:
            rel = str(dest)
        src = Source(sid, kind, rel, label or path.stem, (short or "").strip())
        self.config.setdefault("sources", []).append(src.to_dict())
        self.save()
        return src

    # ---- paths
    def source_path(self, s: Source) -> Path:
        p = Path(s.file)
        return p if p.is_absolute() else self.root / p

    def build_dir(self, sid: str) -> Path:
        return self.root / "build" / sid

    def manifest_path(self, sid: str) -> Path:
        return self.build_dir(sid) / "manifest.json"

    def notes_path(self, sid: str) -> Path:
        return self.root / "notes" / f"{sid}.json"

    def part_paths(self, sid: str) -> list[Path]:
        """notes/<id>.part-<k>.json files of a long source read in parts, in part order."""
        import re as _re
        return sorted(self.root.glob(f"notes/{sid}.part-*.json"),
                      key=lambda p: int(_re.search(r"part-(\d+)", p.name).group(1)))

    def all_nodes(self, sid: str) -> list[dict]:
        """Every note written for a source: notes/<id>.json plus its part files."""
        nodes = list((read_json(self.notes_path(sid), {}) or {}).get("nodes", []))
        for path in self.part_paths(sid):
            nodes += (read_json(path, {}) or {}).get("nodes", [])
        return nodes

    @property
    def shared_path(self) -> Path:
        return self.root / "notes" / "_shared.json"

    @property
    def qa_path(self) -> Path:
        return self.root / "notes" / "_qa.json"

    @property
    def site_dir(self) -> Path:
        return self.root / "site"
