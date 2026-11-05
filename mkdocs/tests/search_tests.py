#!/usr/bin/env python

import json
import os
import unittest
from unittest import mock

from mkdocs.commands import build
from mkdocs.config.config_options import ValidationError
from mkdocs.contrib import search
from mkdocs.contrib.search import search_index
from mkdocs.structure.files import File, get_files
from mkdocs.structure.nav import get_navigation
from mkdocs.structure.pages import Page
from mkdocs.structure.toc import get_toc
from mkdocs.tests.base import PathAssertionMixin, dedent, get_markdown_toc, load_config, tempdir


def strip_whitespace(string):
    return string.replace("\n", "").replace(" ", "")


class SearchConfigTests(unittest.TestCase):
    def test_lang_default(self):
        option = search.LangOption(default=['en'])
        value = option.validate(None)
        self.assertEqual(['en'], value)

    def test_lang_str(self):
        option = search.LangOption()
        value = option.validate('en')
        self.assertEqual(['en'], value)

    def test_lang_list(self):
        option = search.LangOption()
        value = option.validate(['en'])
        self.assertEqual(['en'], value)

    def test_lang_multi_list(self):
        option = search.LangOption()
        value = option.validate(['en', 'es', 'fr'])
        self.assertEqual(['en', 'es', 'fr'], value)

    def test_lang_no_default_none(self):
        option = search.LangOption()
        value = option.validate(None)
        self.assertIsNone(value)

    def test_lang_no_default_str(self):
        option = search.LangOption(default=[])
        value = option.validate('en')
        self.assertEqual(['en'], value)

    def test_lang_no_default_list(self):
        option = search.LangOption(default=[])
        value = option.validate(['en'])
        self.assertEqual(['en'], value)

    def test_lang_bad_type(self):
        option = search.LangOption()
        with self.assertRaises(ValidationError):
            option.validate({})

    def test_lang_bad_code(self):
        option = search.LangOption()
        value = option.validate(['foo'])
        self.assertEqual(['en'], value)

    def test_lang_good_and_bad_code(self):
        option = search.LangOption()
        value = option.validate(['en', 'foo'])
        self.assertEqual(['en'], value)

    def test_lang_missing_and_with_territory(self):
        option = search.LangOption()
        value = option.validate(['cs_CZ', 'pt_BR', 'fr'])
        self.assertEqual(['fr', 'en', 'pt'], value)


