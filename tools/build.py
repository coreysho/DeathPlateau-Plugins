#!/usr/bin/env python3
"""Builds every plugin in this repo from source, and writes the index the client reads.

WHY THIS EXISTS. Until this, a plugin got into the index by someone building a jar on their own
machine and committing the binary. Nobody reviewing the repo could tell what was in it, and
nothing tied it to the source it came from. Worse, a client API change silently broke every
published jar: the client gained addPanel, the published xptracker.jar called it, and on an older
client that is a NoSuchMethodError that switches the whole plugin off. Nothing in this repo would
have noticed.

So a plugin is a MANIFEST naming a repository, a commit and a class - never a jar. This script
clones each one at its pinned commit, compiles it against the client named in client.version,
checks what it declares against what it built, and writes index.json. An API change that breaks a
plugin is now a build failure here, before a player ever sees it.

    python3 tools/build.py                  # build everything into out/
    python3 tools/build.py --check          # build and verify, write nothing
    python3 tools/build.py --plugin xptracker

Needs: git, javac/java/jar (8 or newer), and network access to GitHub.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGINS = os.path.join(ROOT, 'plugins')
OUT = os.path.join(ROOT, 'out')

CLIENT_REPO = 'coreysho/DeathPlateau-Client'
CLIENT_ASSET = 'client.jar'

# Where a built jar is served from. Every plugin is published as a release asset of this repo,
# tagged with the plugin's id and version, so a url never changes meaning once it exists.
RELEASE_URL = 'https://github.com/coreysho/DeathPlateau-Plugins/releases/download/%s-%s/%s.jar'

REQUIRED = ('name', 'version', 'repository', 'commit', 'sourceDir', 'pluginClass')

# The client is built for Java 8 (build.gradle says so), so a plugin must be too. This is not a
# detail: a class file newer than the JVM running it throws UnsupportedClassVersionError, the
# plugin never loads, and the player gets a jar that installed perfectly and does nothing. The
# first version of this script compiled with whatever javac it found - a JDK 21 writes class
# version 65 - and produced exactly that jar.
CLASS_TARGET = '8'
CLASS_VERSION = 52  # Java 8. Anything above this will not load in the client.


class Fail(Exception):
    """A problem with a plugin or with this repo, said in one sentence."""


def run(*cmd, **kwargs):
    r = subprocess.run(list(cmd), capture_output=True, text=True, **kwargs)
    return r


def read_properties(path):
    """A .properties file, as far as this repo uses one: key=value, # comments, no escapes."""
    values = {}
    with open(path, encoding='utf-8') as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('!'):
                continue
            if '=' not in line:
                raise Fail('%s line %d is not key=value: %r'
                           % (os.path.basename(path), number, line))
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def client_version():
    path = os.path.join(ROOT, 'client.version')
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if line and not line.startswith('#'):
            return line
    raise Fail('client.version names no client')


def download_client(version, into):
    """The client jar every plugin here compiles against, from its GitHub release."""
    if version == 'latest':
        api = 'https://api.github.com/repos/%s/releases/latest' % CLIENT_REPO
    else:
        api = 'https://api.github.com/repos/%s/releases/tags/%s' % (CLIENT_REPO, version)
    request = urllib.request.Request(api, headers={'Accept': 'application/vnd.github+json'})
    # A token is not needed - both repos are public - but CI has one, and an authenticated
    # request gets a rate limit that a busy runner will not trip over.
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        request.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            release = json.load(response)
    except Exception as error:
        raise Fail('could not read the %s release of %s: %s' % (version, CLIENT_REPO, error))

    for asset in release.get('assets', []):
        if asset.get('name') == CLIENT_ASSET:
            jar = os.path.join(into, CLIENT_ASSET)
            urllib.request.urlretrieve(asset['browser_download_url'], jar)
            return jar, release.get('tag_name', version)
    raise Fail('the %s release of %s has no %s' % (version, CLIENT_REPO, CLIENT_ASSET))


def client_api_level(java_home_bin, client_jar):
    """What PluginApi.LEVEL is in that client, read off the class file rather than run.

    A client from before API levels existed has no PluginApi at all, which reads as 0 - "this
    client makes no promises" - and every level check below then passes. That is correct: there
    is nothing to check against, and refusing to build would just stop the repo working on the
    client people are actually running.
    """
    javap = os.path.join(java_home_bin, 'javap') if java_home_bin else 'javap'
    r = run(javap, '-constants', '-cp', client_jar, 'jagex2.client.plugin.PluginApi')
    if r.returncode != 0:
        return 0, 'that client has no PluginApi, so it predates API levels'
    found = re.search(r'int\s+LEVEL\s*=\s*(\d+)', r.stdout)
    if not found:
        return 0, 'PluginApi is there but LEVEL could not be read'
    return int(found.group(1)), None


