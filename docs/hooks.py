from __future__ import annotations

import contextlib
import hashlib
import html
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from mkdocs.exceptions import BuildError

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.files import Files
    from mkdocs.structure.nav import Page

log = logging.getLogger("mkdocs.hooks")

# ---------------------------------------------------------------------------
# Anchor aliases
#
# A build-time redirect mechanism for anchors of renamed headings. The alias
# manifest is a YAML file (next to mkdocs.yml) that maps documentation pages
# to ``old anchor id -> current anchor id`` mappings:
#
#     user-guide/configuration.md:
#       old-section-name: the-new-section-name
#       other-old-name: other-new-name
#
# Aliases may point at other aliases (chains are followed) as long as the
# chain ends at an anchor that the page actually defines (a heading or a
# manually set anchor). For every alias an empty marker element carrying the
# old id is injected right before the target element, so links using the old
# anchor keep scrolling to the current section instead of landing on top of
# the page.
#
# The build aborts when an alias is defined more than once, collides with an
# existing anchor, points at an anchor that does not exist, or participates
# in a cycle.
# ---------------------------------------------------------------------------

ALIAS_MANIFEST_NAME = "anchor-aliases.yml"
ALIAS_ROSTER_NAME = "anchor-aliases.json"
ALIAS_STATE_NAME = ".anchor-aliases.state.json"
ALIAS_MARKER_CLASS = "mkdocs-anchor-alias"

