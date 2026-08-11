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

Core features
- Simple scalable window
- Live aircraft list/table
- Basic map view centered on receiver location
- Receiver refresh interval control from `receiver.json`
- Handle missing aircraft fields safely

Later enhancements
- Minimize-to-tray
- Range rings and trail controls
- Settings panel for overlays and display preferences
- Detailed selected-aircraft panel
- Packaging as Flatpak or self-contained app

Current implementation
- Prototype created in `main.py`
- Uses GTK 4 and polls the local FlightAware JSON API
- Displays status, aircraft rows, and a simple map render


