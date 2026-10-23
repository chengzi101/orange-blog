"""Prepare a saved workspace snapshot for the existing article HTML renderer."""
import hashlib
import re
from pathlib import PurePosixPath
from latex_renderer import LatexRenderError, latex_bundle

LIMIT = 1024 * 1024
TOKEN = re.compile(
    r'%[^\n]*|'
    r'\\begin\{(?P<env>verbatim\*?|Verbatim|lstlisting|minted)\}.*?\\end\{(?P=env)\}|'
    r'\\(?P<verb>verb\*?|Verb\*?)(?P<opts>\[[^\]\n]*\])?(?P<sep>[^\w\s]).*?(?P=sep)|'
    r'\\includegraphics\*?\s*(?:\[[^\]\n]*\]\s*)?\{(?P<image>[^{}\n]+)\}|'
    r'\\(?:input|include)\s*\{(?P<path>[^{}\n]+)\}|\\.', re.S)


def workspace_article_source(files, main, *, assets=None, asset_url=None):
    """Resolve only supplied files; never ask Pandoc to read private disk files."""
    def image_reference(name):
        path = name.strip()
        pure = PurePosixPath(path)
        if pure.is_absolute() or '..' in pure.parts or '\\' in path or ':' in path:
            raise LatexRenderError('网页图片必须位于当前项目内：' + path)
        path = str(pure)
        if not pure.suffix:
            path = next((path + ext for ext in ('.png', '.jpg', '.jpeg') if path + ext in files), path)
        if path not in files:
            raise LatexRenderError('网页正文缺少图片文件：' + path)
        suffix = PurePosixPath(path).suffix.lower()
        raw = files[path]
        if not isinstance(raw, bytes) or not (
            suffix == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n') or
            suffix in {'.jpg', '.jpeg'} and raw.startswith(b'\xff\xd8\xff')
        ):
            raise LatexRenderError('网页图片请使用有效的 PNG 或 JPG 文件：' + path)
        extension, mime = ('.png', 'image/png') if suffix == '.png' else ('.jpg', 'image/jpeg')
        filename = 'latex-image-' + hashlib.sha256(raw).hexdigest() + extension
        assets[filename] = (path, mime, raw)
        return asset_url(filename) if asset_url else '/uploads/' + filename

    def expand(name, active):
        if name in active or len(active) >= 20:
            raise LatexRenderError('章节引用循环或嵌套过深。')
        if name not in files or not name.endswith('.tex'):
            raise LatexRenderError('网页正文缺少章节文件：' + name)
        raw = files[name]
        source = raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw
        parts = []; end = 0; size = 0
        for match in TOKEN.finditer(source):
            prefix = source[end:match.start()]
            text = match.group(0)
            if match.group('path') is not None:
                path = match.group('path').strip()
                pure = PurePosixPath(path)
                if pure.is_absolute() or '..' in pure.parts or '\\' in path or ':' in path:
                    raise LatexRenderError('章节引用必须位于当前项目内。')
                if not pure.suffix: path += '.tex'
                text = '\n' + expand(path, active + (name,)) + '\n'
            elif match.group('image') is not None and assets is not None:
                start, stop = match.span('image')
                text = source[match.start():start] + image_reference(match.group('image')) + source[stop:match.end()]
            elif match.group('verb') and match.group('verb').startswith('Verb'):
                sep = match.group('sep')
                value = text[text.index(sep) + 1:-1]
                text = '\\verb' + sep + value + sep
                if match.group('opts') and 'fontseries=b' in match.group('opts'):
                    text = '\\textbf{' + text + '}'
            size += len((prefix + text).encode('utf-8'))
            if size > LIMIT:
                raise LatexRenderError('网页正文请控制在 1 MB 以内。')
            parts.extend((prefix, text)); end = match.end()
        parts.append(source[end:])
        result = ''.join(parts)
        if len(result.encode('utf-8')) > LIMIT:
            raise LatexRenderError('网页正文请控制在 1 MB 以内。')
        return result

    source = expand(main, ())
    # Validate before changing the published project. HTML is sanitized by latex_bundle.
    latex_bundle(source)
    return source
