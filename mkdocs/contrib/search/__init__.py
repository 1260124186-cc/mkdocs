from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import unicodedata
from typing import TYPE_CHECKING, List

from mkdocs import utils
from mkdocs.config import base
from mkdocs.config import config_options as c
from mkdocs.contrib.search.search_index import ROOT_SHARD, SearchIndex
from mkdocs.plugins import BasePlugin

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.pages import Page
    from mkdocs.utils.templates import TemplateContext


log = logging.getLogger(__name__)
base_path = os.path.dirname(os.path.abspath(__file__))

#: Title of the synthetic shard that holds pages outside of top-level sections.
ROOT_SHARD_TITLE = 'Root'
#: Directory (under ``search/``) that contains the shard files and the manifest.
SHARDS_DIRNAME = 'shards'
#: Name of the shard manifest inside the shard directory.
MANIFEST_FILENAME = 'manifest.json'
#: Staging directory for a shard set that has not been published yet. Hidden, so
#: that it is never served as site content.
STAGING_PREFIX = '.shards.staging.'
#: A previously published shard set moved aside during an atomic directory swap.
BACKUP_PREFIX = '.shards.old.'


def slugify_shard(title: str) -> str:
    """
    Build a stable, file-system safe slug for a top-level chapter title.

    The slug only depends on the title, so a chapter keeps the same shard file
    across builds as long as it is not renamed. Titles without ASCII
    characters fall back to a stable hash of the title instead of a random
    value, so renames are still the only thing that can change the slug.
    """
    value = unicodedata.normalize('NFKD', str(title)).encode('ascii', 'ignore').decode('ascii')
    value = value.lower()
    value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
    if not value:
        value = hashlib.sha1(title.strip().encode('utf-8')).hexdigest()[:12]
    return value


class LangOption(c.OptionallyRequired[List[str]]):
    """Validate Language(s) provided in config are known languages."""

    def get_lunr_supported_lang(self, lang):
        fallback = {'uk': 'ru'}
        for lang_part in lang.split("_"):
            lang_part = lang_part.lower()
            lang_part = fallback.get(lang_part, lang_part)
            if os.path.isfile(os.path.join(base_path, 'lunr-language', f'lunr.{lang_part}.js')):
                return lang_part

    def run_validation(self, value: object):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise c.ValidationError('Expected a list of language codes.')
        for lang in value[:]:
            if lang != 'en':
                lang_detected = self.get_lunr_supported_lang(lang)
                if not lang_detected:
                    log.info(f"Option search.lang '{lang}' is not supported, falling back to 'en'")
                    value.remove(lang)
                    if 'en' not in value:
                        value.append('en')
                elif lang_detected != lang:
                    value.remove(lang)
                    value.append(lang_detected)
                    log.info(f"Option search.lang '{lang}' switched to '{lang_detected}'")
        return value


class _PluginConfig(base.Config):
    lang = c.Optional(LangOption())
    separator = c.Type(str, default=r'[\s\-]+')
    min_search_length = c.Type(int, default=3)
    prebuild_index = c.Choice((False, True, 'node', 'python'), default=False)
    indexing = c.Choice(('full', 'sections', 'titles'), default='full')
    shard_index = c.Type(bool, default=False)