class SearchPluginTests(unittest.TestCase):
    def test_plugin_config_defaults(self):
        expected = {
            'lang': None,
            'separator': r'[\s\-]+',
            'min_search_length': 3,
            'prebuild_index': False,
            'indexing': 'full',
            'shard_index': False,
        }
        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({})
        self.assertEqual(plugin.config, expected)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_plugin_config_lang(self):
        expected = {
            'lang': ['es'],
            'separator': r'[\s\-]+',
            'min_search_length': 3,
            'prebuild_index': False,
            'indexing': 'full',
            'shard_index': False,
        }
        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({'lang': 'es'})
        self.assertEqual(plugin.config, expected)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_plugin_config_separator(self):
        expected = {
            'lang': None,
            'separator': r'[\s\-\.]+',
            'min_search_length': 3,
            'prebuild_index': False,
            'indexing': 'full',
            'shard_index': False,
        }
        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({'separator': r'[\s\-\.]+'})
        self.assertEqual(plugin.config, expected)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_plugin_config_min_search_length(self):
        expected = {
            'lang': None,
            'separator': r'[\s\-]+',
            'min_search_length': 2,
            'prebuild_index': False,
            'indexing': 'full',
            'shard_index': False,
        }
        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({'min_search_length': 2})
        self.assertEqual(plugin.config, expected)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_plugin_config_prebuild_index(self):
        expected = {
            'lang': None,
            'separator': r'[\s\-]+',
            'min_search_length': 3,
            'prebuild_index': True,
            'indexing': 'full',
            'shard_index': False,
        }
        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({'prebuild_index': True})
        self.assertEqual(plugin.config, expected)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_plugin_config_indexing(self):
        expected = {
            'lang': None,
            'separator': r'[\s\-]+',
            'min_search_length': 3,
            'prebuild_index': False,
            'indexing': 'titles',
            'shard_index': False,
        }
        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({'indexing': 'titles'})
        self.assertEqual(plugin.config, expected)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_plugin_config_shard_index(self):
        expected = {
            'lang': None,
            'separator': r'[\s\-]+',
            'min_search_length': 3,
            'prebuild_index': False,
            'indexing': 'full',
            'shard_index': True,
        }
        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({'shard_index': True})
        self.assertEqual(plugin.config, expected)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_event_on_config_defaults(self):
        plugin = search.SearchPlugin()
        plugin.load_config({})
        result = plugin.on_config(load_config(theme='mkdocs', extra_javascript=[]))
        self.assertFalse(result['theme']['search_index_only'])
        self.assertFalse(result['theme']['include_search_page'])
        self.assertEqual(result['theme'].static_templates, {'404.html', 'sitemap.xml'})
        self.assertEqual(len(result['theme'].dirs), 3)
        self.assertEqual(result['extra_javascript'], ['search/main.js'])
        self.assertEqual(plugin.config.lang, [result['theme']['locale'].language])

    def test_event_on_config_lang(self):
        plugin = search.SearchPlugin()
        plugin.load_config({'lang': 'es'})
        result = plugin.on_config(load_config(theme='mkdocs', extra_javascript=[]))
        self.assertFalse(result['theme']['search_index_only'])
        self.assertFalse(result['theme']['include_search_page'])
        self.assertEqual(result['theme'].static_templates, {'404.html', 'sitemap.xml'})
        self.assertEqual(len(result['theme'].dirs), 3)
        self.assertEqual(result['extra_javascript'], ['search/main.js'])
        self.assertEqual(plugin.config.lang, ['es'])

    def test_event_on_config_theme_locale(self):
        plugin = search.SearchPlugin()
        plugin.load_config({})
        result = plugin.on_config(
            load_config(theme={'name': 'mkdocs', 'locale': 'fr'}, extra_javascript=[])
        )
        self.assertFalse(result['theme']['search_index_only'])
        self.assertFalse(result['theme']['include_search_page'])
        self.assertEqual(result['theme'].static_templates, {'404.html', 'sitemap.xml'})
        self.assertEqual(len(result['theme'].dirs), 3)
        self.assertEqual(result['extra_javascript'], ['search/main.js'])
        self.assertEqual(plugin.config.lang, [result['theme']['locale'].language])

    def test_event_on_config_include_search_page(self):
        plugin = search.SearchPlugin()
        plugin.load_config({})
        config = load_config(
            theme={'name': 'mkdocs', 'include_search_page': True}, extra_javascript=[]
        )
        result = plugin.on_config(config)
        self.assertFalse(result['theme']['search_index_only'])
        self.assertTrue(result['theme']['include_search_page'])
        self.assertEqual(
            result['theme'].static_templates, {'404.html', 'sitemap.xml', 'search.html'}
        )
        self.assertEqual(len(result['theme'].dirs), 3)
        self.assertEqual(result['extra_javascript'], ['search/main.js'])

    def test_event_on_config_search_index_only(self):
        plugin = search.SearchPlugin()
        plugin.load_config({})
        config = load_config(
            theme={'name': 'mkdocs', 'search_index_only': True}, extra_javascript=[]
        )
        result = plugin.on_config(config)
        self.assertTrue(result['theme']['search_index_only'])
        self.assertFalse(result['theme']['include_search_page'])
        self.assertEqual(result['theme'].static_templates, {'404.html', 'sitemap.xml'})
        self.assertEqual(len(result['theme'].dirs), 2)
        self.assertEqual(len(result['extra_javascript']), 0)

    @mock.patch('mkdocs.utils.write_file', autospec=True)
    @mock.patch('mkdocs.utils.copy_file', autospec=True)
    def test_event_on_post_build_defaults(self, mock_copy_file, mock_write_file):
        plugin = search.SearchPlugin()
        plugin.load_config({})
        config = load_config(theme='mkdocs')
        plugin.on_config(config)
        plugin.on_pre_build(config)
        plugin.on_post_build(config)
        self.assertEqual(mock_copy_file.call_count, 0)
        self.assertEqual(mock_write_file.call_count, 1)

    @mock.patch('mkdocs.utils.write_file', autospec=True)
    @mock.patch('mkdocs.utils.copy_file', autospec=True)
    def test_event_on_post_build_single_lang(self, mock_copy_file, mock_write_file):
        plugin = search.SearchPlugin()
        plugin.load_config({'lang': ['es']})
        config = load_config(theme='mkdocs')
        plugin.on_pre_build(config)
        plugin.on_post_build(config)
        self.assertEqual(mock_copy_file.call_count, 2)
        self.assertEqual(mock_write_file.call_count, 1)

    @mock.patch('mkdocs.utils.write_file', autospec=True)
    @mock.patch('mkdocs.utils.copy_file', autospec=True)
    def test_event_on_post_build_multi_lang(self, mock_copy_file, mock_write_file):
        plugin = search.SearchPlugin()
        plugin.load_config({'lang': ['es', 'fr']})
        config = load_config(theme='mkdocs')
        plugin.on_pre_build(config)
        plugin.on_post_build(config)
        self.assertEqual(mock_copy_file.call_count, 4)
        self.assertEqual(mock_write_file.call_count, 1)

    @mock.patch('mkdocs.utils.write_file', autospec=True)
    @mock.patch('mkdocs.utils.copy_file', autospec=True)
    def test_event_on_post_build_search_index_only(self, mock_copy_file, mock_write_file):
        plugin = search.SearchPlugin()
        plugin.load_config({'lang': ['es']})
        config = load_config(theme={'name': 'mkdocs', 'search_index_only': True})
        plugin.on_pre_build(config)
        plugin.on_post_build(config)
        self.assertEqual(mock_copy_file.call_count, 0)
        self.assertEqual(mock_write_file.call_count, 1)