def checkout(repository, commit, into):
    """The named commit of the named repository, and nothing else.

    Fetched by sha with --depth 1 rather than cloning a branch, which is what makes the pin a pin:
    a branch moves, and "the commit the manifest names" is the only thing worth building.
    """
    os.makedirs(into)
    for cmd in (('git', 'init', '-q'),
                ('git', 'remote', 'add', 'origin', repository),
                ('git', 'fetch', '-q', '--depth', '1', 'origin', commit),
                ('git', 'checkout', '-q', 'FETCH_HEAD')):
        r = run(*cmd, cwd=into)
        if r.returncode != 0:
            raise Fail('%s failed: %s' % (' '.join(cmd[:2]), (r.stderr or r.stdout).strip()[:300]))
    r = run('git', 'rev-parse', 'HEAD', cwd=into)
    built = r.stdout.strip()
    if built != commit:
        raise Fail('asked for commit %s and got %s' % (commit, built))
    return built


def compile_plugin(java_bin, client_jar, source_root, plugin_class, work):
    """javac over the plugin's sources, against the client jar and nothing else.

    Deliberately not gradle, maven or anything else the submitter chooses. A plugin needs no
    dependencies - it compiles against one jar - so the build here is one javac invocation that
    this repo controls, over source it just checked out. That keeps a submission reviewable: a
    manifest and some .java files, with no build script of someone else's running on the runner.
    """
    if not os.path.isdir(source_root):
        raise Fail('sourceDir %s is not a directory in that commit' % source_root)
    sources = []
    for root, _dirs, files in os.walk(source_root):
        sources += [os.path.join(root, f) for f in files if f.endswith('.java')]
    if not sources:
        raise Fail('no .java files under sourceDir')

    classes = os.path.join(work, 'classes')
    os.makedirs(classes)
    javac = os.path.join(java_bin, 'javac') if java_bin else 'javac'
    # -g for the same reason gradle uses it for the client: when a plugin throws in someone's
    # game, the stack trace is all anyone has, and without it there are no line numbers or local
    # names in it. A few hundred bytes a class.
    common = ['-nowarn', '-g', '-encoding', 'UTF-8', '-cp', client_jar, '-d', classes]
    # --release is the one that gets this right, because it also swaps in the Java 8 API and so
    # refuses source that calls something newer. It arrived in JDK 9, hence the fallback.
    r = run(javac, '--release', CLASS_TARGET, *(common + sources))
    if r.returncode != 0 and 'release' in (r.stderr or ''):
        r = run(javac, '-source', CLASS_TARGET, '-target', CLASS_TARGET, *(common + sources))
    if r.returncode != 0:
        # THIS is the failure the whole script is for: a plugin whose source no longer compiles
        # against the client people are running. It used to be a player's problem.
        raise Fail('does not compile against this client:\n%s' % r.stderr.strip()[-1500:])

    expected = plugin_class.replace('.', '/') + '.class'
    if not os.path.isfile(os.path.join(classes, expected)):
        raise Fail('pluginClass %s is not in what that source built' % plugin_class)
    return classes, len(sources)


def class_version(data):
    """The major version in a .class file's header: bytes 6 and 7, big endian."""
    return data[6] * 256 + data[7]


def check_class_versions(jar):
    """Every class in the jar must load in the JVM the client runs on.

    Checked on the packed jar rather than trusted from the javac flags, because this is the
    failure that is invisible until a player hits it: the jar downloads, the checksum matches, it
    installs, and then nothing happens. Reading the header is three bytes and settles it.
    """
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            if not name.endswith('.class'):
                continue
            version = class_version(z.read(name))
            if version > CLASS_VERSION:
                raise Fail('%s is class version %d and the client runs Java %s (version %d) -'
                           ' it would not load' % (name, version, CLASS_TARGET, CLASS_VERSION))


