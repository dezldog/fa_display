#!/usr/bin/env python3
import cairo
import colorsys
import json
import math
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import gi
import os
import base64

from aircraft_icons import AIRCRAFT_SHAPES, CATEGORY_ICONS

gi.require_version('Gtk', '4.0')
gi.require_version('GLib', '2.0')
gi.require_version('Gio', '2.0')
gi.require_version('Pango', '1.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gio, GLib, Gtk, Pango
from gi.repository import Gdk, GdkPixbuf, GObject

RECEIVER_URL = 'http://flightaware.airwisp.net:8080/data/receiver.json'
AIRCRAFT_URL = 'http://flightaware.airwisp.net:8080/data/aircraft.json'
# Chunked ICAO hex -> {registration, type designator} database the receiver serves
# for its own SkyAware map (see dbloader.js). Files are keyed by hex prefix, with
# denser prefixes split into their own deeper file listed under a "children" key.
DB_BASE_URL = 'http://flightaware.airwisp.net:8080/db/'

# Visible columns (order matters). Update this list to change the table.
# Trimmed/updated visible columns (removed Message Age, RSSI, Data Source, Photos)
COLUMNS = [
    'ICAO', 'Ident', 'Flag', 'Tail', 'Type', 'Squawk', 'Alt',
    'Speed (kt)', 'Dist', 'Heading',
    'Msgs', 'Lat', 'Lon'
]

# Columns that should be right-aligned (numeric-like)
NUMERIC_COLUMNS = set(['ICAO', 'Alt', 'Dist', 'Speed (kt)', 'Heading', 'Msgs', 'Lat', 'Lon'])

# Placeholder for missing values (use an em-dash for nicer alignment/appearance)
PLACEHOLDER = '\u2014'  # em dash

FLAGS_DIR = 'flags'

# Mirrors the local receiver's PiAware SkyAware config.js ColorByAlt scheme so
# aircraft are colored the same way as http://flightaware.airwisp.net:8080/.
COLOR_BY_ALT = {
    'unknown': (0, 0, 40),
    'ground': (15, 80, 20),
    'air_hue_points': [(2000, 20), (10000, 140), (40000, 300)],
    'air_s': 85,
    'air_l': 50,
    'stale': (0, -10, 30),
    'mlat': (0, -10, -10),
}
OUTLINE_ADSB_COLOR = '#000000'
OUTLINE_MLAT_COLOR = '#4040FF'
SPECIAL_SQUAWK_COLORS = {
    '7500': 'rgb(255, 85, 85)',
    '7600': 'rgb(0, 255, 255)',
    '7700': 'rgb(255, 255, 0)',
}

# (label, shape) entries shown in the map legend.
LEGEND_ENTRIES = [
    ('Airliner', 'airliner'),
    ('Heavy', 'heavy_2e'),
    ('Business jet', 'jet_swept'),
    ('Turboprop', 'twin_small'),
    ('Piston', 'cessna'),
    ('Helicopter', 'helicopter'),
    ('Glider/Balloon', 'balloon'),
    ('Ground vehicle', 'ground_fixed'),
    ('Unknown', 'unknown'),
]

# Client-side flight trails: dump1090 only reports current position, so we
# build up history ourselves from each poll, same as the reference site does.
TRAIL_MAX_POINTS = 300
TRAIL_MIN_MOVE_DEG = 0.0002  # ~20m; avoids piling up points while parked
TRAIL_MAX_AGE_SECONDS = 600  # drop a trail if its aircraft hasn't been seen this long

# tiny 1x1 PNG used as fallback when downloads fail
PLACEHOLDER_PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII='

# map column label to AircraftRow attribute
COLUMN_TO_ATTR = {
    'ICAO': 'icao',
    'Ident': 'ident',
    'Flag': 'flag_pixbuf',
    'Tail': 'registration',
    'Type': 'ac_type',
    'Squawk': 'squawk',
    'Alt': 'altitude',
    'Speed (kt)': 'speed',
    'Dist': 'distance',
    'Heading': 'heading',
    'Msgs': 'msgs',
    'Lat': 'latitude',
    'Lon': 'longitude',
}


def registration_from_hex(hexid):
    if not hexid:
        return None
    hid = str(hexid).strip()
    if hid.lower().startswith('0x'):
        hid = hid[2:]
    try:
        value = int(hid, 16)
    except (ValueError, TypeError):
        return None

    limited_alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    full_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    stride_mappings = [
        {'start': 0x008011, 's1': 26 * 26, 's2': 26, 'prefix': 'ZS-'},
        {'start': 0x390000, 's1': 1024, 's2': 32, 'prefix': 'F-G'},
        {'start': 0x398000, 's1': 1024, 's2': 32, 'prefix': 'F-H'},
        {'start': 0x3C4421, 's1': 1024, 's2': 32, 'prefix': 'D-A', 'first': 'AAA', 'last': 'OZZ'},
        {'start': 0x3C0001, 's1': 26 * 26, 's2': 26, 'prefix': 'D-A', 'first': 'PAA', 'last': 'ZZZ'},
        {'start': 0x3C8421, 's1': 1024, 's2': 32, 'prefix': 'D-B', 'first': 'AAA', 'last': 'OZZ'},
        {'start': 0x3C2001, 's1': 26 * 26, 's2': 26, 'prefix': 'D-B', 'first': 'PAA', 'last': 'ZZZ'},
        {'start': 0x3CC000, 's1': 26 * 26, 's2': 26, 'prefix': 'D-C'},
        {'start': 0x3D04A8, 's1': 26 * 26, 's2': 26, 'prefix': 'D-E'},
        {'start': 0x3D4950, 's1': 26 * 26, 's2': 26, 'prefix': 'D-F'},
        {'start': 0x3D8DF8, 's1': 26 * 26, 's2': 26, 'prefix': 'D-G'},
        {'start': 0x3DD2A0, 's1': 26 * 26, 's2': 26, 'prefix': 'D-H'},
        {'start': 0x3E1748, 's1': 26 * 26, 's2': 26, 'prefix': 'D-I'},
        {'start': 0x448421, 's1': 1024, 's2': 32, 'prefix': 'OO-'},
        {'start': 0x458421, 's1': 1024, 's2': 32, 'prefix': 'OY-'},
        {'start': 0x460000, 's1': 26 * 26, 's2': 26, 'prefix': 'OH-'},
        {'start': 0x468421, 's1': 1024, 's2': 32, 'prefix': 'SX-'},
        {'start': 0x490421, 's1': 1024, 's2': 32, 'prefix': 'CS-'},
        {'start': 0x4A0421, 's1': 1024, 's2': 32, 'prefix': 'YR-'},
        {'start': 0x4B8421, 's1': 1024, 's2': 32, 'prefix': 'TC-'},
        {'start': 0x740421, 's1': 1024, 's2': 32, 'prefix': 'JY-'},
        {'start': 0x760421, 's1': 1024, 's2': 32, 'prefix': 'AP-'},
        {'start': 0x768421, 's1': 1024, 's2': 32, 'prefix': '9V-'},
        {'start': 0x778421, 's1': 1024, 's2': 32, 'prefix': 'YK-'},
        {'start': 0x7C0000, 's1': 36 * 36, 's2': 36, 'prefix': 'VH-'},
        {'start': 0xC00001, 's1': 26 * 26, 's2': 26, 'prefix': 'C-F'},
        {'start': 0xC044A9, 's1': 26 * 26, 's2': 26, 'prefix': 'C-G'},
        {'start': 0xE01041, 's1': 4096, 's2': 64, 'prefix': 'LV-'}
    ]

    numeric_mappings = [
        {'start': 0x140000, 'first': 0, 'count': 100000, 'template': 'RA-00000'},
        {'start': 0x0B03E8, 'first': 1000, 'count': 1000, 'template': 'CU-T0000'}
    ]

    for mapping in stride_mappings:
        if 'alphabet' not in mapping:
            mapping['alphabet'] = full_alphabet
        if 'first' in mapping:
            c1 = mapping['alphabet'].index(mapping['first'][0])
            c2 = mapping['alphabet'].index(mapping['first'][1])
            c3 = mapping['alphabet'].index(mapping['first'][2])
            mapping['offset'] = c1 * mapping['s1'] + c2 * mapping['s2'] + c3
        else:
            mapping['offset'] = 0
        if 'last' in mapping:
            c1 = mapping['alphabet'].index(mapping['last'][0])
            c2 = mapping['alphabet'].index(mapping['last'][1])
            c3 = mapping['alphabet'].index(mapping['last'][2])
            mapping['end'] = mapping['start'] - mapping['offset'] + c1 * mapping['s1'] + c2 * mapping['s2'] + c3
        else:
            mapping['end'] = mapping['start'] - mapping['offset'] + (len(mapping['alphabet']) - 1) * mapping['s1'] + (len(mapping['alphabet']) - 1) * mapping['s2'] + (len(mapping['alphabet']) - 1)

    for mapping in numeric_mappings:
        mapping['end'] = mapping['start'] + mapping['count'] - 1

    def n_letter(rem):
        if rem == 0:
            return ''
        rem -= 1
        return limited_alphabet[rem]

    def n_letters(rem):
        if rem == 0:
            return ''
        rem -= 1
        return limited_alphabet[rem // 25] + n_letter(rem % 25)

    def n_reg(hexid_val):
        offset = hexid_val - 0xA00001
        if offset < 0 or offset >= 915399:
            return None
        digit1 = offset // 101711 + 1
        reg = 'N' + str(digit1)
        offset = offset % 101711
        if offset <= 600:
            return reg + n_letters(offset)
        offset -= 601
        digit2 = offset // 10111
        reg += str(digit2)
        offset = offset % 10111
        if offset <= 600:
            return reg + n_letters(offset)
        offset -= 601
        digit3 = offset // 951
        reg += str(digit3)
        offset = offset % 951
        if offset <= 600:
            return reg + n_letters(offset)
        offset -= 601
        digit4 = offset // 35
        reg += str(digit4)
        offset = offset % 35
        if offset <= 24:
            return reg + n_letter(offset)
        offset -= 25
        return reg + str(offset)

    def ja_reg(hexid_val):
        offset = hexid_val - 0x840000
        if offset < 0 or offset >= 229840:
            return None
        reg = 'JA'
        digit1 = offset // 22984
        if digit1 < 0 or digit1 > 9:
            return None
        reg += str(digit1)
        offset = offset % 22984
        digit2 = offset // 916
        if digit2 < 0 or digit2 > 9:
            return None
        reg += str(digit2)
        offset = offset % 916
        if offset < 340:
            digit3 = offset // 34
            reg += str(digit3)
            offset = offset % 34
            if offset < 10:
                return reg + str(offset)
            offset -= 10
            return reg + limited_alphabet[offset]
        offset -= 340
        letter3 = offset // 24
        if letter3 < 0 or letter3 >= len(limited_alphabet):
            return None
        return reg + limited_alphabet[letter3] + limited_alphabet[offset % 24]

    def hl_reg(hexid_val):
        if 0x71BA00 <= hexid_val <= 0x71BF99:
            return 'HL' + format(hexid_val - 0x71BA00 + 0x7200, 'X')
        if 0x71C000 <= hexid_val <= 0x71C099:
            return 'HL' + format(hexid_val - 0x71C000 + 0x8000, 'X')
        if 0x71C200 <= hexid_val <= 0x71C299:
            return 'HL' + format(hexid_val - 0x71C200 + 0x8200, 'X')
        return None

    def numeric_reg(hexid_val):
        for mapping in numeric_mappings:
            if hexid_val < mapping['start'] or hexid_val > mapping['end']:
                continue
            reg_num = hexid_val - mapping['start'] + mapping['first']
            reg = str(reg_num)
            return mapping['template'][:len(mapping['template']) - len(reg)] + reg
        return None

    def stride_reg(hexid_val):
        for mapping in stride_mappings:
            if hexid_val < mapping['start'] or hexid_val > mapping['end']:
                continue
            offset = hexid_val - mapping['start'] + mapping['offset']
            i1 = offset // mapping['s1']
            offset = offset % mapping['s1']
            i2 = offset // mapping['s2']
            offset = offset % mapping['s2']
            i3 = offset
            if i1 < 0 or i1 >= len(mapping['alphabet']) or i2 < 0 or i2 >= len(mapping['alphabet']) or i3 < 0 or i3 >= len(mapping['alphabet']):
                continue
            return mapping['prefix'] + mapping['alphabet'][i1] + mapping['alphabet'][i2] + mapping['alphabet'][i3]
        return None

    return n_reg(value) or ja_reg(value) or hl_reg(value) or numeric_reg(value) or stride_reg(value)


class AircraftRow(GObject.Object):
    def __init__(self):
        super().__init__()
        self.ident = ''
        self.flag_pixbuf = None
        self.registration = ''
        self.ac_type = ''
        self.squawk = ''
        self.altitude = ''
        self.speed = ''
        self.vrate = ''
        self.distance = ''
        self.heading = ''
        self.msgs = ''
        self.latitude = ''
        self.longitude = ''
        # self.airframes = ''


class FlightAwareDisplay(Gtk.Application):
    COLUMN_WIDTHS = {
        'ICAO': 100,
        'Ident': 160,
        'Flag': 36,
        'Tail': 90,
        'Type': 130,
        'Squawk': 60,
        'Alt': 90,
        'Speed (kt)': 80,
        'Dist': 90,
        'Heading': 70,
        'Msgs': 60,
        'Lat': 80,
        'Lon': 90,
    }

    def __init__(self):
        super().__init__(application_id='org.fa_display.app')
        self.window = None
        self.receiver = {}
        self.aircraft = []
        self.poll_interval = 2
        self.executor = ThreadPoolExecutor(max_workers=1)
        # Separate pool for hex DB lookups so a batch of new aircraft (each
        # needing 1-3 sequential requests to resolve) can't queue up behind
        # and starve the live receiver/aircraft.json poll on self.executor.
        self.db_executor = ThreadPoolExecutor(max_workers=3)
        self.refresh_source = None
        self.tile_cache_dir = 'tiles'
        self.pending_tile_downloads = set()
        self.map_zoom = 8
        self.zoom_label = None
        self.icon_pixbuf_cache = {}
        self.hex_bucket_cache = {}
        self.hex_entry_cache = {}
        self.hex_entry_pending = set()
        self.map_hit_targets = []
        self.selected_hex = None
        self.aircraft_trails = {}
        self.aircraft_trail_last_seen = {}
        self.show_trails = False
        try:
            os.makedirs(self.tile_cache_dir, exist_ok=True)
        except Exception:
            pass

    def do_activate(self):
        if self.window is None:
            self.window = self.build_ui()
        self.window.present()
        self.schedule_refresh()

    def build_ui(self):
        window = Gtk.ApplicationWindow(application=self, title='FlightAware Display')
        window.set_default_size(1000, 600)
        window.set_resizable(True)

        header = Gtk.Label(label='FlightAware receiver display', xalign=0)
        header.set_margin_bottom(6)
        header.set_hexpand(True)

        self.status_label = Gtk.Label(label='Starting...', xalign=0)
        self.status_label.set_wrap(True)
        self.status_label.set_margin_bottom(12)

        self.map_area = Gtk.DrawingArea()
        self.map_area.set_draw_func(self.on_map_draw)
        self.map_area.set_content_width(600)
        self.map_area.set_content_height(400)
        self.map_area.set_hexpand(True)
        self.map_area.set_vexpand(True)

        map_click = Gtk.GestureClick.new()
        map_click.connect('pressed', self.on_map_click)
        self.map_area.add_controller(map_click)

        self.zoom_label = Gtk.Label(label='Zoom: Auto', xalign=1.0)
        self.zoom_label.set_margin_end(0)
        self.zoom_label.set_margin_top(0)
        self.zoom_label.set_margin_bottom(0)

        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        zoom_box.set_halign(Gtk.Align.END)
        minus_button = Gtk.Button(label='-')
        plus_button = Gtk.Button(label='+')
        reset_button = Gtk.Button(label='Reset')
        minus_button.set_tooltip_text('Zoom out')
        plus_button.set_tooltip_text('Zoom in')
        reset_button.set_tooltip_text('Reset zoom to default 8')
        minus_button.connect('clicked', self.on_zoom_button_clicked, -1)
        plus_button.connect('clicked', self.on_zoom_button_clicked, +1)
        reset_button.connect('clicked', self.on_reset_zoom_clicked)

        trails_button = Gtk.ToggleButton(label='Trails')
        trails_button.set_tooltip_text('Show/hide aircraft flight trails')
        trails_button.set_active(self.show_trails)
        trails_button.connect('toggled', self.on_trails_toggled)

        zoom_box.append(minus_button)
        zoom_box.append(plus_button)
        zoom_box.append(reset_button)
        zoom_box.append(trails_button)
        zoom_box.append(self.zoom_label)

        map_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        map_box.append(zoom_box)
        map_box.append(self.map_area)

        map_frame = Gtk.Frame(label='Map')
        map_frame.set_child(map_box)
        map_frame.set_vexpand(True)
        map_frame.set_hexpand(True)

        # Use a ListView backed by a Gio.ListStore for proper tabular behavior
        self.columns = COLUMNS
        self.size_groups = {col: Gtk.SizeGroup.new(Gtk.SizeGroupMode.HORIZONTAL) for col in self.columns}
        self.list_store = Gio.ListStore.new(AircraftRow)
        self.selection = Gtk.SingleSelection.new(self.list_store)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.on_factory_setup)
        factory.connect("bind", self.on_factory_bind)

        self.aircraft_list = Gtk.ListView.new(self.selection, factory)
        self.aircraft_list.set_vexpand(True)
        try:
            self.aircraft_list.add_css_class('aircraft-list')
        except Exception:
            pass

        header_factory = Gtk.SignalListItemFactory()
        header_factory.connect("setup", self.on_header_factory_setup)
        self.aircraft_list.set_header_factory(header_factory)

        # prepare placeholder.png by copying a common flag (US) if available
        try:
            ph = os.path.join(FLAGS_DIR, 'placeholder.png')
            if not os.path.exists(ph):
                src = os.path.join(FLAGS_DIR, 'us.png')
                if os.path.exists(src):
                    import shutil
                    shutil.copyfile(src, ph)
                else:
                    with open(ph, 'wb') as fh:
                        fh.write(base64.b64decode(PLACEHOLDER_PNG_B64))
        except Exception:
            pass

        provider = Gtk.CssProvider()
        provider.load_from_data(b'''
.row-even {
    background-color: rgba(255, 255, 255, 0.04);
}
.row-odd {
    background-color: rgba(255, 255, 255, 0.02);
}
.heading {
    font-weight: bold;
    color: #5A7595;
    font-family: system-ui, sans-serif;
}
.header-row {
    border-bottom: 1px solid rgba(90, 117, 149, 0.3);
    padding-bottom: 4px;
    margin-bottom: 4px;
}
/* Make aircraft list labels slightly larger and monospace */
.aircraft-list label {
    font-family: monospace;
    font-size: 13px;
    padding: 0px 8px;
    margin: 0px;
}
/* Header labels should match row size and spacing */
.heading {
    font-size: 13px;
    padding: 0px 8px;
    margin: 0px;
}
/* Tighten list item spacing */
.listview, .listview * {
    padding: 0px;
    margin: 0px;
}
''')
        Gtk.StyleContext.add_provider_for_display(window.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.aircraft_list)
        scroller.set_min_content_width(560)
        scroller.set_min_content_height(350)

        list_frame = Gtk.Frame(label='Aircraft')
        list_frame.set_child(scroller)
        list_frame.set_vexpand(True)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        main_box.append(header)
        main_box.append(self.status_label)

        # Place the table below the map (vertical layout)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.append(map_frame)
        content_box.append(list_frame)
        content_box.set_vexpand(True)

        main_box.append(content_box)
        window.set_child(main_box)
        return window

    def on_header_factory_setup(self, factory, list_item):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_margin_top(2)
        row.set_margin_bottom(2)
        row.set_homogeneous(False)
        row.add_css_class('header-row')
        width_map = self.COLUMN_WIDTHS
        for idx, value in enumerate(self.columns):
            label = Gtk.Label(xalign=0.0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(False)
            label.set_halign(Gtk.Align.FILL)
            label.set_xalign(0.0)
            label.set_justify(Gtk.Justification.LEFT)
            label.add_css_class('heading')
            escaped = GLib.markup_escape_text(str(value))
            label.set_markup(f'<span foreground="#5A7595" weight="bold">{escaped}</span>')
            try:
                gesture = Gtk.GestureClick.new()
                gesture.connect('pressed', lambda g, n, x, y, idx=idx: self.on_header_clicked(idx))
                label.add_controller(gesture)
            except Exception:
                pass
            desired = width_map.get(value)
            if desired:
                try:
                    label.set_min_content_width(desired)
                except Exception:
                    pass
            if value in self.size_groups:
                self.size_groups[value].add_widget(label)
            row.append(label)
        list_item.set_child(row)


    def schedule_refresh(self):
        if self.refresh_source is None:
            self.refresh_source = GLib.timeout_add_seconds(self.poll_interval, self.refresh_data)
            self.refresh_data()

    def get_marker_shape(self, item):
        category = item.get('category')
        shape = CATEGORY_ICONS.get(category, 'unknown') if category else 'unknown'
        return shape if shape in AIRCRAFT_SHAPES else 'unknown'

    def get_altitude_hsl(self, item):
        alt = item.get('alt_baro')
        if alt is None:
            alt = item.get('alt_geom')
        if isinstance(alt, str) and alt.strip().lower() == 'ground':
            return COLOR_BY_ALT['ground']
        if alt is None:
            return COLOR_BY_ALT['unknown']
        try:
            alt = float(alt)
        except (TypeError, ValueError):
            return COLOR_BY_ALT['unknown']

        s = COLOR_BY_ALT['air_s']
        l = COLOR_BY_ALT['air_l']
        points = COLOR_BY_ALT['air_hue_points']
        h = points[0][1]
        for i in range(len(points) - 1, -1, -1):
            alt_i, val_i = points[i]
            if alt > alt_i:
                if i == len(points) - 1:
                    h = val_i
                else:
                    alt_next, val_next = points[i + 1]
                    h = val_i + (val_next - val_i) * (alt - alt_i) / (alt_next - alt_i)
                break
        return h, s, l

    def get_marker_colors(self, item):
        is_mlat = any(f in ('lat', 'lon') for f in (item.get('mlat') or []))
        stroke = OUTLINE_MLAT_COLOR if is_mlat else OUTLINE_ADSB_COLOR

        squawk = item.get('squawk')
        special = SPECIAL_SQUAWK_COLORS.get(squawk)
        if special:
            return special, stroke

        h, s, l = self.get_altitude_hsl(item)

        seen_pos = item.get('seen_pos')
        if seen_pos is not None and seen_pos > 15:
            dh, ds, dl = COLOR_BY_ALT['stale']
            h, s, l = h + dh, s + ds, l + dl

        if is_mlat:
            dh, ds, dl = COLOR_BY_ALT['mlat']
            h, s, l = h + dh, s + ds, l + dl

        h = h % 360
        s = max(5, min(95, s))
        l = max(5, min(95, l))
        fill = f'hsl({round(h / 5) * 5:.0f},{round(s / 5) * 5:.0f}%,{round(l / 5) * 5:.0f}%)'
        return fill, stroke

    @staticmethod
    def hsl_to_rgb(h, s, l):
        r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l / 100.0, s / 100.0)
        return r, g, b

    def get_icon_pixbuf(self, shape, fill, stroke, target_size=26):
        key = (shape, fill, stroke, target_size)
        if key in self.icon_pixbuf_cache:
            return self.icon_pixbuf_cache[key]

        meta = AIRCRAFT_SHAPES.get(shape) or AIRCRAFT_SHAPES['unknown']
        svg = meta['svg']
        svg = svg.replace('aircraft_color_fill', fill)
        svg = svg.replace('aircraft_color_stroke', stroke)
        svg = svg.replace('add_stroke_selected', '')
        pixbuf = None
        try:
            stream = Gio.MemoryInputStream.new_from_data(svg.encode('utf-8'), None)
            w, h = meta['size']
            if w >= h:
                pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, target_size, -1, True, None)
            else:
                pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, -1, target_size, True, None)
        except Exception:
            pixbuf = None
        self.icon_pixbuf_cache[key] = pixbuf
        return pixbuf

    def refresh_data(self):
        self.executor.submit(self.fetch_data)
        return True

    def fetch_data(self):
        receiver = self.fetch_json(RECEIVER_URL)
        aircraft = self.fetch_json(AIRCRAFT_URL)
        GLib.idle_add(self.update_data, receiver, aircraft)

    def fetch_json(self, url):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'fa-display/1.0'})
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read().decode('utf-8')
                return json.loads(data)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            print(f'Error fetching {url}: {exc}')
            return None

    def get_hex_entry(self, hex_code):
        # Returns the receiver DB's {'r': registration, 't': type designator}
        # entry for an ICAO hex address, or None if unknown/not loaded yet.
        # A background lookup is kicked off on first request for a given hex;
        # callers should just re-check on the next redraw/rebuild.
        if not hex_code:
            return None
        hexv = str(hex_code).strip().upper().lstrip('~')
        if len(hexv) < 2:
            return None
        if hexv in self.hex_entry_cache:
            return self.hex_entry_cache[hexv] or None
        if hexv not in self.hex_entry_pending:
            self.hex_entry_pending.add(hexv)
            self.db_executor.submit(self.resolve_hex_entry, hexv)
        return None

    def get_hex_bucket(self, bkey):
        if bkey in self.hex_bucket_cache:
            return self.hex_bucket_cache[bkey]
        data = self.fetch_json(f'{DB_BASE_URL}{bkey}.json')
        bucket = data if isinstance(data, dict) else {}
        self.hex_bucket_cache[bkey] = bucket
        return bucket

    def resolve_hex_entry(self, hexv):
        # Walks the receiver's hex-prefix tree: db/<hexv[0]>.json first, then
        # (only for the denser prefixes it lists under "children") a deeper
        # db/<2-char-prefix>.json, mirroring dbloader.js's request_from_db.
        entry = None
        level = 1
        while level <= len(hexv):
            bkey = hexv[:level]
            bucket = self.get_hex_bucket(bkey)
            if not bucket:
                break
            dkey = hexv[level:]
            if dkey in bucket:
                entry = bucket[dkey]
                break
            children = bucket.get('children')
            if children and level < len(hexv) and hexv[:level + 1] in children:
                level += 1
                continue
            break
        GLib.idle_add(self.on_hex_entry_resolved, hexv, entry or {})

    def on_hex_entry_resolved(self, hexv, entry):
        self.hex_entry_cache[hexv] = entry
        self.hex_entry_pending.discard(hexv)
        self.rebuild_aircraft_list()
        return False

    def update_data(self, receiver, aircraft):
        if receiver:
            self.receiver = receiver
            self.poll_interval = int(receiver.get('refresh', self.poll_interval) / 1000) or self.poll_interval

        if aircraft and 'aircraft' in aircraft:
            self.aircraft = aircraft['aircraft']
            self.update_trails()

        self.status_label.set_text(self.build_status_text())
        self.rebuild_aircraft_list()
        self.map_area.queue_draw()
        return False

    def build_status_text(self):
        if not self.receiver:
            return 'Waiting for receiver data...'
        refresh_ms = self.receiver.get('refresh', 1000)
        center = f"Receiver center: {self.receiver.get('lat', 'n/a'):.5f}, {self.receiver.get('lon', 'n/a'):.5f}"
        aircraft_count = len(self.aircraft)
        return f'{center} | {aircraft_count} aircraft | polling every {refresh_ms // 1000}s'

    def rebuild_aircraft_list(self):
        # Rebuild the Gio.ListStore backing the ListView
        self.list_store.remove_all()
        # Ensure flags directory exists
        try:
            os.makedirs(FLAGS_DIR, exist_ok=True)
        except Exception:
            pass

        for index, item in enumerate(self.aircraft[:200]):
            # Skip entries with no identifier (don't show anonymous rows)
            ident_raw = item.get('flight') or item.get('ident') or item.get('registration')
            if not ident_raw or str(ident_raw).strip() == '':
                continue
            row = AircraftRow()
            row.icao = self.format_field(item, 'ICAO')
            row.ident = self.format_field(item, 'Ident')
            code = self.infer_aircraft_country(item)
            row.flag_pixbuf = self.get_flag_pixbuf(code)
            row.registration = self.format_field(item, 'Tail')
            row.ac_type = self.format_field(item, 'Type')
            row.squawk = self.format_field(item, 'Squawk')
            row.altitude = self.format_field(item, 'Alt')
            row.speed = self.format_field(item, 'Speed (kt)')
            row.distance = self.format_field(item, 'Dist')
            row.heading = self.format_field(item, 'Heading')
            row.msgs = self.format_field(item, 'Msgs')
            row.latitude = self.format_field(item, 'Lat')
            row.longitude = self.format_field(item, 'Lon')
            self.list_store.append(row)
        # remember current sort state if any
        # (we simply keep attributes on self.sort_index and self.sort_asc)
        if not hasattr(self, 'sort_index'):
            self.sort_index = None
            self.sort_asc = True

        self.apply_row_selection()

    def on_factory_setup(self, factory, list_item):
        # Create the row widgets and attach to the list_item
        # Reduce spacing for denser rows
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        widgets = []
        for col in self.columns:
            if col == 'Flag':
                img = Gtk.Image()
                img.set_valign(Gtk.Align.CENTER)
                img.set_pixel_size(18)
                img.set_margin_top(0)
                img.set_margin_bottom(0)
                widgets.append(img)
                box.append(img)
                if col in self.size_groups:
                    self.size_groups[col].add_widget(img)
            else:
                lbl = Gtk.Label()
                lbl.set_xalign(1.0)
                lbl.set_justify(Gtk.Justification.RIGHT)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_hexpand(False)
                lbl.set_margin_top(0)
                lbl.set_margin_bottom(0)
                lbl.add_css_class('row-label')
                widgets.append(lbl)
                box.append(lbl)
                if col in self.size_groups:
                    self.size_groups[col].add_widget(lbl)
            # set fixed widths where appropriate
            desired = self.COLUMN_WIDTHS.get(col)
            try:
                w = widgets[-1]
                if desired:
                    w.set_min_content_width(desired)
                    w.set_hexpand(False)
                    w.set_halign(Gtk.Align.FILL)
            except Exception:
                pass
        list_item.set_child(box)
        list_item.widgets = widgets

    def on_factory_bind(self, factory, list_item):
        obj = list_item.get_item()
        widgets = getattr(list_item, 'widgets', None)
        for idx, col in enumerate(self.columns):
            w = widgets[idx]
            try:
                if col == 'Flag':
                    if obj.flag_pixbuf:
                        w.set_from_pixbuf(obj.flag_pixbuf)
                    else:
                        # fallback to placeholder.png
                        ph = os.path.join(FLAGS_DIR, 'placeholder.png')
                        if os.path.exists(ph):
                            try:
                                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(ph, width=24, height=18, preserve_aspect_ratio=True)
                                w.set_from_pixbuf(pb)
                            except Exception:
                                w.clear()
                        else:
                            w.clear()
                else:
                    # pick attribute from AircraftRow via COLUMN_TO_ATTR mapping
                    attr = COLUMN_TO_ATTR.get(col, col.lower().split('(')[0].strip().replace(' ', '_'))
                    text = getattr(obj, attr, '')
                    if text is None or text == '':
                        text = PLACEHOLDER
                    else:
                        w.set_text(str(text))
            except Exception:
                pass

    def on_header_clicked(self, index):
        # Toggle sort on column index
        try:
            if getattr(self, 'sort_index', None) == index:
                self.sort_asc = not getattr(self, 'sort_asc', True)
            else:
                self.sort_index = index
                self.sort_asc = True

            attr = COLUMN_TO_ATTR.get(self.columns[index], self.columns[index].lower().split('(')[0].strip().replace(' ', '_'))
            # extract all items
            items = [self.list_store.get_item(i) for i in range(self.list_store.get_n_items())]

            def key_fn(item):
                v = getattr(item, attr, None)
                if v is None:
                    return ''
                # try numeric
                try:
                    s = str(v).replace(',', '').replace(' ft', '').replace(' NM', '').replace(' kt', '').replace('°', '').strip()
                    return float(s)
                except Exception:
                    return str(v).lower()

            items_sorted = sorted(items, key=key_fn, reverse=not self.sort_asc)
            # repopulate list_store
            self.list_store.remove_all()
            for it in items_sorted:
                self.list_store.append(it)
            self.apply_row_selection()
        except Exception:
            pass

    def get_flag_pixbuf(self, code):
        if not code:
            return None
        s = str(code).strip()
        if len(s) == 2 and s.isalpha():
            cc = s.lower()
        elif isinstance(s, str) and len(s) >= 2:
            cc = s[-2:].lower() if s[-2:].isalpha() else None
        else:
            cc = None
        if not cc:
            return None
        filename = os.path.join(FLAGS_DIR, f"{cc}.png")
        if not os.path.exists(filename):
            url = f"https://flagcdn.com/w40/{cc}.png"
            try:
                urllib.request.urlretrieve(url, filename)
            except Exception:
                try:
                    with open(filename, 'wb') as fh:
                        fh.write(base64.b64decode(PLACEHOLDER_PNG_B64))
                except Exception:
                    return None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(filename, width=24, height=18, preserve_aspect_ratio=True)
            return pb
        except Exception:
            ph = os.path.join(FLAGS_DIR, 'placeholder.png')
            if os.path.exists(ph):
                try:
                    return GdkPixbuf.Pixbuf.new_from_file_at_scale(ph, width=24, height=18, preserve_aspect_ratio=True)
                except Exception:
                    return None
            return None

    def format_field(self, item, label):
        key = label.lower()
        if key == 'icao':
            hexv = item.get('hex')
            return hexv.upper() if hexv else PLACEHOLDER
        if key == 'ident':
            val = item.get('ident') or item.get('flight')
            if val:
                return str(val).strip() or PLACEHOLDER
            return PLACEHOLDER
        if key in ('tail', 'registration'):
            val = item.get('registration')
            if val and str(val).strip():
                return str(val).strip()
            entry = self.get_hex_entry(item.get('hex'))
            if entry and entry.get('r'):
                return str(entry['r']).strip()
            reg = registration_from_hex(item.get('hex'))
            return str(reg).strip() if reg else PLACEHOLDER
        if key in ('type', 'aircraft type'):
            # No raw category ("A2"/"A3"/...) fallback here on purpose: that's
            # the ADS-B emitter category, not an aircraft type, and showing it
            # is misleading. The reference SkyAware site leaves this blank
            # when it has no ICAO type designator on file for the hex, too.
            entry = self.get_hex_entry(item.get('hex'))
            if entry and entry.get('t'):
                return str(entry['t']).strip()
            val = item.get('type') or item.get('model')
            if val:
                return str(val).strip()
            return PLACEHOLDER
        if key in ('latitude', 'lat'):
            v = item.get('lat') or item.get('latitude')
            if v is None:
                return PLACEHOLDER
            try:
                return f"{float(v):.3f}"
            except Exception:
                return str(v)
        if key in ('longitude', 'lon'):
            v = item.get('lon') or item.get('longitude')
            if v is None:
                return PLACEHOLDER
            try:
                return f"{float(v):.3f}"
            except Exception:
                return str(v)
        if 'alt' in key:
            v = item.get('alt_baro') if item.get('alt_baro') is not None else item.get('alt_geom')
            if v is None:
                return PLACEHOLDER
            try:
                iv = int(float(v))
                return f"{iv:,} ft"
            except Exception:
                return str(v)
        if key == 'speed (kt)' or key == 'speed' or key == 'gs':
            for k in ('gs', 'speed'):
                v = item.get(k)
                if v is not None:
                    try:
                        return f"{float(v):.0f}"
                    except Exception:
                        return str(v)
            return PLACEHOLDER
        if key in ('track', 'heading'):
            v = item.get('track') or item.get('heading')
            if v is None:
                return PLACEHOLDER
            try:
                iv = int(float(v))
                return f"{iv}°"
            except Exception:
                return str(v)
        if key == 'squawk':
            v = item.get('squawk')
            return str(v) if v is not None else PLACEHOLDER
        if key in ('msgs', 'messages'):
            v = item.get('messages')
            return str(v) if v is not None else PLACEHOLDER
        if key in ('distance (nm)', 'distance', 'dist'):
            lat = item.get('lat')
            lon = item.get('lon')
            center_lat = self.receiver.get('lat')
            center_lon = self.receiver.get('lon')
            if lat is None or lon is None or center_lat is None or center_lon is None:
                return PLACEHOLDER
            dx, dy = self.latlon_to_meters(lat, lon, center_lat, center_lon)
            meters = math.hypot(dx, dy)
            nm = meters / 1852.0
            return f"{nm:.1f} NM"
        if key == 'flag':
            code = item.get('country_code') or item.get('country')
            if not code:
                # infer by flight/operator if country isn't provided
                code = self.infer_aircraft_country(item)
            return self.country_code_to_emoji(code) if code else PLACEHOLDER
        # generic mappings
        generic_map = {
            'squawk': 'squawk',
            'distance': 'd',
            'longitude': 'lon',
            'latitude': 'lat',
            'flag': 'flag',
            'aircraft type': 'type',
            'data source': 'source',
            'photos': 'photos',
            'flightaware': 'flightaware',
        }
        mapped = generic_map.get(key)
        if mapped:
            v = item.get(mapped)
            return str(v) if v is not None else PLACEHOLDER
        return PLACEHOLDER

    def infer_aircraft_country(self, item):
        # Prefer explicit country codes or names when available
        country_code = item.get('country_code') or item.get('country')
        if country_code:
            return country_code

        flight = item.get('flight') or ''
        flight_code = ''.join([c for c in str(flight).strip() if c.isalpha()])[:3].upper()
        airline_map = {
            'UAL': 'US', 'AAL': 'US', 'SWA': 'US', 'SKW': 'US', 'DAL': 'US', 'ASA': 'US',
            'NKS': 'US', 'FFT': 'US', 'JBU': 'US', 'UAE': 'AE', 'CPA': 'CA', 'ACA': 'CA',
            'JAL': 'JP', 'ANA': 'JP', 'BAW': 'GB', 'AFR': 'FR', 'KLM': 'NL', 'QFA': 'AU',
            'DLH': 'DE', 'SAS': 'SE', 'RYR': 'IE', 'CSN': 'CA', 'ACA': 'CA', 'SWR': 'DE',
            'EZY': 'GB', 'TAP': 'PT', 'SIA': 'SG', 'QTR': 'QA', 'QR': 'QA', 'NH': 'JP'
        }
        if flight_code in airline_map:
            return airline_map[flight_code]

        hex_code = item.get('hex')
        if hex_code and len(str(hex_code)) >= 2:
            prefix = str(hex_code).upper()[:2]
            prefix_map = {
                'A0': 'US', 'A1': 'US', 'A2': 'US', 'A3': 'US', 'A4': 'US', 'A5': 'US',
                'A6': 'US', 'A7': 'US', 'A8': 'US', 'A9': 'US', 'AC': 'US',
                'C0': 'CA', 'C1': 'CA', 'C2': 'CA', 'C3': 'CA', 'C4': 'CA',
                '7C': 'CA', '7B': 'CA', '86': 'JP', 'B3': 'GB', '44': 'GB'
            }
            if prefix in prefix_map:
                return prefix_map[prefix]
        return None

    def on_map_draw(self, area, context, width, height):
        self.map_hit_targets = []

        context.set_source_rgb(0.93, 0.93, 0.93)
        context.rectangle(0, 0, width, height)
        context.fill()

        center_lat = self.receiver.get('lat')
        center_lon = self.receiver.get('lon')
        if center_lat is None or center_lon is None:
            context.set_source_rgb(0, 0, 0)
            context.select_font_face('Sans', 0, 0)
            context.set_font_size(18)
            context.move_to(width * 0.1, height * 0.5)
            context.show_text('Receiver location unavailable')
            return

        points = [(center_lat, center_lon)]
        for item in self.aircraft:
            lat = item.get('lat')
            lon = item.get('lon')
            if lat is None or lon is None:
                continue
            points.append((lat, lon))

        zoom = self.map_zoom if self.map_zoom is not None else self.compute_map_zoom(width, height, points)
        self.zoom_label.set_text(f'Zoom: {zoom}')
        center_tx, center_ty = self.latlon_to_tile_xy(center_lat, center_lon, zoom)
        center_px = center_tx * 256.0
        center_py = center_ty * 256.0
        left = center_px - width / 2.0
        top = center_py - height / 2.0

        num_tiles = 2 ** zoom
        tile_x0 = int(math.floor(left / 256.0))
        tile_y0 = int(math.floor(top / 256.0))
        tile_x1 = int(math.floor((center_px + width / 2.0) / 256.0))
        tile_y1 = int(math.floor((center_py + height / 2.0) / 256.0))

        for ty in range(tile_y0, tile_y1 + 1):
            if ty < 0 or ty >= num_tiles:
                continue
            for tx in range(tile_x0, tile_x1 + 1):
                wrapped_tx = tx % num_tiles
                px = tx * 256.0 - left
                py = ty * 256.0 - top
                tile = self.get_tile_surface(zoom, wrapped_tx, ty)
                if tile:
                    Gdk.cairo_set_source_pixbuf(context, tile, px, py)
                    context.paint()
                else:
                    context.set_source_rgb(0.82, 0.82, 0.82)
                    context.rectangle(px, py, 256.0, 256.0)
                    context.fill()

        resolution = 156543.03392 * math.cos(math.radians(center_lat)) / (2 ** zoom)
        context.select_font_face('Sans', 0, 0)
        context.set_font_size(10)
        for nm in (5, 15, 25, 35, 45):
            radius_px = (nm * 1852.0) / resolution
            context.set_source_rgba(0.1, 0.1, 0.1, 0.45)
            context.set_line_width(1.0)
            context.arc(width / 2.0, height / 2.0, radius_px, 0, 2 * math.pi)
            context.stroke()

            label = f'{nm} NM'
            extents = context.text_extents(label)
            lx = width / 2.0 - extents.width / 2.0
            ly = height / 2.0 - radius_px - 4
            if ly < 14:
                continue
            pad = 2
            context.set_source_rgba(1.0, 1.0, 1.0, 0.75)
            context.rectangle(lx - pad, ly - extents.height - pad, extents.width + 2 * pad, extents.height + 2 * pad)
            context.fill()
            context.set_source_rgb(0.15, 0.15, 0.15)
            context.move_to(lx, ly)
            context.show_text(label)

        context.set_source_rgb(0.05, 0.05, 0.05)
        context.arc(width / 2.0, height / 2.0, 6.0, 0, 2 * math.pi)
        context.fill()

        self.draw_aircraft_trails(context, zoom, left, top, width, height)

        for item in self.aircraft:
            lat = item.get('lat')
            lon = item.get('lon')
            if lat is None or lon is None:
                continue
            tx, ty = self.latlon_to_tile_xy(lat, lon, zoom)
            px = tx * 256.0 - left
            py = ty * 256.0 - top
            heading = item.get('track') or item.get('heading') or 0
            if px < -20 or px > width + 20 or py < -20 or py > height + 20:
                continue
            self.draw_aircraft_icon(context, px, py, heading, item)
            hex_code = item.get('hex')
            if hex_code:
                self.map_hit_targets.append((px, py, str(hex_code).upper()))

        self.draw_map_legend(context, width, height)
        self.draw_altitude_legend(context, width, height)

        context.set_source_rgb(0, 0, 0)
        context.select_font_face('Sans', 0, 0)
        context.set_font_size(12)
        context.move_to(10, 20)
        context.show_text(f'Receiver: {center_lat:.5f}, {center_lon:.5f}')

    def on_map_click(self, gesture, n_press, x, y):
        hex_code = self.find_aircraft_at(x, y)
        if hex_code:
            self.selected_hex = hex_code
            self.apply_row_selection()

    def find_aircraft_at(self, x, y):
        hit_radius = 14.0
        best_hex = None
        best_dist = hit_radius
        for px, py, hex_code in self.map_hit_targets:
            dist = math.hypot(px - x, py - y)
            if dist <= best_dist:
                best_dist = dist
                best_hex = hex_code
        return best_hex

    def apply_row_selection(self):
        if not self.selected_hex:
            return
        for i in range(self.list_store.get_n_items()):
            row = self.list_store.get_item(i)
            if getattr(row, 'icao', None) == self.selected_hex:
                self.selection.select_item(i, True)
                return

    def on_trails_toggled(self, button):
        self.show_trails = button.get_active()
        self.map_area.queue_draw()

    def update_trails(self):
        now = time.time()
        for item in self.aircraft:
            lat = item.get('lat')
            lon = item.get('lon')
            hex_code = item.get('hex')
            if lat is None or lon is None or not hex_code:
                continue
            hex_code = str(hex_code).upper()
            alt = item.get('alt_baro')
            if alt is None:
                alt = item.get('alt_geom')

            trail = self.aircraft_trails.setdefault(hex_code, [])
            if trail:
                last_lat, last_lon, _ = trail[-1]
                if abs(lat - last_lat) < TRAIL_MIN_MOVE_DEG and abs(lon - last_lon) < TRAIL_MIN_MOVE_DEG:
                    self.aircraft_trail_last_seen[hex_code] = now
                    continue
            trail.append((lat, lon, alt))
            if len(trail) > TRAIL_MAX_POINTS:
                del trail[:len(trail) - TRAIL_MAX_POINTS]
            self.aircraft_trail_last_seen[hex_code] = now

        stale_cutoff = now - TRAIL_MAX_AGE_SECONDS
        for hex_code in [h for h, seen in self.aircraft_trail_last_seen.items() if seen < stale_cutoff]:
            self.aircraft_trails.pop(hex_code, None)
            self.aircraft_trail_last_seen.pop(hex_code, None)

    def draw_aircraft_trails(self, context, zoom, left, top, width, height):
        if not self.show_trails:
            return
        context.save()
        context.set_line_width(2.0)
        context.set_line_cap(cairo.LINE_CAP_ROUND)
        context.set_line_join(cairo.LINE_JOIN_ROUND)
        for trail in self.aircraft_trails.values():
            if len(trail) < 2:
                continue
            points = []
            for lat, lon, alt in trail:
                tx, ty = self.latlon_to_tile_xy(lat, lon, zoom)
                points.append((tx * 256.0 - left, ty * 256.0 - top, alt))

            segment_count = len(points) - 1
            for i in range(segment_count):
                x0, y0, _ = points[i]
                x1, y1, alt1 = points[i + 1]
                if (max(x0, x1) < -20 or min(x0, x1) > width + 20
                        or max(y0, y1) < -20 or min(y0, y1) > height + 20):
                    continue
                h, s, l = self.get_altitude_hsl({'alt_baro': alt1})
                r, g, b = self.hsl_to_rgb(h, s, l)
                age_fraction = i / max(1, segment_count - 1)
                alpha = 0.25 + 0.55 * age_fraction
                context.set_source_rgba(r, g, b, alpha)
                context.move_to(x0, y0)
                context.line_to(x1, y1)
                context.stroke()
        context.restore()

    @staticmethod
    def latlon_to_meters(lat, lon, center_lat, center_lon):
        lat_m = (lat - center_lat) * 111320.0
        lon_m = (lon - center_lon) * 111320.0 * math.cos(math.radians(center_lat))
        return lon_m, lat_m

    @staticmethod
    def latlon_to_mercator(lat, lon):
        x = (lon + 180.0) / 360.0
        sin_lat = math.sin(math.radians(lat))
        y = 0.5 - 0.5 * math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / math.pi
        return x, y

    def latlon_to_tile_xy(self, lat, lon, zoom):
        n = 2.0 ** zoom
        x = (lon + 180.0) / 360.0 * n
        lat_rad = math.radians(lat)
        y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
        return x, y

    def get_tile_surface(self, z, x, y):
        path = os.path.join(self.tile_cache_dir, f'{z}_{x}_{y}.png')
        if os.path.exists(path):
            try:
                return GdkPixbuf.Pixbuf.new_from_file(path)
            except Exception:
                return None
        if (z, x, y) not in self.pending_tile_downloads:
            self.pending_tile_downloads.add((z, x, y))
            self.executor.submit(self.download_tile, z, x, y)
        return None

    def download_tile(self, z, x, y):
        url = f'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
        path = os.path.join(self.tile_cache_dir, f'{z}_{x}_{y}.png')
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'fa-display/1.0'})
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read()
                with open(path, 'wb') as fh:
                    fh.write(data)
        except Exception:
            pass
        finally:
            self.pending_tile_downloads.discard((z, x, y))
        GLib.idle_add(self.map_area.queue_draw)

    def draw_aircraft_icon(self, context, x, y, heading, item):
        shape = self.get_marker_shape(item)
        fill, stroke = self.get_marker_colors(item)
        meta = AIRCRAFT_SHAPES.get(shape) or AIRCRAFT_SHAPES['unknown']
        icon = self.get_icon_pixbuf(shape, fill, stroke)
        if icon is None:
            return
        context.save()
        context.translate(x, y)
        if not meta['no_rotate']:
            context.rotate(math.radians(heading or 0))
        Gdk.cairo_set_source_pixbuf(context, icon, -icon.get_width() / 2.0, -icon.get_height() / 2.0)
        context.paint()
        context.restore()

    def draw_map_legend(self, context, width, height):
        entries = LEGEND_ENTRIES
        panel_margin = 14
        item_height = 15
        left_pad = 6
        icon_col_width = 14
        text_left_pad = 5
        right_pad = 8
        icon_size = 12
        top_offset = 16
        bottom_pad = 4

        context.save()
        context.select_font_face('Sans', 0, 0)
        context.set_font_size(8)

        text_x_rel = left_pad + icon_col_width + text_left_pad
        max_label_width = max(context.text_extents(label)[2] for label, _ in entries)
        title_width = context.text_extents('Icon legend')[2]
        panel_width = max(text_x_rel + max_label_width + right_pad, left_pad + title_width + right_pad)
        panel_height = top_offset + item_height * len(entries) + bottom_pad

        x0 = width - panel_width - panel_margin
        y0 = panel_margin

        context.set_source_rgba(1.0, 1.0, 1.0, 0.9)
        context.rectangle(x0, y0, panel_width, panel_height)
        context.fill()

        context.set_source_rgba(0.18, 0.18, 0.18, 0.8)
        context.set_line_width(1.0)
        context.rectangle(x0, y0, panel_width, panel_height)
        context.stroke()

        context.set_source_rgb(0.1, 0.1, 0.1)
        context.move_to(x0 + left_pad, y0 + 11)
        context.show_text('Icon legend')

        for idx, (label, shape) in enumerate(entries):
            item_y = y0 + top_offset + idx * item_height
            icon_x = x0 + left_pad + icon_col_width / 2.0
            text_x = x0 + text_x_rel
            self.draw_legend_icon(context, icon_x, item_y + item_height / 2.0, shape, icon_size)
            context.set_source_rgb(0.08, 0.08, 0.08)
            context.move_to(text_x, item_y + item_height - 4)
            context.show_text(label)

        context.restore()

    def draw_legend_icon(self, context, x, y, shape, target_size=22):
        icon = self.get_icon_pixbuf(shape, 'hsl(0,0%,55%)', '#000000', target_size=target_size)
        if icon is None:
            return
        context.save()
        Gdk.cairo_set_source_pixbuf(context, icon, x - icon.get_width() / 2.0, y - icon.get_height() / 2.0)
        context.paint()
        context.restore()

    def draw_altitude_legend(self, context, width, height):
        bar_width = min(220, max(120, width - 260))
        bar_height = 8
        swatch_size = 9
        panel_margin = 14
        max_alt = 42000.0
        tick_alts = [0, 10000, 20000, 30000, 40000]

        context.save()
        context.select_font_face('Sans', 0, 0)
        context.set_font_size(8)

        ground_label = 'Ground'
        unknown_label = 'Unknown'
        ground_w = context.text_extents(ground_label)[2]
        unknown_w = context.text_extents(unknown_label)[2]

        left_pad = 6
        swatch_gap = 3
        group_gap = 8
        cx = left_pad
        ground_x = cx
        cx += swatch_size + swatch_gap + ground_w + group_gap
        unknown_x = cx
        cx += swatch_size + swatch_gap + unknown_w + group_gap
        bar_x = cx
        content_width = bar_x + bar_width + left_pad

        row_h = max(swatch_size, bar_height)
        pad_top = 5
        tick_area = 11
        pad_bottom = 3
        panel_height = pad_top + row_h + tick_area + pad_bottom
        x0 = max(panel_margin, width - content_width - panel_margin)
        y0 = height - panel_height - panel_margin

        context.set_source_rgba(1.0, 1.0, 1.0, 0.9)
        context.rectangle(x0, y0, content_width, panel_height)
        context.fill()
        context.set_source_rgba(0.18, 0.18, 0.18, 0.8)
        context.set_line_width(1.0)
        context.rectangle(x0, y0, content_width, panel_height)
        context.stroke()

        row_y = y0 + pad_top

        def draw_swatch(sx, hsl, label):
            r, g, b = self.hsl_to_rgb(*hsl)
            context.set_source_rgb(r, g, b)
            context.rectangle(x0 + sx, row_y, swatch_size, swatch_size)
            context.fill()
            context.set_source_rgb(0.1, 0.1, 0.1)
            context.set_line_width(0.75)
            context.rectangle(x0 + sx, row_y, swatch_size, swatch_size)
            context.stroke()
            context.move_to(x0 + sx + swatch_size + swatch_gap, row_y + swatch_size - 1)
            context.show_text(label)

        draw_swatch(ground_x, COLOR_BY_ALT['ground'], ground_label)
        draw_swatch(unknown_x, COLOR_BY_ALT['unknown'], unknown_label)

        gradient = cairo.LinearGradient(x0 + bar_x, 0, x0 + bar_x + bar_width, 0)
        samples = 16
        for i in range(samples + 1):
            frac = i / samples
            h, s, l = self.get_altitude_hsl({'alt_baro': frac * max_alt})
            r, g, b = self.hsl_to_rgb(h, s, l)
            gradient.add_color_stop_rgb(frac, r, g, b)
        context.set_source(gradient)
        context.rectangle(x0 + bar_x, row_y, bar_width, bar_height)
        context.fill()
        context.set_source_rgb(0.1, 0.1, 0.1)
        context.set_line_width(0.75)
        context.rectangle(x0 + bar_x, row_y, bar_width, bar_height)
        context.stroke()

        context.set_font_size(7)
        for alt in tick_alts:
            frac = min(1.0, alt / max_alt)
            tx = x0 + bar_x + frac * bar_width
            context.set_source_rgb(0.2, 0.2, 0.2)
            context.move_to(tx, row_y + bar_height)
            context.line_to(tx, row_y + bar_height + 2)
            context.stroke()
            label = f'{alt // 1000}k' if alt else '0'
            tw = context.text_extents(label)[2]
            lx = min(max(tx - tw / 2.0, x0 + bar_x), x0 + bar_x + bar_width - tw)
            context.move_to(lx, row_y + bar_height + 9)
            context.show_text(label)

        context.restore()

    def compute_map_zoom(self, width, height, points):
        if not points:
            return 8
        mercators = [self.latlon_to_mercator(lat, lon) for lat, lon in points]
        xs = [p[0] for p in mercators]
        ys = [p[1] for p in mercators]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max_x - min_x
        span_y = max_y - min_y
        if span_x <= 0:
            span_x = 0.001
        if span_y <= 0:
            span_y = 0.001
        span_x *= 1.2
        span_y *= 1.2
        zoom_x = math.log2(width / (span_x * 256.0))
        zoom_y = math.log2(height / (span_y * 256.0))
        return max(2, min(18, int(math.floor(min(zoom_x, zoom_y)))))

    def on_zoom_button_clicked(self, button, delta):
        base_zoom = self.map_zoom if self.map_zoom is not None else 8
        self.map_zoom = max(2, min(18, base_zoom + delta))
        self.zoom_label.set_text(f'Zoom: {self.map_zoom}')
        self.map_area.queue_draw()

    def on_reset_zoom_clicked(self, button):
        self.map_zoom = 8
        self.zoom_label.set_text(f'Zoom: {self.map_zoom}')
        self.map_area.queue_draw()


def main():
    app = FlightAwareDisplay()
    return app.run(None)


if __name__ == '__main__':
    raise SystemExit(main())