class SearchIndexTests(unittest.TestCase):
    def test_html_stripping(self):
        stripper = search_index.ContentParser()

        stripper.feed("<h1>Testing</h1><p>Content</p>")

        self.assertEqual(stripper.stripped_html, "Testing\nContent")

    def test_content_parser(self):
        parser = search_index.ContentParser()

        parser.feed('<h1 id="title">Title</h1>TEST')
        parser.close()

        self.assertEqual(
            parser.data, [search_index.ContentSection(text=["TEST"], id_="title", title="Title")]
        )

    def test_content_parser_no_id(self):
        parser = search_index.ContentParser()

        parser.feed("<h1>Title</h1>TEST")
        parser.close()

        self.assertEqual(
            parser.data, [search_index.ContentSection(text=["TEST"], id_=None, title="Title")]
        )

    def test_content_parser_content_before_header(self):
        parser = search_index.ContentParser()

        parser.feed("Content Before H1 <h1>Title</h1>TEST")
        parser.close()

        self.assertEqual(
            parser.data, [search_index.ContentSection(text=["TEST"], id_=None, title="Title")]
        )

    def test_content_parser_no_sections(self):
        parser = search_index.ContentParser()

        parser.feed("No H1 or H2<span>Title</span>TEST")

        self.assertEqual(parser.data, [])

    def test_find_toc_by_id(self):
        """Test finding the relevant TOC item by the tag ID."""
        index = search_index.SearchIndex()

        md = dedent(
            """
            # Heading 1
            ## Heading 2
            ### Heading 3
            """
        )
        toc = get_toc(get_markdown_toc(md))

        toc_item = index._find_toc_by_id(toc, "heading-1")
        self.assertEqual(toc_item.url, "#heading-1")
        self.assertEqual(toc_item.title, "Heading 1")

        toc_item2 = index._find_toc_by_id(toc, "heading-2")
        self.assertEqual(toc_item2.url, "#heading-2")
        self.assertEqual(toc_item2.title, "Heading 2")

        toc_item3 = index._find_toc_by_id(toc, "heading-3")
        self.assertEqual(toc_item3.url, "#heading-3")
        self.assertEqual(toc_item3.title, "Heading 3")

    def test_create_search_index(self):
        html_content = """
        <h1 id="heading-1">Heading 1</h1>
        <p>Content 1</p>
        <h2 id="heading-2">Heading 2</h1>
        <p>Content 2</p>
        <h3 id="heading-3">Heading 3</h1>
        <p>Content 3</p>
        """

        base_cfg = load_config()
        pages = [
            Page(
                'Home',
                File('index.md', base_cfg.docs_dir, base_cfg.site_dir, base_cfg.use_directory_urls),
                base_cfg,
            ),
            Page(
                'About',
                File('about.md', base_cfg.docs_dir, base_cfg.site_dir, base_cfg.use_directory_urls),
                base_cfg,
            ),
        ]

        md = dedent(
            """
            # Heading 1
            ## Heading 2
            ### Heading 3
            """
        )
        toc = get_toc(get_markdown_toc(md))

        full_content = ''.join(f"Heading{i}Content{i}" for i in range(1, 4))

        plugin = search.SearchPlugin()
        errors, warnings = plugin.load_config({})

        for page in pages:
            # Fake page.read_source() and page.render()
            page.markdown = md
            page.toc = toc
            page.content = html_content

            index = search_index.SearchIndex(**plugin.config)
            index.add_entry_from_context(page)

            self.assertEqual(len(index._entries), 4)

            loc = page.url

            self.assertEqual(index._entries[0]['title'], page.title)
            self.assertEqual(strip_whitespace(index._entries[0]['text']), full_content)
            self.assertEqual(index._entries[0]['location'], loc)

            self.assertEqual(index._entries[1]['title'], "Heading 1")
            self.assertEqual(index._entries[1]['text'], "Content 1")
            self.assertEqual(index._entries[1]['location'], f"{loc}#heading-1")

            self.assertEqual(index._entries[2]['title'], "Heading 2")
            self.assertEqual(strip_whitespace(index._entries[2]['text']), "Content2")
            self.assertEqual(index._entries[2]['location'], f"{loc}#heading-2")

            self.assertEqual(index._entries[3]['title'], "Heading 3")
            self.assertEqual(strip_whitespace(index._entries[3]['text']), "Content3")
            self.assertEqual(index._entries[3]['location'], f"{loc}#heading-3")

    def test_search_indexing_options(self):
        def test_page(title, filename, config):
            test_page = Page(
                title,
                File(filename, config.docs_dir, config.site_dir, config.use_directory_urls),
                config,
            )
            test_page.content = """
                <h1 id="heading-1">Heading 1</h1>
                <p>Content 1</p>
                <h2 id="heading-2">Heading 2</h1>
                <p>Content 2</p>
                <h3 id="heading-3">Heading 3</h1>
                <p>Content 3</p>"""
            test_page.markdown = dedent(
                """
                # Heading 1
                ## Heading 2
                ### Heading 3"""
            )
            test_page.toc = get_toc(get_markdown_toc(test_page.markdown))
            return test_page

        def validate_full(data, page):
            self.assertEqual(len(data), 4)
            for x in data:
                self.assertTrue(x['title'])
                self.assertTrue(x['text'])

        def validate_sections(data, page):
            # Sanity
            self.assertEqual(len(data), 4)
            # Page
            self.assertEqual(data[0]['title'], page.title)
            self.assertFalse(data[0]['text'])
            # Headings
            for x in data[1:]:
                self.assertTrue(x['title'])
                self.assertFalse(x['text'])

        def validate_titles(data, page):
            # Sanity
            self.assertEqual(len(data), 1)
            for x in data:
                self.assertFalse(x['text'])

        for option, validate in {
            'full': validate_full,
            'sections': validate_sections,
            'titles': validate_titles,
        }.items():
            with self.subTest(option):
                plugin = search.SearchPlugin()

                # Load plugin config, overriding indexing for test case
                errors, warnings = plugin.load_config({'indexing': option})
                self.assertEqual(errors, [])
                self.assertEqual(warnings, [])

                base_cfg = load_config(plugins=['search'])
                base_cfg.plugins['search'].config.indexing = option

                pages = [
                    test_page('Home', 'index.md', base_cfg),
                    test_page('About', 'about.md', base_cfg),
                ]

                for page in pages:
                    index = search_index.SearchIndex(**plugin.config)
                    index.add_entry_from_context(page)
                    data = index.generate_search_index()
                    validate(json.loads(data)['docs'], page)

    @mock.patch('subprocess.Popen', autospec=True)
    def test_prebuild_index(self, mock_popen):
        # See https://stackoverflow.com/a/36501078/866026
        mock_popen.return_value = mock.Mock()
        mock_popen_obj = mock_popen.return_value
        mock_popen_obj.communicate.return_value = ('{"mock": "index"}', None)
        mock_popen_obj.returncode = 0

        index = search_index.SearchIndex(prebuild_index=True)
        expected = {
            'docs': [],
            'config': {'prebuild_index': True},
            'index': {'mock': 'index'},
        }
        result = json.loads(index.generate_search_index())
        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(mock_popen_obj.communicate.call_count, 1)
        self.assertEqual(result, expected)

    @mock.patch('subprocess.Popen', autospec=True)
    def test_prebuild_index_returns_error(self, mock_popen):
        # See https://stackoverflow.com/a/36501078/866026
        mock_popen.return_value = mock.Mock()
        mock_popen_obj = mock_popen.return_value
        mock_popen_obj.communicate.return_value = ('', 'Some Error')
        mock_popen_obj.returncode = 0

        index = search_index.SearchIndex(prebuild_index=True)
        expected = {
            'docs': [],
            'config': {'prebuild_index': True},
        }
        with self.assertLogs('mkdocs') as cm:
            result = json.loads(index.generate_search_index())
        self.assertEqual(
            '\n'.join(cm.output),
            'WARNING:mkdocs.contrib.search.search_index:Failed to pre-build search index. Error: Some Error',
        )

        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(mock_popen_obj.communicate.call_count, 1)
        self.assertEqual(result, expected)

    @mock.patch('subprocess.Popen', autospec=True)
    def test_prebuild_index_raises_ioerror(self, mock_popen):
        # See https://stackoverflow.com/a/36501078/866026
        mock_popen.return_value = mock.Mock()
        mock_popen_obj = mock_popen.return_value
        mock_popen_obj.communicate.side_effect = OSError
        mock_popen_obj.returncode = 1

        index = search_index.SearchIndex(prebuild_index=True)
        expected = {
            'docs': [],
            'config': {'prebuild_index': True},
        }
        with self.assertLogs('mkdocs') as cm:
            result = json.loads(index.generate_search_index())
        self.assertEqual(
            '\n'.join(cm.output),
            'WARNING:mkdocs.contrib.search.search_index:Failed to pre-build search index. Error: ',
        )

        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(mock_popen_obj.communicate.call_count, 1)
        self.assertEqual(result, expected)

    @mock.patch('subprocess.Popen', autospec=True, side_effect=OSError)
    def test_prebuild_index_raises_oserror(self, mock_popen):
        # See https://stackoverflow.com/a/36501078/866026
        mock_popen.return_value = mock.Mock()
        mock_popen_obj = mock_popen.return_value
        mock_popen_obj.communicate.return_value = ('foo', 'bar')
        mock_popen_obj.returncode = 0

        index = search_index.SearchIndex(prebuild_index=True)
        expected = {
            'docs': [],
            'config': {'prebuild_index': True},
        }
        with self.assertLogs('mkdocs') as cm:
            result = json.loads(index.generate_search_index())
        self.assertEqual(
            '\n'.join(cm.output),
            'WARNING:mkdocs.contrib.search.search_index:Failed to pre-build search index. Error: ',
        )

        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(mock_popen_obj.communicate.call_count, 0)
        self.assertEqual(result, expected)

    @mock.patch('subprocess.Popen', autospec=True)
    def test_prebuild_index_false(self, mock_popen):
        # See https://stackoverflow.com/a/36501078/866026
        mock_popen.return_value = mock.Mock()
        mock_popen_obj = mock_popen.return_value
        mock_popen_obj.communicate.return_value = ('', '')
        mock_popen_obj.returncode = 0

        index = search_index.SearchIndex(prebuild_index=False)
        expected = {
            'docs': [],
            'config': {'prebuild_index': False},
        }
        result = json.loads(index.generate_search_index())
        self.assertEqual(mock_popen.call_count, 0)
        self.assertEqual(mock_popen_obj.communicate.call_count, 0)
        self.assertEqual(result, expected)

    @unittest.skipUnless(search_index.haslunrpy, 'lunr.py is not installed')
    @mock.patch('mkdocs.contrib.search.search_index.lunr', autospec=True)
    def test_prebuild_index_python(self, mock_lunr):
        mock_lunr.return_value.serialize.return_value = {'mock': 'index'}
        index = search_index.SearchIndex(prebuild_index='python', lang='en')
        expected = {
            'docs': [],
            'config': {'prebuild_index': 'python', 'lang': 'en'},
            'index': {'mock': 'index'},
        }
        result = json.loads(index.generate_search_index())
        self.assertEqual(mock_lunr.call_count, 1)
        self.assertEqual(result, expected)

    @unittest.skipIf(search_index.haslunrpy, 'lunr.py is installed')
    def test_prebuild_index_python_missing_lunr(self):
        # When the lunr.py dependencies are not installed no prebuilt index is created.
        index = search_index.SearchIndex(prebuild_index='python', lang='en')
        expected = {
            'docs': [],
            'config': {'prebuild_index': 'python', 'lang': 'en'},
        }
        with self.assertLogs('mkdocs', level='WARNING'):
            result = json.loads(index.generate_search_index())
        self.assertEqual(result, expected)

    @mock.patch('subprocess.Popen', autospec=True)
    def test_prebuild_index_node(self, mock_popen):
        # See https://stackoverflow.com/a/36501078/866026
        mock_popen.return_value = mock.Mock()
        mock_popen_obj = mock_popen.return_value
        mock_popen_obj.communicate.return_value = ('{"mock": "index"}', None)
        mock_popen_obj.returncode = 0

        index = search_index.SearchIndex(prebuild_index='node')
        expected = {
            'docs': [],
            'config': {'prebuild_index': 'node'},
            'index': {'mock': 'index'},
        }
        result = json.loads(index.generate_search_index())
        self.assertEqual(mock_popen.call_count, 1)
        self.assertEqual(mock_popen_obj.communicate.call_count, 1)
        self.assertEqual(result, expected)