def pack(classes, plugin_class, target):
    """The plugin's own classes and its inner classes, with the manifest the loader reads.

    Only this plugin's classes: the source directory may hold several plugins - the client repo's
    does - and a jar carrying another plugin's code would install the same class twice under two
    ids.
    """
    prefix = plugin_class.replace('.', '/')
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('META-INF/MANIFEST.MF',
                   'Manifest-Version: 1.0\nPlugin-Class: %s\n\n' % plugin_class)
        packed = 0
        for root, _dirs, files in os.walk(classes):
            for name in files:
                if not name.endswith('.class'):
                    continue
                full = os.path.join(root, name)
                entry = os.path.relpath(full, classes).replace(os.sep, '/')
                # "pkg/Thing.class" and "pkg/Thing$1.class", not "pkg/ThingElse.class".
                stem = entry[:-len('.class')]
                if stem == prefix or stem.startswith(prefix + '$'):
                    z.write(full, entry)
                    packed += 1
    if packed == 0:
        raise Fail('nothing to pack for %s' % plugin_class)
    return packed


def describe(java_bin, client_jar, tools_classes, jar, plugin_class):
    """What the built jar says about itself, read with the client's own annotation."""
    java = os.path.join(java_bin, 'java') if java_bin else 'java'
    r = run(java, '-cp', os.pathsep.join([client_jar, tools_classes]),
            'ReadDescriptor', jar, plugin_class)
    values = {}
    for line in r.stdout.split('\n'):
        if '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    if r.returncode != 0 or 'error' in values:
        raise Fail('could not read its descriptor: %s'
                   % values.get('error', (r.stderr or r.stdout).strip()[:300]))
    return values


def check_api_promise(built_api, declared_api):
    """Whether a manifest's clientApi covers what the jar it describes actually declares.

    Understating is the dangerous direction and the only one refused: the hub reads the manifest,
    so a manifest claiming less than the code needs is how a plugin gets offered to a client that
    cannot run it. Overstating is allowed, because a plugin can genuinely need a level its class
    does not declare - xptracker declares nothing and guards a newer call with catch(Throwable),
    which is the older way of handling this and still correct.
    """
    if built_api > declared_api:
        raise Fail('the jar declares apiLevel %d and the manifest says clientApi %d - the'
                   ' manifest must not promise less than the code needs'
                   % (built_api, declared_api))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 16), b''):
            digest.update(block)
    return digest.hexdigest()


