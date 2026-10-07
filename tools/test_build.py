#!/usr/bin/env python3
"""Tests for tools/build.py, aimed at what it REFUSES.

The happy path proves itself: if the build is green, every plugin in the repo compiled. What
needs testing is the other half - a manifest that lies, a commit that is a branch name, a jar
built for the wrong Java. Those are the checks that stop a bad plugin reaching a player, and a
check nobody tests is a check with a hole in it.

Each case below builds a real manifest in a copy of the repo and runs the real builder over it,
so what is being tested is the script as it ships. The client jar is downloaded once and reused.

    python3 tools/test_build.py [filter]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# A manifest that works, which every case below breaks in exactly one way.
GOOD = {
    'name': 'Coordinates',
    'description': 'Shows your world position in the corner of the viewport',
    'author': 'Corey',
    'version': '1.0',
    'repository': 'https://github.com/coreysho/DeathPlateau-Client.git',
    'commit': '48cd52f7e59f7bf4effa10455e4568e413d3474b',
    'sourceDir': 'plugins/src',
    'pluginClass': 'deathplateau.plugins.CoordinatesPlugin',
}

# (name, changes to GOOD, what the failure must mention). A change of None drops the key.
CASES = [
    ('a manifest with no version', {'version': None}, 'no version'),
    ('a manifest with no pluginClass', {'pluginClass': None}, 'no pluginClass'),
    ('a commit that is a branch name', {'commit': 'main'}, 'full 40-character sha'),
    ('a commit abbreviated to 7 characters', {'commit': '48cd52f'}, 'full 40-character sha'),
    ('a commit that does not exist',
     {'commit': '0123456789abcdef0123456789abcdef01234567'}, 'git fetch'),
    ('a repository served over plain http',
     {'repository': 'http://github.com/coreysho/DeathPlateau-Client.git'}, 'https'),
    ('a url of the submitter\'s own served over plain http',
     {'url': 'http://example.com/mytool.jar'}, 'url must be https'),
    ('a sourceDir that is not in that commit', {'sourceDir': 'nope/src'}, 'not a directory'),
    ('a sourceDir that climbs out of the checkout',
     {'sourceDir': '../../../etc'}, 'climbs out'),
    ('a pluginClass that the source does not build',
     {'pluginClass': 'deathplateau.plugins.NoSuchPlugin'}, 'is not in what that source built'),
    ('a clientApi that is not a number', {'clientApi': 'two'}, 'whole number'),
    # The check that makes the manifest worth anything: it may not promise less than the code
    # needs. The source here declares nothing, so anything positive is allowed - what is not
    # allowed is the other way round, which the apiLevel case below covers.
    ('a clientApi past what the client provides', {'clientApi': '9999'}, 'provides'),
]


def run(*cmd, **kwargs):
    return subprocess.run(list(cmd), capture_output=True, text=True, **kwargs)


def write_manifest(path, values):
    with open(path, 'w', encoding='utf-8') as f:
        for key, value in values.items():
            if value is not None:
                f.write('%s=%s\n' % (key, value))


class Repo(object):
    """A copy of this repo with only the plugins the test puts in it."""

    def __init__(self, work, client_jar=None):
        self.dir = os.path.join(work, 'repo')
        os.makedirs(os.path.join(self.dir, 'plugins'))
        shutil.copytree(HERE, os.path.join(self.dir, 'tools'))
        shutil.copy(os.path.join(ROOT, 'client.version'), self.dir)
        # The client jar is 700KB and the same every time: downloaded once by the first build and
        # handed to the rest, so twelve cases do not mean twelve downloads.
        if client_jar:
            os.makedirs(os.path.join(self.dir, 'out'))
            shutil.copy(client_jar, os.path.join(self.dir, 'out', 'client.jar'))

    def build(self, *args):
        return run(sys.executable, os.path.join(self.dir, 'tools', 'build.py'), *args,
                   cwd=self.dir)


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    fails = 0

    work = tempfile.mkdtemp(prefix='testbuild')
    try:
        # 1. The real repo builds. This also warms the client jar for everything below.
        print('1. the repo as it stands')
        r = run(sys.executable, os.path.join(HERE, 'build.py'), cwd=ROOT)
        ok = r.returncode == 0
        print(('  ok   ' if ok else 'FAIL   ') + 'every plugin in plugins/ builds and checks out')
        if not ok:
            print((r.stdout + r.stderr)[-2000:])
            fails += 1
            return 1
        built = [l for l in r.stdout.split('\n') if l.strip().startswith('ok')]
        print(('  ok   ' if len(built) >= 2 else 'FAIL   ')
              + '...all %d of them (%d)' % (len(built), len(built)))
        if len(built) < 2:
            fails += 1

        index = os.path.join(ROOT, 'out', 'index.json')
        ok = os.path.isfile(index)
        print(('  ok   ' if ok else 'FAIL   ') + 'an index is written')
        if not ok:
            fails += 1

        # Every class in every jar must be Java 8, which is what the client runs. This is the
        # bug the first version of this builder shipped: a JDK 21 writes class version 65 and
        # the jar installs perfectly and never loads.
        worst = 0
        for name in sorted(os.listdir(os.path.join(ROOT, 'out', 'jars'))):
            with zipfile.ZipFile(os.path.join(ROOT, 'out', 'jars', name)) as z:
                for member in z.namelist():
                    if member.endswith('.class'):
                        data = z.read(member)
                        worst = max(worst, data[6] * 256 + data[7])
        print(('  ok   ' if worst == 52 else 'FAIL   ')
              + 'every class in every jar is Java 8 (class version %d)' % worst)
        if worst != 52:
            fails += 1

        client_jar = os.path.join(ROOT, 'out', 'client.jar')

        # 2. One manifest, broken one way at a time.
        print()
        print('2. manifests it must refuse')
        cases = [c for c in CASES if not only or only in c[0]]
        for what, changes, expected in cases:
            values = dict(GOOD)
            values.update(changes)
            repo = Repo(work + os.sep + re.sub(r'\W+', '_', what), client_jar)
            write_manifest(os.path.join(repo.dir, 'plugins', 'mytool.properties'), values)
            r = repo.build()
            output = r.stdout + r.stderr
            refused = r.returncode != 0
            said = expected.lower() in output.lower()
            if refused and said:
                print('  ok   %s' % what)
            elif refused:
                print('FAIL   %s - refused, but for a reason that does not mention %r'
                      % (what, expected))
                print('       ' + ' | '.join(l.strip() for l in output.split('\n')
                                             if 'FAIL' in l or 'build:' in l)[:300])
                fails += 1
            else:
                print('FAIL   %s - WAS ACCEPTED' % what)
                fails += 1

        # 3. An id that is not usable as a file name. Checked here rather than in the table above
        # because the id is the file NAME, so it cannot be expressed as a manifest key.
        print()
        print('3. ids it must refuse')
        for bad, why in (('has space', 'an id with a space in it'),
                         ('dots.in.it', 'an id with dots in it'),
                         ('x' * 41, 'an id longer than 40 characters')):
            repo = Repo(work + os.sep + 'id' + re.sub(r'\W+', '_', bad)[:20], client_jar)
            write_manifest(os.path.join(repo.dir, 'plugins', bad + '.properties'), dict(GOOD))
            r = repo.build()
            output = r.stdout + r.stderr
            # "dots.in.it.properties" splits on the LAST dot, so the id reads as "dots.in.it" and
            # the dots are what must be refused.
            if r.returncode != 0 and 'usable plugin id' in output:
                print('  ok   %s is refused' % why)
            else:
                print('FAIL   %s was accepted' % why)
                fails += 1

        # 4. The rule that makes a manifest worth anything: it may not promise less than the
        # code needs. Driven as a function rather than through a built jar, because getting a
        # source that declares apiLevel 4 into a commit of somebody else's repository is not
        # something a test can do - and the rule IS the function; its call site is one line that
        # every build above exercises.
        print()
        print('4. a manifest may not promise less than its jar needs')
        sys.path.insert(0, HERE)
        import build
        for built, declared, allowed in ((0, 0, True), (0, 1, True), (1, 1, True), (4, 9, True),
                                         (1, 0, False), (2, 1, False), (4, 1, False)):
            try:
                build.check_api_promise(built, declared)
                got = True
            except build.Fail:
                got = False
            what = ('a jar declaring %d under a manifest saying %d is %s'
                    % (built, declared, 'allowed' if allowed else 'refused'))
            if got == allowed:
                print('  ok   ' + what)
            else:
                print('FAIL   ' + what + ' - it was not')
                fails += 1

        print()
        print('ALL PASS' if fails == 0 else '%d FAILED' % fails)
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