class ShardSlugTests(unittest.TestCase):
    def test_ascii_title(self):
        self.assertEqual(search.slugify_shard('User Guide'), 'user-guide')

    def test_accents_and_case(self):
        self.assertEqual(search.slugify_shard('Ünited States'), 'united-states')

    def test_punctuation_collapses_to_single_separator(self):
        self.assertEqual(search.slugify_shard('a---b / c!!!'), 'a-b-c')

    def test_non_ascii_falls_back_to_stable_hash(self):
        slug = search.slugify_shard('用户指南')
        # Stable across builds, 12 hex characters, file-system safe.
        self.assertEqual(slug, search.slugify_shard('用户指南'))
        self.assertRegex(slug, r'^[0-9a-f]{12}$')

    def test_hash_changes_when_title_changes(self):
        self.assertNotEqual(search.slugify_shard('用户指南'), search.slugify_shard('开发指南'))


class SearchIndexShardTests(unittest.TestCase):
    def _make_pages(self, base_cfg):
        html_content = """
        <h1 id="heading-1">Heading 1</h1>
        <p>Content 1</p>
        """
        md = dedent(
            """
            # Heading 1
            """
        )
        return [
            Page(
                'Home',
                File('index.md', base_cfg.docs_dir, base_cfg.site_dir, base_cfg.use_directory_urls),
                base_cfg,
            ),
            Page(
                'About',
                File('about.md', base_cfg.docs_dir, base_cfg.site_dir, base_cfg.use_directory_urls),
                base_cfg,
            ),
            html_content,
            md,
        ]

    def test_entries_default_to_root_shard(self):
        base_cfg = load_config()
        pages = self._make_pages(base_cfg)
        page1, page2, html_content, md = pages
        toc = get_toc(get_markdown_toc(md))
        plugin = search.SearchPlugin()
        plugin.load_config({})
        index = search_index.SearchIndex(**plugin.config)
        for page in (page1, page2):
            page.content = html_content
            page.toc = toc
            index.add_entry_from_context(page)
        self.assertEqual(index.shards, [search_index.ROOT_SHARD])
        self.assertEqual(
            sorted(index.shard_entries()), [search_index.ROOT_SHARD]
        )

    def test_entries_grouped_by_shard_and_rendered_separately(self):
        base_cfg = load_config()
        pages = self._make_pages(base_cfg)
        page1, page2, html_content, md = pages
        toc = get_toc(get_markdown_toc(md))

        plugin = search.SearchPlugin()
        plugin.load_config({})
        index = search_index.SearchIndex(**plugin.config)

        page1.content = html_content
        page1.toc = toc
        page2.content = html_content
        page2.toc = toc
        index.add_entry_from_context(page1, shard='alpha-guide')
        index.add_entry_from_context(page2, shard='beta-guide')

        shard_entries = index.shard_entries()
        self.assertEqual(list(shard_entries), ['alpha-guide', 'beta-guide'])
        self.assertEqual(len(shard_entries['alpha-guide']), 2)
        self.assertEqual(len(shard_entries['beta-guide']), 2)

        alpha = json.loads(index.generate_shard_search_index('alpha-guide'))
        beta = json.loads(index.generate_shard_search_index('beta-guide'))
        full = json.loads(index.generate_search_index())
        self.assertEqual({e['location'] for e in alpha['docs']}, {page1.url, f'{page1.url}#heading-1'})
        self.assertEqual({e['location'] for e in beta['docs']}, {page2.url, f'{page2.url}#heading-1'})
        self.assertEqual(len(full['docs']), 4)
        # Shard and full documents share the same config snapshot.
        self.assertEqual(alpha['config'], full['config'])
        self.assertEqual(beta['config'], full['config'])