def build_one(manifest_path, context):
    """One plugin, from its manifest to a jar and an index entry. Raises Fail with the reason."""
    plugin_id = os.path.basename(manifest_path)[:-len('.properties')]
    if not re.match(r'^[A-Za-z0-9_-]{1,40}$', plugin_id):
        raise Fail('the file name is not a usable plugin id - letters, digits, dash, underscore,'
                   ' up to 40 characters')

    values = read_properties(manifest_path)
    for key in REQUIRED:
        if not values.get(key):
            raise Fail('the manifest has no %s' % key)

    declared_api = values.get('clientApi', '0')
    if not re.match(r'^\d+$', declared_api):
        raise Fail('clientApi must be a whole number, not %r' % declared_api)
    declared_api = int(declared_api)
    if declared_api > context['client_api']:
        raise Fail('asks for plugin API %d and the %s client provides %d'
                   % (declared_api, context['client_tag'], context['client_api']))

    if not re.match(r'^https://', values['repository']):
        raise Fail('repository must be an https url')
    # A url of the submitter's own is allowed, and must still be https: the index's checksum is
    # of the jar THIS build made, so a url serving anything else makes the plugin uninstallable
    # rather than letting something unreviewed through - but plain http would let whoever can
    # rewrite the download rewrite the index too.
    if values.get('url') and not re.match(r'^https://', values['url']):
        raise Fail('url must be https, not %r' % values['url'])
    if not re.match(r'^[0-9a-f]{40}$', values['commit']):
        raise Fail('commit must be a full 40-character sha, not %r - a short one is ambiguous,'
                   ' and a branch name is not a pin' % values['commit'])

    work = tempfile.mkdtemp(prefix='plugin-' + plugin_id)
    try:
        source = os.path.join(work, 'source')
        checkout(values['repository'], values['commit'], source)

        source_root = os.path.join(source, values['sourceDir'])
        # The sourceDir comes out of a file in this repo, but it still may not reach outside the
        # checkout it is relative to.
        if not os.path.abspath(source_root).startswith(os.path.abspath(source) + os.sep):
            raise Fail('sourceDir climbs out of the checkout')

        classes, source_count = compile_plugin(
            context['java_bin'], context['client_jar'], source_root, values['pluginClass'], work)
        jar = os.path.join(context['jars'], plugin_id + '.jar')
        packed = pack(classes, values['pluginClass'], jar)
        check_class_versions(jar)

        described = describe(context['java_bin'], context['client_jar'],
                             context['tools_classes'], jar, values['pluginClass'])
        if described.get('isPlugin') != 'true':
            raise Fail('%s does not extend Plugin, so the client would never load it'
                       % values['pluginClass'])

        # The check that makes the manifest worth anything - see check_api_promise.
        built_api = int(described.get('apiLevel', '0') or '0')
        check_api_promise(built_api, declared_api)

        entry = {
            'id': plugin_id,
            'name': values['name'],
            'description': values.get('description', ''),
            'author': values.get('author', ''),
            'version': values['version'],
            'url': values.get('url') or RELEASE_URL % (plugin_id, values['version'], plugin_id),
            'sha256': sha256(jar),
        }
        if declared_api:
            entry['clientApi'] = declared_api
        if values.get('warning'):
            entry['warning'] = values['warning']

        note = '%d sources, %d classes, %d bytes' % (source_count, packed, os.path.getsize(jar))
        if built_api != declared_api:
            note += ', declares apiLevel %d (manifest says %d)' % (built_api, declared_api)
        return entry, note
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true',
                        help='build and verify everything, but write no index')
    parser.add_argument('--plugin', action='append', default=[],
                        help='only this plugin id (repeatable)')
    args = parser.parse_args()

    java_bin = os.path.join(os.environ['JAVA_HOME'], 'bin') if os.environ.get('JAVA_HOME') else ''
    for tool in ('javac', 'java', 'javap'):
        if not shutil.which(os.path.join(java_bin, tool) if java_bin else tool):
            raise SystemExit('build: needs %s on PATH (or a JAVA_HOME)' % tool)
    if not shutil.which('git'):
        raise SystemExit('build: needs git on PATH')

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    jars = os.path.join(OUT, 'jars')
    os.makedirs(jars)

    wanted = client_version()
    client_jar, client_tag = download_client(wanted, OUT)
    client_api, why = client_api_level(java_bin, client_jar)
    print('client %s (%s), plugin API %d%s'
          % (client_tag, '%d KB' % (os.path.getsize(client_jar) // 1024), client_api,
             ' - ' + why if why else ''))

    # The descriptor reader, compiled once against this client.
    tools_classes = os.path.join(OUT, 'tools-classes')
    os.makedirs(tools_classes)
    javac = os.path.join(java_bin, 'javac') if java_bin else 'javac'
    r = run(javac, '-nowarn', '-cp', client_jar, '-d', tools_classes,
            os.path.join(HERE, 'ReadDescriptor.java'))
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise SystemExit('build: tools/ReadDescriptor.java does not compile against that client')

    context = {
        'java_bin': java_bin, 'client_jar': client_jar, 'client_tag': client_tag,
        'client_api': client_api, 'jars': jars, 'tools_classes': tools_classes,
    }

    manifests = sorted(f for f in os.listdir(PLUGINS) if f.endswith('.properties'))
    if args.plugin:
        manifests = [f for f in manifests
                     if f[:-len('.properties')] in args.plugin]
        if not manifests:
            raise SystemExit('build: no manifest matches %s' % ', '.join(args.plugin))
    print('%d plugin%s' % (len(manifests), '' if len(manifests) == 1 else 's'))
    print()

    entries = []
    failed = []
    for name in manifests:
        plugin_id = name[:-len('.properties')]
        try:
            entry, note = build_one(os.path.join(PLUGINS, name), context)
            entries.append(entry)
            print('  ok   %-14s %s' % (plugin_id, note))
        except Fail as error:
            failed.append(plugin_id)
            print('  FAIL %-14s %s' % (plugin_id, error))

    print()
    if failed:
        # One bad plugin does not publish a half index: the client would read it as the whole
        # list and offer a plugin that is no longer there.
        print('%d of %d plugins failed: %s' % (len(failed), len(manifests), ', '.join(failed)))
        return 1

    index = {'plugins': entries}
    text = json.dumps(index, indent=2) + '\n'
    if args.check:
        print('every plugin builds and checks out; wrote nothing (--check)')
        return 0
    with open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8') as f:
        f.write(text)

    # What the workflow needs to publish, in the one shape a shell loop can read without parsing
    # JSON: id, version, jar. Writing it here rather than digging it back out of index.json in
    # yaml-inside-bash-inside-python keeps the workflow to a read loop.
    with open(os.path.join(OUT, 'built.tsv'), 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write('%s\t%s\t%s\n'
                    % (entry['id'], entry['version'], 'out/jars/%s.jar' % entry['id']))

    print('wrote out/index.json, out/built.tsv and %d jar%s under out/jars'
          % (len(entries), '' if len(entries) == 1 else 's'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Fail as error:
        raise SystemExit('build: %s' % error)
