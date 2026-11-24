"""Check tracked Markdown links/anchors and explicit package manifests, offline.

Supports inline, full/collapsed reference links, known shortcut references,
ATX/setext headings and HTML id/name anchors. Code blocks and inline code are
excluded from link checks. External URLs are deliberately not requested.
Manifests are statically read: application or packaging code is never imported.
"""
from __future__ import annotations

import ast
from html import unescape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import unicodedata
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PI = '树莓派心电终端'
CLOUD = '心电云端平台'


def without_fences(text: str) -> str:
    result, fence = [], None
    for line in text.splitlines():
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            if re.match(r'^ {0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*$', line):
                fence = None
            result.append('')
        elif mark:
            fence = mark[1]
            result.append('')
        elif line.startswith(('    ', '\t')):
            result.append('')
        else:
            result.append(line)
    return '\n'.join(result)


class HtmlAnchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        self.anchors.update(v for k, v in attrs if v and (k == 'id' or tag == 'a' and k == 'name'))


def anchors(text: str) -> set[str]:
    text = without_fences(text)
    html = HtmlAnchors()
    html.feed(text)
    result, counts = html.anchors, {}
    previous = ''
    for line in text.splitlines():
        match = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$', line)
        title = match[1] if match else previous if re.fullmatch(r' {0,3}(?:=+|-+)\s*', line) else None
        previous = line.strip()
        if title is None:
            continue
        title = re.sub(r'!?\[([^\]]+)\]\([^)]*\)', r'\1', title)
        title = unescape(re.sub(r'<[^>]+>', '', title)).replace('`', '').lower()
        slug = ''.join(c for c in title if c in '-_ ' or unicodedata.category(c)[0] in 'LNM')
        slug = slug.replace(' ', '-')
        base, number = slug, counts.get(slug, 0)
        while slug in result:
            number += 1
            slug = f'{base}-{number}'
        counts[base] = number
        result.add(slug)
    return result


def links(text: str):
    text = without_fences(text)
    text = re.sub(r'(`+)(.+?)\1', '', text)
    definitions = {}
    def normalize(label):
        return ' '.join(label.lower().split())
    def destination(value):
        value = value.strip()
        if value.startswith('<'):
            return value[1:value.index('>')]
        return re.split(r'\s', value, 1)[0]
    for match in re.finditer(r'^ {0,3}\[([^\]]+)\]:\s*(.+)$', text, re.M):
        definitions[normalize(match[1])] = destination(match[2])
    # Definitions are checked even if not yet referenced, to prevent stale maps.
    for value in definitions.values():
        yield value
    text = re.sub(r'^ {0,3}\[[^\]]+\]:.*$', '', text, flags=re.M)
    for match in re.finditer(r'!?\[([^\]\n]*)\](\(|\[([^\]\n]*)\])?', text):
        suffix = match[2]
        if suffix == '(':
            start, end, depth = match.end(), match.end(), 1
            while end < len(text) and depth:
                if text[end] == '\\':
                    end += 2
                    continue
                if text[end] == '(':
                    depth += 1
                elif text[end] == ')':
                    depth -= 1
                end += 1
            if depth == 0:
                yield re.sub(r'\\([()])', r'\1', destination(text[start:end - 1]))
        elif suffix:
            label = normalize(match[3] or match[1])
            if label not in definitions:
                yield 'missing-reference:' + label
        elif normalize(match[1]) in definitions:
            # Already checked the reference definition above.
            continue


