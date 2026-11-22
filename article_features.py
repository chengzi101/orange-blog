"""Literal C directives and explicit article covers."""
import re
import sqlite3
from html import escape

from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor


class CDirectiveLiterals(Preprocessor):
    directive = re.compile(r'^[ \t]*#[ \t]*(?:include|include_next|define|undef|if|ifdef|ifndef|elif|else|endif|error|warning|pragma|line)\b')

    def run(self, lines):
        # Fenced code has already been stashed by Markdown at priority 25.
        return [self.md.htmlStash.store('<code>' + escape(line, quote=False) + '</code>')
                if self.directive.match(line) else line for line in lines]


class ArticleMarkdown(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.register(CDirectiveLiterals(md), 'c-directive-literals', 24)


def init_cover_schema(db):
    for table in ('posts', 'post_revisions'):
        if 'cover_position' not in {row['name'] for row in db.execute('PRAGMA table_info(' + table + ')')}:
            try:
                db.execute('ALTER TABLE ' + table + " ADD COLUMN cover_position TEXT NOT NULL DEFAULT '50% 50%'")
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    raise
        if 'cover_image' not in {row['name'] for row in db.execute('PRAGMA table_info(' + table + ')')}:
            try:
                db.execute('ALTER TABLE ' + table + " ADD COLUMN cover_image TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    raise


def valid_cover(value):
    return isinstance(value, str) and (value in ('', 'none') or bool(re.fullmatch(
        r'/uploads/[A-Za-z0-9._-]{1,240}\.(?:png|jpe?g|gif|webp)', value, re.I)))


def post_cover(post, automatic):
    try:
        selected = post['cover_image']
    except (KeyError, IndexError, TypeError):
        selected = getattr(post, 'cover_image', '')
    if selected == 'none':
        return ''
    return selected if selected and valid_cover(selected) else automatic(post['content'] if hasattr(post, 'keys') else post.content)


def valid_cover_position(value):
    return isinstance(value, str) and bool(re.fullmatch(r'(?:100|[1-9]?[0-9])% (?:100|[1-9]?[0-9])%', value))


def cover_position(post):
    try:
        value = post['cover_position']
    except (KeyError, IndexError, TypeError):
        value = getattr(post, 'cover_position', '')
    return value if valid_cover_position(value) else '50% 50%'
