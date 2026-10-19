"""Real Pandoc output preserves semantic styles, lists, tables and Unicode."""
import re
import shutil
import subprocess

import pytest

from app.matching.rtf_styles import STYLES, _escape
from app.matching.vacancy_documents import DocumentRenderer


@pytest.fixture
def renderer():
    pytest.importorskip('weasyprint')
    if not shutil.which('pandoc'):
        pytest.skip('Pandoc not installed')
    return DocumentRenderer()


def plain(path):
    return subprocess.run(['pandoc', '-f', 'rtf', '-t', 'plain', str(path)],
                          capture_output=True, text=True, check=True, timeout=30).stdout


def test_semantic_report_styles_and_content_survive_real_pandoc(renderer, tmp_path):
    content = '''<!doctype html><html lang="ru"><body>
    <p class="eyebrow">Обзор</p><h1>Заголовок</h1><h2>Раздел</h2><h3>Подраздел</h3>
    <p>Unicode Grüße, Привет, 中文, 😀; C++, C#, {скобки} и \\путь.</p>
    <p><strong>Важно</strong> <em>курсив</em> <code>x &lt; y</code></p>
    <p class="muted">Примечание</p><p class="source-label">Источник</p>
    <blockquote><p>Цитата</p></blockquote><ul><li>Docker</li><li>Kubernetes</li></ul>
    <ol><li>Первый</li><li>Второй</li></ol>
    <table><thead><tr><th>Навык</th><th>Балл</th></tr></thead>
    <tbody><tr><td>Python</td><td>80</td></tr></tbody></table><p>КОНЕЦ</p>
    </body></html>'''
    renderer.render(content, tmp_path / 'report', formats=('rtf',))
    path = tmp_path / 'report.rtf'
    rtf = path.read_text()
    assert rtf.startswith(r'{\rtf1') and r'{\stylesheet' in rtf
    for style in STYLES.values():
        assert _escape(style.name) + ';}' in rtf
    used = set(map(int, re.findall(r'\\pard\\plain\\s(\d+)\b', rtf)))
    assert {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 18} <= used
    assert not re.search(r'\\pard(?!\\plain\\s\d+)', rtf)
    assert r'\cs30\b' in rtf and r'\cs31\i' in rtf and r'\cs32\f1' in rtf
    assert r'\trhdr' in rtf and r'\cellx9866' in rtf
    assert r'\jsstyle' not in rtf
    # Pandoc's reader does not combine UTF-16 surrogate pairs; Writer does.
    assert r'\u-10179' in rtf and r'\u-8704' in rtf
    text = ' '.join(plain(path).split())
    for expected in ('Grüße, Привет, 中文', '{скобки} и \\путь.', 'Важно', 'курсив',
                     'x < y', 'Цитата', 'Docker', 'Kubernetes', 'Первый', 'Второй',
                     'Python', '80', 'КОНЕЦ'):
        assert expected in text


def test_letter_blocks_have_distinct_named_styles_and_no_redundant_bold_toggle(renderer, tmp_path):
    content = '''<!doctype html><html lang="de"><body>
    <p class="sender">Alex Beispiel</p><p class="recipient">Beispiel GmbH</p>
    <p class="date">18. Oktober 2026</p><p class="subject"><strong>Bewerbung</strong></p>
    <p class="reference">Referenz: 123</p><p>Sehr geehrte Damen und Herren,</p>
    <p>Brieftext mit <strong>Kenntnissen</strong>.</p>
    <p class="signature">Alex Beispiel</p></body></html>'''
    renderer.render(content, tmp_path / 'letter', formats=('rtf',))
    rtf = (tmp_path / 'letter.rtf').read_text()
    used = list(map(int, re.findall(r'\\pard\\plain\\s(\d+)\b', rtf)))
    assert used == [11, 12, 13, 14, 15, 16, 16, 17]
    subject = re.search(r'\\pard\\plain\\s14\b(.*?)\\par', rtf, re.S)[1]
    assert r'\b\fs24' in subject and r'\cs30' not in subject
    assert r'\cs30\b Kenntnissen' in rtf
    assert r'\margl1247\margr1247' in rtf and r'\footer' not in rtf
    assert _escape('Empfänger') in rtf


def test_overview_has_proportional_columns_fitted_to_page(renderer, tmp_path):
    cells = ''.join('<th>' + c + '</th>' for c in ('№', 'Вакансия', 'Фирма', 'Место', 'Балл', 'Статус'))
    content = '<html lang="ru"><body><table class="overview"><tr>' + cells + '</tr></table></body></html>'
    renderer.render(content, tmp_path / 'overview', formats=('rtf',))
    rtf = (tmp_path / 'overview.rtf').read_text()
    widths = list(map(int, re.findall(r'\\cellx(\d+)', rtf)))
    assert len(widths) == 6 and widths[-1] == 9866
    assert widths[1] - widths[0] > widths[0] * 2
