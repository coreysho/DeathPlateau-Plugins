<div align="center">
    <h1>Death Plateau Plugins</h1>
</div>

Plugins for the [Death Plateau client](https://github.com/coreysho/DeathPlateau-Client), and the
index its plugin hub reads.

The client fetches `index.json` from this repo's `main` branch and shows what is in it under the
hub tab of its plugin sidebar. Pressing Install downloads the jar, checks it against the `sha256`
in the index, and puts it in the player's plugins folder.

## Installing a plugin

You do not need this repo for that. Open the client, go to the sidebar's second tab, press
Install.

## Publishing one

1. Build the jar. A plugin is compiled against the client and needs nothing else - see
   [writing a plugin](https://github.com/coreysho/DeathPlateau-Client/blob/main/plugins/README.md).
2. Put it in `jars/`.
3. Add an entry to `index.json`:

```json
{
  "id": "mytool",
  "name": "My tool",
  "description": "What it does, in one line",
  "author": "You",
  "version": "1.0",
  "url": "https://raw.githubusercontent.com/coreysho/DeathPlateau-Plugins/main/jars/mytool.jar",
  "sha256": "..."
}
```

Get the checksum with `sha256sum jars/mytool.jar`, or `certutil -hashfile jars\mytool.jar SHA256`
on Windows.

4. Commit. Every client sees it the next time it opens the hub.

**`id` is permanent.** It is the file the plugin installs as and how the client knows an installed
plugin is this plugin. Renaming one makes every install of it an orphan.

**Updating** is a new jar, a new `version`, and a new `sha256`. The version is compared as text,
not ordered - any different string offers an update.

### Using something the client might not have yet

A jar in this index is installed by whatever client the player happens to be running, which is
not always the newest one. If your plugin calls an API that arrived after their client was built,
the call does not resolve and throws `NoSuchMethodError` - and the manager's answer to a plugin
that throws while starting is to **switch the whole plugin off**. The player loses everything
your plugin does, over one feature they could not have seen anyway.

So guard anything recent, and catch `Throwable` rather than `Exception`: a missing method is an
`Error`, and the narrower catch will not see it.

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

`jars/xptracker.jar` does exactly this, and is worth copying from.

## A note on what installing means

A plugin is ordinary Java running inside the client's process, with the access the client has.
There is no sandbox. The checksum in the index proves a download is the jar the index meant; it
proves nothing about what that jar does.

So what is in this repo is what people are trusting. Read what you merge.

## Hosting the jars elsewhere

`url` can point anywhere over http or https - release assets are the usual answer for anything
large, and their urls are stable. Jars live in `jars/` here because two small examples are not
worth a release each.

Prefer https. Anything that can rewrite an index served over plain http can rewrite the checksums
in it, and the client marks those plugins in the hub to say so.
