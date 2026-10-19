"""Semantic named RTF styles around Pandoc's text/list/table conversion.

Pandoc owns Unicode escaping and document content. This adapter supplies the
stylesheet, page geometry, and paragraph/character styles lost by its HTML→RTF
writer. Formatting is materialized as well as named for RTF reader compatibility.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from app.core.i18n import language_scope, tr

VERSION = 'rtf-styles-v1'


@dataclass(frozen=True)
class Style:
    number: int
    name: str
    properties: str


# Sizes are half-points; spacing and margins are twips, matching the PDF CSS.
STYLES = {
    'body': Style(0, 'Textkörper', r'\ql\fs20\sa113'),
    'title': Style(1, 'Titel', r'\ql\b\fs46\cf2\sa397\sl288\slmult1\keepn\outlinelevel0'),
    'heading1': Style(2, 'Überschrift 1', r'\ql\b\fs28\cf3\sb454\sa113\keepn\outlinelevel1\brdrb\brdrs\brdrw10\brdrcf6\brsp80'),
    'heading2': Style(3, 'Überschrift 2', r'\ql\b\fs22\cf2\sb170\sa113\keepn\outlinelevel2'),
    'quote': Style(4, 'Zitat', r'\ql\li720\sa170\brdrl\brdrs\brdrw20\brdrcf6\brsp60'),
    'note': Style(5, 'Hinweis', r'\ql\fs18\cf4\sa113'),
    'source': Style(6, 'Quellenangabe', r'\ql\fs18\cf4\sb170\sa57\keepn'),
    'eyebrow': Style(7, 'Rubrik', r'\ql\b\fs18\cf3\sa113\keepn'),
    'list': Style(8, 'Listenpunkt', r'\ql\li360\fi-360\sa57'),
    'table_head': Style(9, 'Tabellenkopf', r'\ql\b\fs18\sa0\sl288\slmult1\keepn'),
    'table_body': Style(10, 'Tabelleninhalt', r'\ql\fs18\sa0\sl288\slmult1'),
    'sender': Style(11, 'Absender', r'\qr\fs18\sa340'),
    'recipient': Style(12, 'Empfänger', r'\ql\sa283'),
    'date': Style(13, 'Datum', r'\qr\sa283'),
    'subject': Style(14, 'Betreff', r'\ql\b\fs24\sa85\keepn'),
    'reference': Style(15, 'Referenz', r'\ql\fs18\sa340\keepn'),
    'letter': Style(16, 'Brieftext', r'\ql\sa198'),
    'signature': Style(17, 'Unterschrift', r'\ql\sb340\sa0'),
    'footer': Style(18, 'Fußzeile', r'\ql\fs16\cf4\sa0\sl240\slmult1'),
}
CHARACTER_STYLES = {
    'Strong': Style(30, 'Hervorhebung', r'\b'),
    'Emph': Style(31, 'Betont', r'\i'),
    'Code': Style(32, 'Code', r'\f1\fs18'),
}
_CLASS_ROLES = {'sender': 'sender', 'recipient': 'recipient', 'date': 'date',
                'subject': 'subject', 'reference': 'reference', 'signature': 'signature',
                'muted': 'note', 'source-label': 'source', 'eyebrow': 'eyebrow'}


class _SemanticHTML(HTMLParser):
    """Preserve HTML and attach paragraph roles as Div attributes for Pandoc."""
    def __init__(self, content: str):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.letter = bool(re.search(r'class=["\'][^"\']*\b(?:sender|subject)\b', content))
        self.language = 'ru'
        self.wrapped: list[str] = []
        self.feed(content)
        self.close()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'html':
            self.language = attributes.get('lang', 'ru')
        if tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            role = next((_CLASS_ROLES[c] for c in attributes.get('class', '').split()
                         if c in _CLASS_ROLES), None)
            if not role and self.letter and tag == 'p':
                role = 'letter'
            if role:
                self.parts.append(f'<div data-rtf-role="{role}">')
                self.wrapped.append(tag)
            else:
                self.wrapped.append('')
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        self.parts.append(f'</{tag}>')
        if tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'} and self.wrapped:
            if self.wrapped.pop():
                self.parts.append('</div>')

    def handle_startendtag(self, tag, attrs):
        self.parts.append(self.get_starttag_text())

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')

    def handle_decl(self, decl):
        self.parts.append('<!' + decl + '>')

    def handle_comment(self, data):
        self.parts.append('<!--' + data + '-->')


def _inlines(items: list, *, bold: bool = False) -> list:
    result = []
    for item in items:
        kind, content = item.get('t'), item.get('c')
        if kind == 'Strong' and bold:
            # An additive bold character style can toggle an already bold
            # paragraph back to regular weight in Word/LibreOffice.
            result.extend(_inlines(content, bold=True))
            continue
        if kind in {'Strong', 'Emph', 'Code'}:
            style = CHARACTER_STYLES[kind]
            children = ([{'t': 'Str', 'c': content[1]}] if kind == 'Code'
                        else _inlines(content, bold=bold or kind == 'Strong'))
            result += [{'t': 'RawInline', 'c': ['rtf', '{\\cs' + str(style.number)
                                               + style.properties + ' ']}]
            result += children + [{'t': 'RawInline', 'c': ['rtf', '}']}]
        elif kind == 'Note':
            result.append({**item, 'c': _blocks(content, 'note')})
        else:
            # Inline containers (Span, Link, …) can contain nested Strong/Emph.
            result.append(_nested_inlines(item, bold=bold))
    return result


def _nested_inlines(value, *, bold: bool = False):
    if isinstance(value, list):
        if value and all(isinstance(v, dict) and 't' in v for v in value):
            return _inlines(value, bold=bold)
        return [_nested_inlines(v, bold=bold) for v in value]
    if isinstance(value, dict):
        return {key: _nested_inlines(v, bold=bold) for key, v in value.items()}
    return value


def _rows(rows: list, role: str) -> None:
    for row in rows:
        for cell in row[1]:
            cell[4] = _blocks(cell[4], role)


def _blocks(blocks: list, inherited: str = 'body') -> list:
    for block in blocks:
        kind, content = block.get('t'), block.get('c')
        if kind in {'Para', 'Plain', 'Header'}:
            role = inherited
            if kind == 'Header':
                role = {1: 'title', 2: 'heading1'}.get(content[0], 'heading2')
            inlines = content[2] if kind == 'Header' else content
            marker = {'t': 'RawInline', 'c': ['rtf', '\\jsstyle' + str(STYLES[role].number) + ' ']}
            styled = [marker, *_inlines(inlines, bold=role in {
                'title', 'heading1', 'heading2', 'subject', 'table_head', 'eyebrow'})]
            if kind == 'Header':
                content[2] = styled
            else:
                block['c'] = styled
        elif kind == 'Div':
            role = dict(content[0][2]).get('rtf-role', inherited)
            content[1] = _blocks(content[1], role if role in STYLES else inherited)
        elif kind == 'BlockQuote':
            block['c'] = _blocks(content, 'quote')
        elif kind in {'BulletList', 'OrderedList'}:
            items = content[1] if kind == 'OrderedList' else content
            for item in items:
                _blocks(item, 'list')
        elif kind == 'Table':
            if 'overview' in content[0][1] and len(content[2]) == 6:
                for column, width in zip(content[2], (.08, .27, .20, .14, .09, .22), strict=True):
                    column[1] = {'t': 'ColWidth', 'c': width}
            _blocks(content[1][1], 'note')  # caption
            _rows(content[3][1], 'table_head')
            for body in content[4]:
                _rows(body[2], 'table_head')
                _rows(body[3], 'table_body')
            _rows(content[5][1], 'table_body')
        elif kind == 'DefinitionList':
            for term, definitions in content:
                term[:] = _inlines(term)
                for definition in definitions:
                    _blocks(definition, inherited)
    return blocks


def _escape(value: str) -> str:
    result = ''
    encoded = value.encode('utf-16-le')
    for offset in range(0, len(encoded), 2):
        code = int.from_bytes(encoded[offset:offset + 2], 'little')
        if code in (92, 123, 125):
            result += '\\' + chr(code)
        elif 32 <= code < 127:
            result += chr(code)
        else:
            result += '\\u' + str(code if code < 32768 else code - 65536) + '?'
    return result


def _properties(style: Style, letter: bool) -> str:
    base = r'\f0\fs21\cf1\sl336\slmult1' if letter else r'\f0\fs20\cf1\sl360\slmult1'
    return base + r'\ql\b0\i0\sb0\sa0\li0\fi0\widctlpar' + style.properties


def _document(body: str, letter: bool, language: str = 'ru') -> str:
    base = _properties(STYLES['body'], letter)
    sheet = '{\\stylesheet'
    for style in STYLES.values():
        sheet += '{\\s' + str(style.number) + _properties(style, letter)
        sheet += (r'\sbasedon0' if style.number else '')
        sheet += '\\snext' + ('16' if letter else '0') + ' ' + _escape(style.name) + ';}'
    for style in CHARACTER_STYLES.values():
        sheet += '{\\*\\cs' + str(style.number) + r'\additive' + style.properties + ' ' + style.name + ';}'
    sheet += '}'
    margin = '1247' if letter else '1020'
    header = (r'{\rtf1\ansi\deff0\uc1{\fonttbl{\f0\fswiss DejaVu Sans;}'
              r'{\f1\fmodern DejaVu Sans Mono;}}'
              r'{\colortbl;\red35\green51\blue68;\red20\green46\blue72;'
              r'\red8\green126\blue131;\red96\green112\blue128;'
              r'\red237\green244\blue247;\red200\green221\blue221;}' + sheet
              + r'\paperw11906\paperh16838\margl' + margin + r'\margr' + margin
              + r'\margt1134\margb1134\widowctrl\deftab720\hyphauto0\deflang'
              + ('1031' if letter or language == 'de' else '1049') + base + '\n')
    if not letter:
        footer = STYLES['footer']
        width = 11906 - int(margin) * 2
        header += ('{\\footer\\pard\\plain\\s18' + _properties(footer, False)
                   + '\\tqr\\tx' + str(width) + ' '
                   + _escape(tr('JOBSEEKER / ЛОКАЛЬНАЯ ОЦЕНКА'))
                   + r'\tab {\field{\*\fldinst PAGE}{\fldrslt 1}} / '
                   + r'{\field{\*\fldinst NUMPAGES}{\fldrslt 1}}\par}' + '\n')
    return header + body + '\n}'


def _apply_styles(body: str, letter: bool) -> str:
    # Older Pandoc versions emit unsigned UTF-16 surrogates. RTF specifies
    # signed 16-bit \u values; keep supplementary characters interoperable.
    body = re.sub(r'(?<!\\)\\u(\d+)',
                  lambda m: '\\u' + str(int(m[1]) - 65536) if 32767 < int(m[1]) <= 65535 else m[0],
                  body)
    by_id = {style.number: style for style in STYLES.values()}

    def paragraph(match):
        prefix, number = match['prefix'], int(match['number'])
        style = by_id[number]
        properties = _properties(style, letter)
        if style.name in {'Listenpunkt', 'Zitat'}:
            # Pandoc supplies nesting depth and ordered/bullet markers.
            properties += ''.join(re.findall(r'\\(?:li|fi)-?\d+', prefix))
        table = r'\intbl' if r'\intbl' in prefix else ''
        bullet = re.search(r'(?:\\bullet|[\w]+[.)])\s*\\tx\d+\\tab\s*', prefix)
        return '{\\pard\\plain\\s' + str(number) + properties + table + ' ' + (bullet[0] if bullet else '')

    body = re.sub(r'\{\\pard(?P<prefix>[^{}]*?)\\jsstyle(?P<number>\d+) ', paragraph, body)
    if r'\jsstyle' in body:
        raise ValueError('RTF paragraph styling failed; no dossier folder was published')
    # Pandoc adds spacer paragraphs around tables; assign the body style too.
    body = re.sub(r'\{\\pard(?!\\plain)',
                  lambda _: '{\\pard\\plain\\s0' + _properties(STYLES['body'], letter), body)
    # Pandoc's RTF tables default to 6 inches; fit them to the A4 text area.
    width = 11906 - (1247 if letter else 1020) * 2

    def row(match):
        value = match[0]
        # Pandoc terminates the last cell paragraph with \par before \cell,
        # creating a second, empty paragraph in Writer/Word. End it with \cell.
        value = re.sub(r'\\par}\s*\\cell}', lambda _: r'\cell}}', value)
        cells = [int(x) for x in re.findall(r'\\cellx(\d+)', value)]
        if cells:
            value = re.sub(r'\\cellx(\d+)',
                           lambda m: '\\cellx' + str(round(int(m[1]) * width / max(cells))), value)
        is_header = '\\s9\\' in value
        value = value.replace(r'\trgaph120', r'\trgaph120\trpaddt100\trpaddb100\trpaddft3\trpaddfb3', 1)
        value = value.replace(r'\clbrdrb\brdrs', '')
        value = re.sub(r'(?=\\cellx\d+)',
                       lambda _: r'\clbrdrb\brdrs\brdrw5\brdrcf6' + (r'\clcbpat5' if is_header else ''),
                       value)
        if is_header:
            value = value.replace(r'\trkeep', r'\trhdr\trkeep', 1)
        return value

    body = re.sub(r'^\{\n\\trowd .*?^\\intbl\\row\}', row, body, flags=re.M | re.S)
    return body


def render_rtf(content: str, pandoc: str, run) -> str:
    """Convert through Pandoc AST, then attach named, compatible RTF formatting."""
    source = _SemanticHTML(content)
    parsed = run([pandoc, '--from=html', '--to=json'],
                 input_bytes=''.join(source.parts).encode('utf-8'))
    try:
        document = json.loads(parsed.stdout)
    except (ValueError, TypeError):
        raise ValueError('Pandoc produced invalid document structure') from None
    _blocks(document['blocks'], 'letter' if source.letter else 'body')
    converted = run([pandoc, '--from=json', '--to=rtf', '--wrap=none'],
                    input_bytes=json.dumps(document, ensure_ascii=False).encode('utf-8'))
    with language_scope(source.language if source.language in {'ru', 'de'} else 'ru'):
        return _document(_apply_styles(converted.stdout.decode('utf-8'), source.letter),
                         source.letter, source.language)