def _write_file_atomic(content: bytes, output_path: str) -> None:
    """
    Write a file atomically: the destination either keeps its previous
    complete content or receives the new complete content, it can never
    observe a partially written file.
    """
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)
    # mkstemp creates files with 0o600 permissions; match the permissions a
    # regular open() would give the published file (respecting the umask).
    umask = os.umask(0o022)
    os.umask(umask)
    fd, tmp_path = tempfile.mkstemp(
        prefix='.' + os.path.basename(output_path) + '.', suffix='.tmp', dir=output_dir
    )
    try:
        os.fchmod(fd, 0o666 & ~umask)
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
        os.replace(tmp_path, output_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _sweep_leftovers(search_dir: str) -> None:
    """
    Remove staging directories and backup directories left behind by a build
    that was interrupted while rotating the shard directory.
    """
    if not os.path.isdir(search_dir):
        return
    for entry in os.listdir(search_dir):
        if entry.startswith((STAGING_PREFIX, BACKUP_PREFIX)):
            shutil.rmtree(os.path.join(search_dir, entry), ignore_errors=True)


def _unique_path(directory: str, prefix: str) -> str:
    """Return a non-existing path inside directory using prefix."""
    counter = 1
    while True:
        path = os.path.join(directory, f'{prefix}{os.getpid()}.{counter}')
        if not os.path.lexists(path):
            return path
        counter += 1


def _rotate_shards_directory(search_dir: str, staging_dir: str) -> None:
    """
    Publish staging_dir as ``search/shards`` atomically.

    The previously published directory is moved aside and deleted only once
    the new one is in place, so consumers always observe either the complete
    old set or the complete new set, never a mixture of both.
    """
    shards_dir = os.path.join(search_dir, SHARDS_DIRNAME)
    backup_path = None

    if os.path.lexists(shards_dir):
        if os.path.isdir(shards_dir) and not os.path.islink(shards_dir):
            backup_path = _unique_path(search_dir, BACKUP_PREFIX)
            os.rename(shards_dir, backup_path)
        else:
            # A stray file or symlink occupies the reserved shard location.
            if os.path.islink(shards_dir) or os.path.isfile(shards_dir):
                os.unlink(shards_dir)
            else:
                shutil.rmtree(shards_dir, ignore_errors=True)

    try:
        os.rename(staging_dir, shards_dir)
    except OSError:
        # Publishing the new set failed; restore the previous complete set
        # so that nobody is left without a shard directory.
        if backup_path is not None and not os.path.lexists(shards_dir):
            os.rename(backup_path, shards_dir)
            backup_path = None
        raise

    if backup_path is not None:
        shutil.rmtree(backup_path, ignore_errors=True)


def _retire_shards_directory(search_dir: str) -> None:
    """Remove a published shard set (e.g. when sharding was switched off)."""
    shards_dir = os.path.join(search_dir, SHARDS_DIRNAME)
    if not os.path.lexists(shards_dir):
        return
    backup_path = _unique_path(search_dir, BACKUP_PREFIX)
    os.rename(shards_dir, backup_path)
    shutil.rmtree(backup_path, ignore_errors=True)


class SearchPlugin(BasePlugin[_PluginConfig]):
    """Add a search feature to MkDocs."""

    def on_config(self, config: MkDocsConfig, **kwargs) -> MkDocsConfig:
        """Add plugin templates and scripts to config."""
        if config.theme.get('include_search_page'):
            config.theme.static_templates.add('search.html')
        if not config.theme.get('search_index_only'):
            path = os.path.join(base_path, 'templates')
            config.theme.dirs.append(path)
            if 'search/main.js' not in config.extra_javascript:
                config.extra_javascript.append('search/main.js')  # type: ignore
        if self.config.lang is None:
            # lang setting undefined. Set default based on theme locale
            validate = _PluginConfig.lang.run_validation
            self.config.lang = validate(config.theme.locale.language)
        # The `python` method of `prebuild_index` is pending deprecation as of version 1.2.
        # TODO: Raise a deprecation warning in a future release (1.3?).
        if self.config.prebuild_index == 'python':
            log.info(
                "The 'python' method of the search plugin's 'prebuild_index' config option "
                "is pending deprecation and will not be supported in a future release."
            )
        return config

    def on_pre_build(self, config: MkDocsConfig, **kwargs) -> None:
        """Create search index instance for later use."""
        self.search_index = SearchIndex(**self.config)
        # Stable slug allocation per top-level section, keyed by the nav
        # section object so identically titled chapters do not collapse.
        self._shard_slugs: dict[int, str] = {}
        self._shard_titles: dict[str, str] = {}
        self._used_slugs: set[str] = {ROOT_SHARD}

    def _shard_for_page(self, page: Page) -> str:
        """
        Return the stable shard slug of the top-level navigation section the
        page belongs to. Pages at the top level of the nav (and pages without
        a nav entry) fall back to the shared :data:`ROOT_SHARD`.
        """
        section = page.parent
        while section is not None and getattr(section.parent, 'is_section', False):
            section = section.parent
        if section is None or not section.is_section:
            return ROOT_SHARD

        key = id(section)
        slug = self._shard_slugs.get(key)
        if slug is None:
            base_slug = slugify_shard(section.title)
            slug = base_slug
            suffix = 2
            while slug in self._used_slugs:
                slug = f'{base_slug}-{suffix}'
                suffix += 1
            self._used_slugs.add(slug)
            self._shard_slugs[key] = slug
            self._shard_titles[slug] = section.title
        return slug

    def on_page_context(self, context: TemplateContext, page: Page, **kwargs) -> None:
        """Add page to search index."""
        shard = self._shard_for_page(page)
        self.search_index.add_entry_from_context(page, shard=shard)

    def _build_manifest(
        self, shard_entries: dict[str, list[dict]]
    ) -> tuple[dict[str, bytes], bytes]:
        """
        Render every shard and the manifest from the in-memory entries of this
        build round. The returned payloads are guaranteed to describe the
        same documentation set as the full index rendered alongside them.
        """
        shard_payloads: dict[str, bytes] = {}
        manifest_shards = []
        total_docs = 0

        for slug, entries in shard_entries.items():
            payload = self.search_index._generate_search_index(entries)
            shard_payloads[slug] = payload.encode('utf-8')
            locations = sorted({entry['location'].split('#', 1)[0] for entry in entries})
            manifest_shards.append(
                {
                    'slug': slug,
                    'title': self._shard_titles.get(slug, ROOT_SHARD_TITLE),
                    'location': f'{SHARDS_DIRNAME}/{slug}.json',
                    'docs': len(entries),
                    'locations': locations,
                }
            )
            total_docs += len(entries)

        manifest = {
            'config': dict(self.config),
            'index': 'search_index.json',
            'docs': total_docs,
            'shards': manifest_shards,
        }
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, separators=(',', ':'), default=str
        ).encode('utf-8')
        return shard_payloads, manifest_bytes

    def _publish_shards(
        self, output_base_path: str, shard_payloads: dict[str, bytes], manifest_bytes: bytes
    ) -> None:
        """
        Stage the complete shard set and publish it with an atomic directory
        swap, reconciling shard files from previous builds.
        """
        search_dir = output_base_path
        os.makedirs(search_dir, exist_ok=True)
        # Reconcile leftovers from interrupted builds before touching the
        # currently published set.
        _sweep_leftovers(search_dir)

        staging_dir = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=search_dir)
        try:
            for slug, payload in shard_payloads.items():
                # Stage every file first; an exception here leaves the
                # published shard directory completely untouched.
                utils.write_file(payload, os.path.join(staging_dir, f'{slug}.json'))
            utils.write_file(manifest_bytes, os.path.join(staging_dir, MANIFEST_FILENAME))
            _rotate_shards_directory(search_dir, staging_dir)
        except BaseException:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    def on_post_build(self, config: MkDocsConfig, **kwargs) -> None:
        """Build search index."""
        output_base_path = os.path.join(config.site_dir, 'search')

        # Render the full index and, when sharding is enabled, every shard and
        # the manifest from the same in-memory state, before anything is
        # written to disk, so that the manifest and the full index always
        # describe one and the same build round.
        search_index = self.search_index.generate_search_index()
        json_output_path = os.path.join(output_base_path, 'search_index.json')

        if self.config.shard_index:
            shard_entries = self.search_index.shard_entries()
            shard_payloads, manifest_bytes = self._build_manifest(shard_entries)
            self._publish_shards(output_base_path, shard_payloads, manifest_bytes)
            _write_file_atomic(search_index.encode('utf-8'), json_output_path)
        else:
            utils.write_file(search_index.encode('utf-8'), json_output_path)
            # Sharding was switched off (or the feature is unused); make sure
            # no shard set from a previous build survives alongside the full
            # index, which would leave mixed versions on disk.
            _sweep_leftovers(output_base_path)
            _retire_shards_directory(output_base_path)

        assert self.config.lang is not None
        if not config.theme.get('search_index_only'):
            # Include language support files in output. Copy them directly
            # so that only the needed files are included.
            files = []
            if len(self.config.lang) > 1 or 'en' not in self.config.lang:
                files.append('lunr.stemmer.support.js')
            if len(self.config.lang) > 1:
                files.append('lunr.multi.js')
            if 'ja' in self.config.lang or 'jp' in self.config.lang:
                files.append('tinyseg.js')
            for lang in self.config.lang:
                if lang != 'en':
                    files.append(f'lunr.{lang}.js')

            for filename in files:
                from_path = os.path.join(base_path, 'lunr-language', filename)
                to_path = os.path.join(output_base_path, filename)
                utils.copy_file(from_path, to_path)
