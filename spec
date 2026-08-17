Project: FlightAware Receiver Display

Purpose
- Build a standalone Linux Mint Cinnamon desktop app for local FlightAware receiver data.
- Display live aircraft tracking and receiver status in a native Python GTK UI.

Stack
- Python 3
- GTK 4 via PyGObject
- Local desktop application
- Packaged as a Flatpak (`net.airwisp.FaDisplay`); see README.md

Data sources
- Receiver API endpoints:
  - http://flightaware.airwisp.net:8080/data/aircraft.json
  - http://flightaware.airwisp.net:8080/data/receiver.json
- Use JSON polling, not HTML scraping.

Core features (done)
- Simple scalable window
- Live aircraft list/table, sortable by clicking any column header
- Map view centered on receiver location, tiled from OpenStreetMap and cached
  locally in `$XDG_CACHE_HOME/fa_display/tiles/`
- Receiver refresh interval control from `receiver.json`
- Handle missing aircraft fields safely (em-dash placeholder)

Later enhancements
- [done] Range rings and trail controls
- [done] Settings panel for location, receiver hostname/IP, overlays, and
        display preferences
- [ ] Minimize-to-tray
- [ ] Detailed selected-aircraft panel (selection currently just highlights
      the aircraft on the map and syncs with the table row)
- [done] Packaging as a Flatpak (see "Packaging" below)

Current implementation
- `main.py`: GTK 4 app, single window, polls receiver.json/aircraft.json on
  the interval reported by the receiver
  - Aircraft table (Gtk.ListView/Gio.ListStore): ICAO, Ident, Flag, Tail,
    Type, Squawk, Alt, Speed, Dist, Heading, Msgs, Lat, Lon; click a header to
    sort, click a row or a map marker to select (selection is synced both
    ways)
  - Map: OSM raster tiles with local PNG cache, manual zoom (+/-/reset) with
    auto-fit fallback, range rings at 5/15/25/35/45 NM, aircraft markers using
    the same SVG icon set and altitude-based HSL coloring as the receiver's
    own SkyAware UI (`aircraft_icons.py`), MLAT/special-squawk outline colors,
    an icon-shape legend and an altitude color legend, and optional flight
    trails (toggle button) built up client-side across polls
  - Registration/type lookup: fills in tail number and type designator by
    querying the receiver's own chunked hex-prefix DB (`db/*.json`), falling
    back to a local port of the ICAO hex→N-number/registration decoder
    (ported from `registrations.js`, kept in the repo for reference)
  - Country flags: inferred from country code, airline ident prefix, or ICAO
    hex allocation block. The bundled `flags/` directory (read-only,
    resolved relative to the app's install location, not the working
    directory) ships the full ISO set pre-fetched via
    `scripts/download_all_flags.py`; any code not found there is fetched
    from flagcdn.com on demand and cached in
    `$XDG_CACHE_HOME/fa_display/flags/`
  - Settings dialog (toolbar "Settings" button): receiver hostname/port
    (rebuilds the receiver/aircraft/hex-DB URLs and re-polls on save);
    optional override of the receiver-reported lat/lon used to center the
    map and compute distances; overlay toggles for range rings, default
    flight-trail state, icon legend, and altitude legend; display
    preferences for distance unit (NM/km/mi, also changes the range-ring
    steps), altitude unit (ft/m), and max aircraft rows shown in the table.
    Persisted as JSON to `$XDG_CONFIG_HOME/fa_display/settings.json`
    (normally `~/.config/fa_display/settings.json`) and applied immediately
    without restarting the app.
- Run from source with `python3 main.py` (requires PyGObject/GTK 4 system
  packages), or install the Flatpak (see "Packaging" below)

Packaging
- Application ID: `net.airwisp.FaDisplay` (reverse-DNS of the receiver's
  domain, which is owned by the project maintainer)
- Flatpak manifest: `net.airwisp.FaDisplay.yml`, targeting the
  `org.gnome.Platform`/`org.gnome.Sdk` runtime (bundles GTK4, Pango,
  GdkPixbuf, PyGObject, and pycairo, so no extra build modules are needed)
- Also ships `net.airwisp.FaDisplay.desktop` (launcher entry),
  `net.airwisp.FaDisplay.metainfo.xml` (AppStream metadata; license field
  is a placeholder, not yet meant for Flathub), and `icons/` (a
  radar-plane icon at 64/96/128px + @2x + scalable SVG, installed into the
  hicolor icon theme by the manifest)
- `finish-args` grant network access (receiver polling, OSM tiles,
  flagcdn.com) and display/GPU sockets; no explicit filesystem
  permissions are needed since settings/cache already use
  `GLib.get_user_config_dir()`/`get_user_cache_dir()`, which Flatpak
  sandboxes per-app automatically
- Build/run instructions are in README.md; verified end-to-end with a real
  `flatpak-builder` build, install, and run (tile fetching over the
  network worked correctly from inside the sandbox)


