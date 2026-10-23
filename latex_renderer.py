"""Convert article LaTeX to sanitized HTML and native MathML, without executing TeX."""
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess
import bleach

PANDOC = Path(os.environ.get('ORANGE_PANDOC', '/opt/orange-blog/tools/pandoc-3.12.1/bin/pandoc'))
MATH_TAGS = set('math semantics annotation mrow mi mn mo mtext mspace ms mfrac msqrt mroot mstyle merror mpadded mphantom mfenced menclose msub msup msubsup munder mover munderover mmultiscripts mprescripts none mtable mtr mtd maligngroup malignmark'.split())
HTML_TAGS = set('p br strong em b i u s del blockquote pre code h1 h2 h3 h4 h5 h6 ul ol li dl dt dd a img table thead tbody tfoot tr th td caption figure figcaption span div sup sub hr'.split())
ATTRIBUTES = {
    '*': ['id', 'class'], 'a': ['href', 'title', 'role'], 'img': ['src', 'alt', 'title'],
    'th': ['colspan', 'rowspan', 'scope'], 'td': ['colspan', 'rowspan'], 'ol': ['start'],
    'math': ['xmlns', 'display'], 'annotation': ['encoding'],
    'mo': ['stretchy', 'fence', 'separator', 'form', 'minsize', 'maxsize', 'largeop', 'movablelimits', 'accent', 'lspace', 'rspace'],
    'mi': ['mathvariant'], 'mtext': ['mathvariant'], 'mstyle': ['mathvariant', 'displaystyle', 'scriptlevel'],
    'mtable': ['columnalign', 'rowalign', 'columnspacing', 'rowspacing', 'columnlines', 'rowlines'],
    'mtd': ['columnalign', 'rowalign', 'columnspan', 'rowspan'], 'mtr': ['rowalign'],
    'mspace': ['width', 'height', 'depth'], 'mover': ['accent'], 'munder': ['accentunder'],
    'munderover': ['accent', 'accentunder'], 'mpadded': ['width', 'height', 'depth', 'lspace', 'voffset'],
}

class LatexRenderError(ValueError):
    pass

class Headings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings = []
        self.active = None
    def handle_starttag(self, tag, attrs):
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.active = [dict(attrs).get('id', ''), [], int(tag[1])]
    def handle_data(self, data):
        if self.active is not None:
            self.active[1].append(data)
    def handle_endtag(self, tag):
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'} and self.active:
            identifier, text, level = self.active
            if identifier:
                self.headings.append((identifier, ''.join(text), level))
            self.active = None

@lru_cache(maxsize=64)
def latex_bundle(source, number_sections=False):
    if len(source.encode('utf-8')) > 1024 * 1024:
        raise LatexRenderError('LaTeX 正文请控制在 1 MB 以内。')
    try:
        result = subprocess.run(
            [str(PANDOC), '--sandbox', '--from=latex', '--to=html5', '--mathml', '--shift-heading-level-by=1', '--wrap=none', '--fail-if-warnings'] + (['--number-sections'] if number_sections else []),
            # Layout-only PDF environment: consume its margins, retain its body.
            input=r'\newenvironment{adjustwidth}[2]{}{}' + '\n' + source,
            capture_output=True, text=True, encoding='utf-8', timeout=10,
            cwd=str(PANDOC.parent),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LatexRenderError('LaTeX 排版暂不可用或超时，请稍后预览重试。') from error
    if result.returncode:
        detail = result.stderr.strip()[:600]
        raise LatexRenderError('LaTeX 排版失败，请检查语法或不支持的命令。' + ('\n' + detail if detail else ''))
    html = bleach.clean(result.stdout, tags=HTML_TAGS | MATH_TAGS, attributes=ATTRIBUTES, protocols={'http', 'https', 'mailto'}, strip=True)
    parser = Headings()
    parser.feed(html)
    toc = '<div class="toc"><ul>' + ''.join('<li><a href="#' + escape(identifier, quote=True) + '"' + (' data-toc-level="' + str(level) + '"' if number_sections else '') + '>' + escape(text) + '</a></li>' for identifier, text, level in parser.headings) + '</ul></div>' if parser.headings else ''
    return html, toc