def check_links(root: Path, names: set[str]) -> tuple[list[str], int]:
    errors, count, cache = [], 0, {}
    directories = {str(parent) for name in names for parent in PurePosixPath(name).parents}
    for name in sorted(n for n in names if n.lower().endswith('.md')):
        path = root / name
        if not path.is_file() or path.is_symlink():
            errors.append(f'{name}: missing or non-regular document')
            continue
        for href in links(path.read_text(encoding='utf-8')):
            if href.startswith('missing-reference:'):
                errors.append(f'{name}: {href}')
                continue
            try:
                url = urlsplit(href)
            except ValueError:
                errors.append(f'{name}: invalid link {href}')
                continue
            if url.scheme or url.netloc:
                continue
            count += 1
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path))) if url.path else name
            if target.startswith('/') or target == '..' or target.startswith('../') or '\\' in target:
                errors.append(f'{name}: link outside repository: {href}')
                continue
            file = root / target
            if target not in names and target not in directories:
                errors.append(f'{name}: untracked or missing target: {href}')
                continue
            if not file.exists() or not file.resolve().is_relative_to(root.resolve()) or file.is_symlink():
                errors.append(f'{name}: missing or non-regular target: {href}')
                continue
            if url.fragment:
                if target in directories:
                    target = target.rstrip('/') + '/README.md'
                    file = root / target
                if target not in names or file.suffix.lower() not in ('.md', '.html'):
                    errors.append(f'{name}: unsupported anchor target: {href}')
                    continue
                if target not in cache:
                    cache[target] = anchors(file.read_text(encoding='utf-8'))
                if unquote(url.fragment) not in cache[target]:
                    errors.append(f'{name}: missing anchor: {href}')
    return errors, count


def constant(node, env):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [constant(item, env) for item in node.elts]
        return set(values) if isinstance(node, ast.Set) else tuple(values)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp):
        left, right = constant(node.left, env), constant(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.BitOr):
            return left | right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'set' and len(node.args) == 1 and not node.keywords:
        return set(constant(node.args[0], env))
    raise ValueError('manifest is not a supported static expression')


def python_manifest(path: Path, target: str, env=None):
    env = dict(env or {})
    nodes = {}
    for statement in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(statement, ast.Assign):
            for name in statement.targets:
                if isinstance(name, ast.Name):
                    nodes[name.id] = statement.value
    def resolve(name):
        if name not in env:
            node = nodes[name]
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id in nodes and child.id != name:
                    resolve(child.id)
            env[name] = constant(node, env)
        return env[name]
    result = resolve(target)
    if not isinstance(result, (tuple, set)) or not all(isinstance(x, str) for x in result):
        raise ValueError('manifest must be a sequence of paths')
    return result, env


def check_members(root: Path, names: set[str], module: str, members) -> list[str]:
    errors = []
    if not isinstance(members, (list, tuple, set)) or not members:
        return ['manifest must contain paths']
    for member in members:
        if not isinstance(member, str) or not member or '\\' in member or ':' in member:
            errors.append('invalid manifest path')
            continue
        path = PurePosixPath(member)
        if path.is_absolute() or '..' in path.parts:
            errors.append(f'invalid manifest path: {member}')
            continue
        name = module + '/' + member
        file = root / name
        if name not in names or not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to((root / module).resolve()):
            errors.append(f'missing, untracked or non-regular package file: {name}')
    return errors


def check_packages(root: Path, names: set[str]) -> tuple[list[str], int]:
    import json
    errors, count = [], 0
    waveform = PI + '/tools/build_waveform_release.py'
    cardio = PI + '/tools/build_cardioedge_release.py'
    specs = [waveform, cardio, PI + '/tools/cloud_workspace_files.json', CLOUD + '/deploy/package_cloud.py']
    env = {}
    for source in specs:
        try:
            if source not in names:
                raise ValueError('manifest source is not tracked')
            if source == waveform:
                members, env = python_manifest(root / source, 'RELEASE_FILES')
            elif source == cardio:
                members, _ = python_manifest(root / source, 'FILES', env)
            elif source.endswith('.json'):
                members = json.loads((root / source).read_text(encoding='utf-8'))
            else:
                members, _ = python_manifest(root / source, 'MEMBERS')
            problems = check_members(root, names, source.split('/')[0], members)
            errors.extend(f'{source}: {problem}' for problem in problems)
            count += len(members)
        except (OSError, ValueError, KeyError, TypeError, SyntaxError, RecursionError) as exc:
            errors.append(f'{source}: cannot read static manifest ({type(exc).__name__})')
    return errors, count


def main() -> int:
    output = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
    names = {name.decode('utf-8') for name in output.split(b'\0') if name}
    errors, count = check_links(ROOT, names)
    package_errors, members = check_packages(ROOT, names)
    errors += package_errors
    for error in errors:
        print(error)
    print(f'Checked {count} local links and {members} package entries; {len(errors)} failures.')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
