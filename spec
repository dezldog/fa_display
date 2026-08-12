Project: FlightAware Receiver Display

Purpose
- Build a standalone Linux Mint Cinnamon desktop app for local FlightAware receiver data.
- Display live aircraft tracking and receiver status in a native Python GTK UI.

Stack
- Python 3
- GTK 4 via PyGObject
- Local desktop application
- Later packaging: Flatpak or self-contained install

Data sources
- Receiver API endpoints:
  - http://flightaware.airwisp.net:8080/data/aircraft.json
  - http://flightaware.airwisp.net:8080/data/receiver.json
- Use JSON polling, not HTML scraping.

Core features (done)
- Simple scalable window
- Live aircraft list/table, sortable by clicking any column header
- Map view centered on receiver location, tiled from OpenStreetMap and cached
  locally in `tiles/`
- Receiver refresh interval control from `receiver.json`
- Handle missing aircraft fields safely (em-dash placeholder)

Later enhancements
- [done] Range rings and trail controls
- [done] Settings panel for location, receiver hostname/IP, overlays, and
        display preferences
- [ ] Minimize-to-tray
- [ ] Detailed selected-aircraft panel (selection currently just highlights
      the aircraft on the map and syncs with the table row)
- [ ] Packaging as Flatpak or self-contained app

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
    hex allocation block; PNGs fetched from flagcdn.com on demand and cached
    in `flags/` (`scripts/download_all_flags.py` can pre-fetch the full set)
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
- Not yet packaged; run directly with `python3 main.py` (requires PyGObject/
  GTK 4 system packages)