# Per-build state, reset in `on_config`. The manifest only contains entries
# for pages that still exist; the roster only contains aliases that were
# actually injected, so removing a manifest entry (or renaming a target away)
# can never leave stale aliases behind.
_alias_manifest: dict[str, dict[str, str]] = {}
_alias_roster: dict[str, dict[str, str]] = {}
# Pages that must be re-rendered because the manifest content changed
# (matters for `mkdocs build --dirty` / `mkdocs serve`, which otherwise skip
# pages whose Markdown source is untouched).
_alias_force_rebuild: set[str] = set()
_alias_state: dict[str, str] = {"digest": ""}


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys in any mapping."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep=False
):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            line = key_node.start_mark.line + 1
            raise BuildError(
                f"Duplicate anchor alias {key!r} in {ALIAS_MANIFEST_NAME} (line {line}): "
                f"each old anchor may only be mapped once per page."
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _alias_manifest_path(config: MkDocsConfig) -> Path:
    return Path(config.config_file_path).parent / ALIAS_MANIFEST_NAME


def _read_alias_manifest(config: MkDocsConfig) -> tuple[dict[str, dict[str, str]], str]:
    """Return the parsed manifest and the sha256 digest of its raw file content."""
    path = _alias_manifest_path(config)
    if not path.is_file():
        return {}, hashlib.sha256(b"").hexdigest()

    raw = path.read_bytes()
    try:
        data = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as e:
        raise BuildError(f"Cannot parse {ALIAS_MANIFEST_NAME}: {e}") from e

    if data is None:
        return {}, hashlib.sha256(raw).hexdigest()
    if not isinstance(data, dict):
        raise BuildError(
            f"{ALIAS_MANIFEST_NAME} must be a mapping of documentation pages to mappings of "
            f"anchor aliases."
        )

    manifest: dict[str, dict[str, str]] = {}
    for page, aliases in data.items():
        if not isinstance(page, str) or not isinstance(aliases, dict):
            raise BuildError(
                f"Each entry in {ALIAS_MANIFEST_NAME} must map a page path to a mapping of "
                f"'old anchor: current anchor', got {page!r}: {aliases!r}."
            )
        page_aliases: dict[str, str] = {}
        for alias, target in aliases.items():
            if (
                not isinstance(alias, str)
                or not isinstance(target, str)
                or not alias
                or not target
            ):
                raise BuildError(
                    f"Anchor aliases in {ALIAS_MANIFEST_NAME} for {page!r} must map non-empty "
                    f"strings to non-empty strings, got {alias!r}: {target!r}."
                )
            page_aliases[alias] = target
        manifest[page] = page_aliases
    return manifest, hashlib.sha256(raw).hexdigest()


def _site_file(config: MkDocsConfig, name: str) -> str:
    return os.path.join(config.site_dir, name)


def _read_previous_state(config: MkDocsConfig) -> tuple[str | None, set[str]]:
    """Return (manifest digest from the previous build, pages that previously had aliases)."""
    try:
        with open(_site_file(config, ALIAS_STATE_NAME), encoding="utf-8") as f:
            previous_digest = json.load(f).get("digest")
    except (OSError, ValueError):
        previous_digest = None
    try:
        with open(_site_file(config, ALIAS_ROSTER_NAME), encoding="utf-8") as f:
            previous_pages = set(json.load(f))
    except (OSError, ValueError):
        previous_pages = set()
    return previous_digest, previous_pages


def _resolve_alias(
    alias: str, aliases: dict[str, str], real_anchors: set[str], page_src_uri: str
) -> str:
    """Follow the alias chain for `alias` until it reaches a real anchor."""
    seen: list[str] = []
    current = alias
    while current not in real_anchors:
        if current in seen:
            chain = " -> ".join(repr(a) for a in [*seen, current])
            raise BuildError(
                f"Anchor alias cycle detected in {ALIAS_MANIFEST_NAME} for {page_src_uri!r}: "
                f"{chain}."
            )
        seen.append(current)
        next_anchor = aliases.get(current)
        if next_anchor is None:
            raise BuildError(
                f"Anchor alias {alias!r} in {ALIAS_MANIFEST_NAME} for {page_src_uri!r} points to "
                f"{current!r}, but the page does not contain a heading or anchor with that id."
            )
        current = next_anchor
    return current


def _alias_marker(alias: str) -> str:
    return (
        f'<span id="{html.escape(alias, quote=True)}" class="{ALIAS_MARKER_CLASS}" '
        f'aria-hidden="true"></span>'
    )


def _inject_alias(content: str, alias: str, target: str, page_src_uri: str) -> str:
    """
    Insert the alias marker at the element carrying `target`.

    For headings the marker is placed right inside the opening tag, so it scrolls
    to exactly the heading's top and cannot add a stray line box before it; for
    any other element (e.g. a raw ``<a id>`` anchor) it goes directly before it.
    """
    target_re = re.escape(target)
    # An `id` attribute on any element, or a `name` attribute on an <a> (raw HTML anchors).
    patterns = (
        re.compile(
            r'<(?P<tag>[a-zA-Z][\w:.-]*)\b[^>]*?\bid=(["\'])' + target_re + r"\2[^>]*>",
            re.IGNORECASE,
        ),
        re.compile(
            r'<(?P<tag>a)\b[^>]*?\bname=(["\'])' + target_re + r"\2[^>]*>",
            re.IGNORECASE,
        ),
    )
    marker = _alias_marker(alias)
    for pattern in patterns:
        match = pattern.search(content)
        if match is None:
            continue
        if match.group("tag").lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return content[: match.end()] + marker + content[match.end() :]
        return content[: match.start()] + marker + content[match.start() :]
    raise BuildError(
        f"Could not place anchor alias {alias!r} on {page_src_uri!r}: the target {target!r} is "
        f"known to the page but no matching element was found in the rendered HTML."
    )


def _atomic_write_file(path: str, content: bytes) -> None:
    """Write a file atomically so an interrupted build can never leave it half-written."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise


def on_config(config: MkDocsConfig, **kwargs) -> MkDocsConfig:
    _alias_manifest.clear()
    _alias_roster.clear()
    _alias_force_rebuild.clear()

    manifest, digest = _read_alias_manifest(config)
    _alias_manifest.update(manifest)
    _alias_state["digest"] = digest

    previous_digest, previous_pages = _read_previous_state(config)
    # `None` means there is neither a manifest nor a previous build state.
    if previous_digest is not None or any(_alias_manifest.values()):
        if digest != previous_digest:
            # Every page that has aliases now or had them in the previous build must be
            # re-rendered, otherwise dirty builds would keep revoked or moved aliases.
            _alias_force_rebuild.update(previous_pages)
            _alias_force_rebuild.update(_alias_manifest)

    total = sum(len(aliases) for aliases in _alias_manifest.values())
    if total:
        log.info(
            f"Loaded {total} anchor alias"
            f"{'es' if total != 1 else ''} for {len(_alias_manifest)} page"
            f"{'s' if len(_alias_manifest) != 1 else ''} from {ALIAS_MANIFEST_NAME}"
        )
    return config


def on_files(files: Files, *, config: MkDocsConfig, **kwargs) -> Files:
    # Aliases for pages that no longer exist simply vanish instead of failing the build.
    for page_src_uri in list(_alias_manifest):
        if files.get_file_from_path(page_src_uri) is None:
            log.info(
                f"Ignoring anchor aliases for {page_src_uri!r} from {ALIAS_MANIFEST_NAME}: "
                f"no such documentation page."
            )
            _alias_manifest.pop(page_src_uri, None)
            _alias_force_rebuild.discard(page_src_uri)

    # Dirty builds skip pages whose Markdown source is unchanged. Make such pages look
    # modified by ageing their output, so alias changes are never skipped.
    for page_src_uri in sorted(_alias_force_rebuild):
        file = files.get_file_from_path(page_src_uri)
        if (
            file is not None
            and file.abs_dest_path
            and os.path.isfile(file.abs_dest_path)
        ):
            os.utime(file.abs_dest_path, (0, 0))
    return files


def on_page_content(
    html_content: str, *, page: Page, config: MkDocsConfig, **kwargs
) -> str:
    aliases = _alias_manifest.get(page.file.src_uri)
    if not aliases:
        return html_content

    real_anchors = page.present_anchor_ids
    if real_anchors is None:  # pragma: no cover - always set after `Page.render()`.
        return html_content

    for alias in aliases:
        if alias in real_anchors:
            raise BuildError(
                f"Anchor alias {alias!r} in {ALIAS_MANIFEST_NAME} for {page.file.src_uri!r} "
                f"duplicates an anchor id that the page already defines; remove the alias or "
                f"rename the existing anchor."
            )

    # Resolve every chain first: a dangling target or a cycle aborts the build before any
    # output is written, so a broken alias can never land on top of the page again.
    resolved = {
        alias: _resolve_alias(alias, aliases, set(real_anchors), page.file.src_uri)
        for alias in sorted(aliases)
    }

    # Sorted insertion order makes the generated HTML (and thus repeated builds) deterministic.
    for alias, target in resolved.items():
        html_content = _inject_alias(html_content, alias, target, page.file.src_uri)

    # Aliases are now valid link targets on this page, including for anchor link validation.
    real_anchors.update(resolved)
    _alias_roster[page.file.src_uri] = dict(sorted(resolved.items()))
    return html_content


def on_post_build(config: MkDocsConfig, **kwargs) -> None:
    roster_path = _site_file(config, ALIAS_ROSTER_NAME)
    if _alias_roster:
        content = (
            json.dumps(_alias_roster, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        _atomic_write_file(roster_path, content)
    else:
        # No (longer any) aliases: make sure a roster from a previous build disappears too.
        with contextlib.suppress(FileNotFoundError):
            os.remove(roster_path)

    # Record the manifest content this build was based on so the next dirty build
    # can tell whether alias-bearing pages need to be re-rendered.
    state = {"digest": _alias_state["digest"]}
    state_content = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_file(_site_file(config, ALIAS_STATE_NAME), state_content)


def _get_language_of_translation_file(path: Path) -> str:
    with path.open(encoding="utf-8") as f:
        translation_line = f.readline()
    m = re.search("^# (.+) translations ", translation_line)
    assert m
    return m[1]


def on_page_markdown(
    markdown: str, page: Page, config: MkDocsConfig, **kwargs
) -> str | None:
    if page.file.src_uri == "user-guide/choosing-your-theme.md":
        here = Path(config.config_file_path).parent

        def replacement(m: re.Match) -> str:
            lines = []
            for d in sorted(here.glob(m[2])):
                lang = _get_language_of_translation_file(
                    Path(d, "LC_MESSAGES", "messages.po")
                )
                lines.append(f"{m[1]}`{d.name}`: {lang}")
            return "\n".join(lines)

        return re.sub(
            r"^( *\* )\(see the list of existing directories `(.+)`\)$",
            replacement,
            markdown,
            flags=re.MULTILINE,
        )
