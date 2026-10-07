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