class SearchShardPluginTests(PathAssertionMixin, unittest.TestCase):
    DOCS = {
        'index.md': '# Home\n\nwelcome home content',
        'alpha/a1.md': '# A1\n\nalpha one content',
        'alpha/a2.md': '# A2\n\nalpha two content',
        'beta/b1.md': '# B1\n\nbeta bananas content',
    }

    NAV = [
        {'Home': 'index.md'},
        {'Alpha Guide': [{'A1': 'alpha/a1.md'}, {'A2': 'alpha/a2.md'}]},
        {'Beta Guide': [{'B1': 'beta/b1.md'}]},
    ]

    def _run_build(self, docs_dir, site_dir, nav_cfg, options=None):
        cfg = load_config(
            docs_dir=docs_dir,
            site_dir=site_dir,
            nav=nav_cfg,
            theme='mkdocs',
        )
        files = get_files(cfg)
        nav = get_navigation(files, cfg)
        plugin = search.SearchPlugin()
        plugin.load_config(options if options is not None else {'shard_index': True})
        plugin.on_config(cfg)
        plugin.on_pre_build(cfg)
        for file in files.documentation_pages():
            page = file.page
            # Minimal rendered page: one heading without a matching TOC entry,
            # so every page contributes exactly one search index entry.
            page.content = '<h1 id="topic">Topic</h1><p>page body content</p>'
            page.toc = ()
            plugin.on_page_context({}, page=page, config=cfg, nav=nav)
        plugin.on_post_build(cfg)
        return plugin, cfg

    def _outputs(self, site_dir):
        search_dir = os.path.join(site_dir, 'search')
        shards_dir = os.path.join(search_dir, 'shards')
        with open(os.path.join(shards_dir, 'manifest.json'), encoding='utf-8') as f:
            manifest = json.load(f)
        shard_docs = {}
        for shard in manifest['shards']:
            with open(os.path.join(shards_dir, os.path.basename(shard['location'])), encoding='utf-8') as f:
                shard_docs[shard['slug']] = json.load(f)['docs']
        with open(os.path.join(search_dir, 'search_index.json'), encoding='utf-8') as f:
            full = json.load(f)
        return search_dir, shards_dir, manifest, shard_docs, full

    def _snapshot(self, search_dir):
        shards_dir = os.path.join(search_dir, 'shards')
        return (
            {name: open(os.path.join(shards_dir, name), 'rb').read() for name in os.listdir(shards_dir)},
            open(os.path.join(search_dir, 'search_index.json'), 'rb').read(),
        )

    @tempdir(files=DOCS)
    @tempdir()
    def test_shard_outputs_and_same_round_consistency(self, site_dir, docs_dir):
        self._run_build(docs_dir, site_dir, self.NAV)
        search_dir, shards_dir, manifest, shard_docs, full = self._outputs(site_dir)

        # The full index is still published unchanged for themes that use it.
        self.assertPathIsFile(search_dir, 'search_index.json')
        # Exactly the manifest and one shard per chapter, nothing stale.
        self.assertEqual(
            sorted(os.listdir(shards_dir)),
            ['alpha-guide.json', 'beta-guide.json', 'manifest.json', 'root.json'],
        )
        self.assertFalse(any(name.startswith('.') for name in os.listdir(search_dir)))

        # Manifest and full index describe the same build round.
        self.assertEqual(manifest['config'], full['config'])
        self.assertEqual(manifest['docs'], len(full['docs']))
        self.assertEqual(sum(s['docs'] for s in manifest['shards']), len(full['docs']))

        by_slug = {s['slug']: s for s in manifest['shards']}
        self.assertEqual(by_slug['alpha-guide']['title'], 'Alpha Guide')
        self.assertEqual(by_slug['beta-guide']['title'], 'Beta Guide')
        self.assertEqual(by_slug['root']['title'], 'Root')
        # The manifest points at files that actually exist.
        for shard in manifest['shards']:
            self.assertPathIsFile(shards_dir, os.path.basename(shard['location']))
            self.assertEqual(shard['docs'], len(shard_docs[shard['slug']]))

        # Pages are assigned to their top-level chapter.
        self.assertEqual(
            by_slug['alpha-guide']['locations'], ['alpha/a1/', 'alpha/a2/']
        )
        self.assertEqual(by_slug['beta-guide']['locations'], ['beta/b1/'])
        self.assertEqual(by_slug['root']['locations'], [''])
        for entry in shard_docs['alpha-guide']:
            self.assertTrue(entry['location'].startswith('alpha/'))
        for entry in shard_docs['beta-guide']:
            self.assertTrue(entry['location'].startswith('beta/'))

        # The shards partition the full index without overlap or loss.
        shard_locations = sorted(
            entry['location']
            for docs in shard_docs.values()
            for entry in docs
        )
        full_locations = sorted(entry['location'] for entry in full['docs'])
        self.assertEqual(shard_locations, full_locations)

    @tempdir(files=DOCS)
    @tempdir()
    def test_shard_slug_titles(self, site_dir, docs_dir):
        plugin, _ = self._run_build(docs_dir, site_dir, self.NAV)
        self.assertEqual(
            plugin._shard_titles,
            {'alpha-guide': 'Alpha Guide', 'beta-guide': 'Beta Guide'},
        )

    @tempdir(files=DOCS)
    @tempdir()
    def test_rename_delete_and_move_reconcile_old_shards(self, site_dir, docs_dir):
        self._run_build(docs_dir, site_dir, self.NAV)
        renamed_nav = [
            {'Home': 'index.md'},
            # Alpha chapter renamed; the Beta chapter deleted and its page
            # moved underneath the renamed chapter.
            {
                'Alpha Handbook': [
                    {'A1': 'alpha/a1.md'},
                    {'A2': 'alpha/a2.md'},
                    {'Moved B1': 'beta/b1.md'},
                ]
            },
        ]
        self._run_build(docs_dir, site_dir, renamed_nav)
        search_dir, shards_dir, manifest, shard_docs, full = self._outputs(site_dir)

        # Old chapter shards (rename + delete) are gone; no mixed versions.
        self.assertEqual(
            sorted(os.listdir(shards_dir)),
            ['alpha-handbook.json', 'manifest.json', 'root.json'],
        )
        self.assertFalse(any(name.startswith('.') for name in os.listdir(search_dir)))

        handbook = next(s for s in manifest['shards'] if s['slug'] == 'alpha-handbook')
        self.assertEqual(
            handbook['locations'], ['alpha/a1/', 'alpha/a2/', 'beta/b1/']
        )
        # The moved page exists only in the new chapter's shard.
        handbook_locations = [e['location'] for e in shard_docs['alpha-handbook']]
        self.assertTrue(any(loc.startswith('beta/b1') for loc in handbook_locations))
        self.assertEqual(
            [loc for loc in handbook_locations if loc.startswith('beta/b1')],
            ['beta/b1/'],
        )
        # The full index from the same round still lists the moved page once.
        self.assertEqual(
            sorted(e['location'] for e in full['docs']),
            ['', 'alpha/a1/', 'alpha/a2/', 'beta/b1/'],
        )
        self.assertEqual(manifest['docs'], len(full['docs']))

    @tempdir(files=DOCS)
    @tempdir()
    def test_repeated_builds_do_not_leave_mixed_versions(self, site_dir, docs_dir):
        self._run_build(docs_dir, site_dir, self.NAV)
        search_dir = os.path.join(site_dir, 'search')
        before_shards, before_full = self._snapshot(search_dir)
        self._run_build(docs_dir, site_dir, self.NAV)
        after_shards, after_full = self._snapshot(search_dir)

        self.assertEqual(set(before_shards), set(after_shards))
        self.assertEqual(before_shards, after_shards)
        self.assertEqual(before_full, after_full)
        self.assertFalse(
            any(name.startswith('.') for name in os.listdir(search_dir)),
            os.listdir(search_dir),
        )

    @tempdir(files=DOCS)
    @tempdir()
    def test_failed_generation_keeps_previous_index_set_intact(self, site_dir, docs_dir):
        _, cfg = self._run_build(docs_dir, site_dir, self.NAV)
        search_dir = os.path.join(site_dir, 'search')
        before_shards, before_full = self._snapshot(search_dir)

        files = get_files(cfg)
        nav = get_navigation(files, cfg)
        plugin = search.SearchPlugin()
        plugin.load_config({'shard_index': True})
        plugin.on_config(cfg)
        plugin.on_pre_build(cfg)
        for file in files.documentation_pages():
            page = file.page
            page.content = '<h1 id="topic">Topic</h1><p>page body content</p>'
            page.toc = ()
            plugin.on_page_context({}, page=page, config=cfg, nav=nav)

        with mock.patch.object(
            plugin.search_index, 'generate_search_index', side_effect=RuntimeError('boom')
        ):
            with self.assertRaises(RuntimeError):
                plugin.on_post_build(cfg)

        after_shards, after_full = self._snapshot(search_dir)
        self.assertEqual(after_shards, before_shards)
        self.assertEqual(after_full, before_full)
        self.assertFalse(any(name.startswith('.') for name in os.listdir(search_dir)))

    @tempdir(files=DOCS)
    @tempdir()
    def test_failed_staging_write_keeps_previous_index_set_intact(self, site_dir, docs_dir):
        _, cfg = self._run_build(docs_dir, site_dir, self.NAV)
        search_dir = os.path.join(site_dir, 'search')
        before_shards, _ = self._snapshot(search_dir)

        files = get_files(cfg)
        nav = get_navigation(files, cfg)
        plugin = search.SearchPlugin()
        plugin.load_config({'shard_index': True})
        plugin.on_config(cfg)
        plugin.on_pre_build(cfg)
        for file in files.documentation_pages():
            page = file.page
            page.content = '<h1 id="topic">Topic</h1><p>page body content</p>'
            page.toc = ()
            plugin.on_page_context({}, page=page, config=cfg, nav=nav)

        real_write_file = search.utils.write_file
        state = {'count': 0}

        def flaky_write_file(content, output_path):
            state['count'] += 1
            if state['count'] == 2:
                raise OSError('simulated staging failure')
            return real_write_file(content, output_path)

        with mock.patch.object(search.utils, 'write_file', flaky_write_file):
            with self.assertRaises(OSError):
                plugin.on_post_build(cfg)

        after_shards, _ = self._snapshot(search_dir)
        self.assertEqual(after_shards, before_shards)
        self.assertFalse(any(name.startswith('.') for name in os.listdir(search_dir)))

    @tempdir(files=DOCS)
    @tempdir()
    def test_disabling_sharding_retires_shard_directory(self, site_dir, docs_dir):
        self._run_build(docs_dir, site_dir, self.NAV)
        search_dir = os.path.join(site_dir, 'search')
        self.assertPathIsDir(search_dir, 'shards')

        self._run_build(docs_dir, site_dir, self.NAV, options={})
        self.assertPathNotExists(search_dir, 'shards')
        self.assertPathIsFile(search_dir, 'search_index.json')
        self.assertFalse(any(name.startswith('.') for name in os.listdir(search_dir)))

    @tempdir(files=DOCS)
    @tempdir()
    def test_identically_titled_chapters_get_distinct_stable_shards(self, site_dir, docs_dir):
        nav_cfg = [
            {'Home': 'index.md'},
            {'Guide': [{'A1': 'alpha/a1.md'}]},
            {'Guide': [{'B1': 'beta/b1.md'}]},
        ]
        self._run_build(docs_dir, site_dir, nav_cfg)
        shards_dir = os.path.join(site_dir, 'search', 'shards')
        first = set(os.listdir(shards_dir))
        self.assertIn('guide.json', first)
        self.assertIn('guide-2.json', first)

        # A repeated build allocates the same slugs; the assignment is stable.
        self._run_build(docs_dir, site_dir, nav_cfg)
        self.assertEqual(set(os.listdir(shards_dir)), first)

    @tempdir(files=DOCS)
    @tempdir()
    def test_non_ascii_chapter_title_gets_stable_shard(self, site_dir, docs_dir):
        nav_cfg = [
            {'Home': 'index.md'},
            {'指南': [{'A1': 'alpha/a1.md'}, {'A2': 'alpha/a2.md'}]},
        ]
        self._run_build(docs_dir, site_dir, nav_cfg)
        shards_dir = os.path.join(site_dir, 'search', 'shards')
        slug = search.slugify_shard('指南')
        self.assertPathIsFile(shards_dir, f'{slug}.json')
        first = set(os.listdir(shards_dir))
        self._run_build(docs_dir, site_dir, nav_cfg)
        self.assertEqual(set(os.listdir(shards_dir)), first)

    @tempdir(
        files={
            'index.md': '# Home\n\nhome content',
            'guide/install.md': '# Install\n\ninstall content',
            'about/license.md': '# License\n\nlicense content',
        }
    )
    @tempdir()
    def test_full_build_publishes_consistent_shard_set(self, site_dir, docs_dir):
        nav_cfg = [
            {'Home': 'index.md'},
            {'Guide': [{'Install': 'guide/install.md'}]},
            {'About': [{'License': 'about/license.md'}]},
        ]
        cfg = load_config(
            docs_dir=docs_dir,
            site_dir=site_dir,
            theme='mkdocs',
            plugins=[{'search': {'shard_index': True}}],
            nav=nav_cfg,
        )
        build.build(cfg)

        search_dir = os.path.join(site_dir, 'search')
        self.assertPathIsFile(search_dir, 'search_index.json')
        shards_dir = os.path.join(search_dir, 'shards')
        self.assertEqual(
            sorted(os.listdir(shards_dir)),
            ['about.json', 'guide.json', 'manifest.json', 'root.json'],
        )
        with open(os.path.join(shards_dir, 'manifest.json'), encoding='utf-8') as f:
            manifest = json.load(f)
        with open(os.path.join(search_dir, 'search_index.json'), encoding='utf-8') as f:
            full = json.load(f)
        self.assertEqual(manifest['config'], full['config'])
        self.assertEqual(manifest['docs'], len(full['docs']))
        self.assertEqual(sum(s['docs'] for s in manifest['shards']), len(full['docs']))
