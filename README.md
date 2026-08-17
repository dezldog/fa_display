# FlightAware Desktop Display

A native GTK4 desktop app that polls a local FlightAware/PiAware receiver's
`aircraft.json` and `receiver.json` and displays live traffic on a sortable
table and an OpenStreetMap-tiled map, matching the receiver's own SkyAware
coloring and iconography.

## Running from source

Requires Python 3 and the GTK4 PyGObject bindings as system packages (not
pip-installable):

```
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0
python3 main.py
```

Settings are stored at `$XDG_CONFIG_HOME/fa_display/settings.json` (normally
`~/.config/fa_display/settings.json`); the map tile cache and any
non-bundled flag downloads are cached under
`$XDG_CACHE_HOME/fa_display/` (normally `~/.cache/fa_display/`).

## Building the Flatpak

Requires `flatpak-builder` and the `org.gnome.Sdk`/`org.gnome.Platform`
runtime (version `49`, matching `net.airwisp.FaDisplay.yml`):

```
sudo apt install flatpak-builder
flatpak install flathub org.gnome.Sdk//49 org.gnome.Platform//49
```

Build and install locally:

```
flatpak-builder --user --install --force-clean builddir net.airwisp.FaDisplay.yml
```

Run it:

```
flatpak run net.airwisp.FaDisplay
```

Rebuild after making changes by re-running the `flatpak-builder` command
above.
