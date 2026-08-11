#!/usr/bin/env python3
import json
import math
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import gi
import os
import base64

gi.require_version('Gtk', '4.0')
gi.require_version('GLib', '2.0')
gi.require_version('Gio', '2.0')
gi.require_version('Pango', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gio, GLib, Gtk, Pango
from gi.repository import GdkPixbuf, GObject

RECEIVER_URL = 'http://flightaware.airwisp.net:8080/data/receiver.json'
AIRCRAFT_URL = 'http://flightaware.airwisp.net:8080/data/aircraft.json'

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
        'ICAO': 90,
        'Ident': 100,
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
        self.refresh_source = None

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

        map_frame = Gtk.Frame(label='Map')
        map_frame.set_child(self.map_area)
        map_frame.set_vexpand(True)
        map_frame.set_hexpand(True)

        # Use a ListView backed by a Gio.ListStore for proper tabular behavior
        self.columns = COLUMNS
        self.size_groups = {col: Gtk.SizeGroup.new(Gtk.SizeGroupMode.HORIZONTAL) for col in self.columns}
        self.list_store = Gio.ListStore.new(AircraftRow)
        selection = Gtk.SingleSelection.new(self.list_store)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.on_factory_setup)
        factory.connect("bind", self.on_factory_bind)

        self.aircraft_list = Gtk.ListView.new(selection, factory)
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
    color: #7FBFFF;
    font-family: system-ui, sans-serif;
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
        width_map = self.COLUMN_WIDTHS
        left_cols = set(['Ident', 'Tail', 'Type'])
        center_cols = set(['Flag'])
        for idx, value in enumerate(self.columns):
            label = Gtk.Label(xalign=0.0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(False)
            label.set_halign(Gtk.Align.FILL)
            if value in NUMERIC_COLUMNS:
                label.set_xalign(1.0)
                label.set_justify(Gtk.Justification.RIGHT)
            elif value in left_cols:
                label.set_xalign(0.0)
                label.set_justify(Gtk.Justification.LEFT)
            elif value in center_cols:
                label.set_xalign(0.5)
                label.set_justify(Gtk.Justification.CENTER)
            else:
                label.set_xalign(0.5)
                label.set_justify(Gtk.Justification.CENTER)
            label.add_css_class('heading')
            escaped = GLib.markup_escape_text(str(value))
            label.set_markup(f'<span foreground="#07385A" weight="bold">{escaped}</span>')
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

    def update_data(self, receiver, aircraft):
        if receiver:
            self.receiver = receiver
            self.poll_interval = int(receiver.get('refresh', self.poll_interval) / 1000) or self.poll_interval

        if aircraft and 'aircraft' in aircraft:
            self.aircraft = aircraft['aircraft']

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
            reg = registration_from_hex(item.get('hex'))
            return str(reg).strip() if reg else PLACEHOLDER
        if key in ('type', 'aircraft type'):
            val = item.get('type') or item.get('category') or item.get('model')
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
        context.set_source_rgb(1, 1, 1)
        context.paint()

        center_lat = self.receiver.get('lat')
        center_lon = self.receiver.get('lon')
        if center_lat is None or center_lon is None:
            context.set_source_rgb(0, 0, 0)
            context.select_font_face('Sans', 0, 0)
            context.set_font_size(18)
            context.move_to(width * 0.1, height * 0.5)
            context.show_text('Receiver location unavailable')
            return

        coords = []
        max_dx = 0.0
        max_dy = 0.0
        for item in self.aircraft:
            lat = item.get('lat')
            lon = item.get('lon')
            if lat is None or lon is None:
                continue
            dx, dy = self.latlon_to_meters(lat, lon, center_lat, center_lon)
            coords.append((dx, dy, item))
            max_dx = max(max_dx, abs(dx))
            max_dy = max(max_dy, abs(dy))

        if max_dx == 0 and max_dy == 0:
            max_span = 5000.0
        else:
            max_span = max(max_dx, max_dy)

        scale = min((width * 0.45) / max_span, (height * 0.45) / max_span)
        if scale <= 0:
            scale = 1.0

        cx = width / 2
        cy = height / 2

        context.set_source_rgb(0.9, 0.9, 0.9)
        context.rectangle(0, 0, width, height)
        context.fill()

        context.set_source_rgb(0.8, 0.8, 0.95)
        context.arc(cx, cy, 4, 0, 2 * math.pi)
        context.fill()

        context.set_source_rgb(0.2, 0.2, 0.2)
        for radius in (20000, 40000, 60000):
            r = radius * scale
            context.arc(cx, cy, r, 0, 2 * math.pi)
            context.stroke()

        for dx, dy, item in coords:
            x = cx + dx * scale
            y = cy - dy * scale
            context.set_source_rgb(0.8, 0.1, 0.1)
            context.arc(x, y, 4, 0, 2 * math.pi)
            context.fill()

        context.set_source_rgb(0, 0, 0)
        context.select_font_face('Sans', 0, 0)
        context.set_font_size(12)
        context.move_to(10, 20)
        context.show_text(f'Receiver: {center_lat:.5f}, {center_lon:.5f}')

    @staticmethod
    def latlon_to_meters(lat, lon, center_lat, center_lon):
        lat_m = (lat - center_lat) * 111320.0
        lon_m = (lon - center_lon) * 111320.0 * math.cos(math.radians(center_lat))
        return lon_m, lat_m


def main():
    app = FlightAwareDisplay()
    return app.run(None)


if __name__ == '__main__':
    raise SystemExit(main())
