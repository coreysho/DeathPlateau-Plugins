<div align="center">
    <h1>Death Plateau Plugins</h1>
</div>

Plugins for the [Death Plateau client](https://github.com/coreysho/DeathPlateau-Client), and the
index its plugin hub reads.

The client fetches `index.json` from this repo's `main` branch and shows what is in it under the
hub tab of its plugin sidebar. Pressing Install downloads the jar, checks it against the `sha256`
in the index, and puts it in the player's plugins folder.

**Nobody commits a jar here.** A plugin is a manifest in `plugins/` naming a repository, a commit
and a class. CI clones it at that commit, compiles it against the client, publishes the jar as a
release asset and writes `index.json`. See [How a plugin gets built](#how-a-plugin-gets-built).

## Installing a plugin

You do not need this repo for that. Open the client, go to the sidebar's second tab, press
Install.

## Publishing one

Add one file - `plugins/<id>.properties`:

```properties
name=My tool
description=What it does, in one line
author=You
version=1.0

# The lowest plugin API level your plugin needs. Leave it out if it needs nothing that was
# added - see "Client API levels" below.
clientApi=1

repository=https://github.com/you/my-tool.git
commit=0123456789abcdef0123456789abcdef01234567
sourceDir=src
pluginClass=com.example.MyToolPlugin
```

Open a pull request. CI builds it and says whether it compiles against the current client and
whether the manifest agrees with what it built; merging publishes it.

To check it yourself first:

```sh
python3 tools/build.py --plugin mytool
```

**The file name is the id, and the id is permanent.** It is the file the plugin installs as and
how the client knows an installed plugin is this plugin. Renaming one makes every install of it
an orphan. Letters, digits, dash and underscore, up to 40 characters.

**`commit` is a full 40-character sha, never a branch.** A branch moves, and then nobody - you
included - knows what is in the jar people installed.

**Updating** is a new `commit` and a new `version`. The version is compared as text, not ordered:
any different string offers an update. Each version gets a release of its own, so the url for a
version never changes meaning once it exists.

## How a plugin gets built

`.github/workflows/build.yml` runs `tools/build.py` on every push, every pull request, and once a
day. For each manifest it:

1. downloads the client named in `client.version` - `latest` by default, because every push to
   the client's `dev-logging` branch publishes a release and every launcher fetches the newest
   one, so the client players are running *is* latest
2. clones the plugin's repository at the pinned commit, and nothing else
3. compiles it with one `javac` against that client jar - no gradle, no maven, no build script of
   the submitter's running on the runner, because a plugin needs exactly one jar on its classpath
4. packs that plugin's own classes with the `Plugin-Class` manifest the loader reads
5. checks what the jar declares against what the manifest claims, and that every class in it is
   Java 8 - the client is, and a newer class file will not load
6. publishes the jar as a release asset, confirms the url serves exactly that jar, and commits
   the regenerated `index.json`

**The jars are reproducible.** Every zip entry is stamped 1980-01-01 and written in sorted order,
so building the same commit twice gives the same bytes. That is what makes the checksum in the
index a claim anyone can check: clone the commit the manifest names, run `tools/build.py`, compare.
It also means a re-run of an unchanged plugin is a no-op rather than a new artifact.

**A released version is immutable.** If a version is already published, the build compares what it
produces to what that release serves, and a difference fails: the remedy is a new version in the
manifest, not a replaced jar, because players have already seen the old checksum. The one exception
is a repackaging - identical code in a different archive - which needs someone to run the workflow
by hand with `repackage` set.

**`index.json` is generated.** Editing it by hand is pointless; the workflow overwrites it.

`jars/` holds the hand-built jars from before this, and the committed `index.json` still points at
them. The first run of the workflow on `main` publishes the releases and rewrites the index to
match, after which `jars/` is dead and can go.

### Why not just commit the jar

That is what this repo did first, and it had three problems. Nobody reviewing it could see what
was in a jar. Nothing tied a jar to the source it came from. And a client API change broke every
published jar silently - the client gained `addPanel`, the published `xptracker.jar` called it,
and on a client without it that is a `NoSuchMethodError` the manager answers by switching the
whole plugin off. A player saw a plugin install cleanly and then stop working.

All three are now a red build here instead of a broken plugin there. The daily run is what
catches the third: a client released today that breaks a plugin fails this build tomorrow.

## Client API levels

The client's plugin API has a level - `PluginApi.LEVEL` - which goes up when something is added
and never otherwise. A plugin says what it needs, in two places that must agree:

- `@PluginDescriptor(apiLevel = 1)` in the source, which the client checks before it constructs
  the plugin, and
- `clientApi=1` in the manifest here, which the hub checks before it offers the Install button

Leaving both out means "needs nothing that was added", which is true of most plugins and is never
refused. The build here requires the manifest to be at least what the jar declares: understating
it is the dangerous direction, because the hub would offer the plugin to someone who cannot run
it. Overstating is allowed - `xptracker` declares nothing in its source and guards a newer call
with `catch (Throwable)`, which is the older way of doing this and still works.

Say the level that added what you call, not the newest one going: `clientApi=2` on a plugin that
only needs level 1 locks out clients that would have run it perfectly.

### Using something the client might not have yet

A jar in this index is installed by whatever client the player happens to be running, which is
not always the newest one. If your plugin calls an API that arrived after their client was built,
the call does not resolve and throws `NoSuchMethodError` - and the manager's answer to a plugin
that throws while starting is to **switch the whole plugin off**. The player loses everything
your plugin does, over one feature they could not have seen anyway.

[Client API levels](#client-api-levels) is the answer to this: declare what you need and the hub
will not offer your plugin to a client that cannot run it. The older answer, which is still the
right one when a plugin can work without the newer feature, is to guard the call - and to catch
`Throwable` rather than `Exception`, because a missing method is an `Error` and the narrower
catch will not see it.

```java
protected void startUp() {
    this.addOverlay(this.overlay);       // as old as the API itself
    try {
        this.addPanel("Session xp", "chart", rows);   // newer; may not be there
    } catch (Throwable olderClient) {
        // No rail pages in this client. Everything above still works.
    }
}
```

The XP tracker does exactly this - see `plugins/xptracker.properties` for the commit its source
is pinned at - and is worth copying from.

## A note on what installing means

A plugin is ordinary Java running inside the client's process, with the access the client has.
There is no sandbox. The checksum in the index proves a download is the jar the index meant; it
proves nothing about what that jar does.

So what is in this repo is what people are trusting. Read what you merge.

## Hosting a jar elsewhere

A manifest may carry a `url=` of its own, and the generated index will use it instead of this
repo's release asset. It has to be https, and the `sha256` in the index is still the checksum of
the jar **this** build made - so a url serving anything else makes the plugin uninstallable
rather than letting something unreviewed through.

That is the only reason to use it: a plugin whose author publishes their own releases and wants
the download to come from there. Everything else is better served by the release this repo makes.

Prefer https everywhere. Anything that can rewrite an index served over plain http can rewrite
the checksums in it, and the client marks those plugins in the hub to say so.
