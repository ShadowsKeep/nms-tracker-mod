# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = true
# start_paused = true
# default_mod_save_dir = "{CURR_DIR}"
#
# [tool.pymhf.gui]
# shown = false
#
# [tool.pymhf.logging]
# shown = false
# log_dir = "{CURR_DIR}"
# log_level = "info"
# ///
"""
Spike S3a: find out how the inventory screen is built, so the tab can copy it. READ-ONLY.

Hooks the game's menu-layout loader (cGcNGuiLayer::LoadFromMetadata) only to watch it, and
reads the element tree of each layout right after the game builds it: element type (from the
game's own RTTI), ID, position, size, hidden flag, text, and child layouts. Calls no game
function except the on-screen message on F8. No windows; output goes to this folder.

Run (cmd or PowerShell, game closed), from this folder:
    pymhf run s3_ui_discovery.py
Load a save, open the inventory, click through its tabs (Exosuit, Starship, Multi-Tool,
Freighter...), open a damaged part's repair screen, then press F8. Output: s3-ui-<time>.json
"""
import ctypes
import json
import logging
import sys
import time
from pathlib import Path

from pymhf import Mod
from pymhf.core import _internal
from pymhf.core.hooking import on_key_pressed
from pymhf.core.memutils import map_struct
from pymhf.gui.decorators import no_gui

import nmspy.data.types as nms
from nmspy.decorators import main_loop, on_state_change

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mod import game  # noqa: E402  (only for the on-screen message)

logger = logging.getLogger('S3')
KEYWORDS = ('INVENTORY', 'TAB', 'SHIP', 'EQUIP', 'REPAIR', 'MAINTENANCE', 'SLOT')
MAX_TREES = 250        # distinct layout files (each stored once)
MAX_NODES = 3000
USER_MIN, USER_MAX = 0x10000, 0x7FFFFFFFFFFF


def ok_ptr(p) -> bool:
    return isinstance(p, int) and USER_MIN <= p <= USER_MAX and p % 8 == 0


def rtti_name(obj_addr: int) -> str:
    """Class name from MSVC RTTI: vtable[-1] -> CompleteObjectLocator -> TypeDescriptor name."""
    try:
        base = _internal.BASE_ADDRESS
        vtable = ctypes.c_uint64.from_address(obj_addr).value
        if not ok_ptr(vtable):
            return '?'
        col = ctypes.c_uint64.from_address(vtable - 8).value
        if not ok_ptr(col):
            return '?'
        td_rva = ctypes.c_uint32.from_address(col + 12).value
        name = ctypes.string_at(base + td_rva + 16)[:96].decode('ascii', 'replace')   # up to the NUL
        return name.replace('.?AV', '').replace('@@', '') or '?'
    except Exception:
        return '?'


def vstring(vs) -> str:
    try:
        return str(vs)
    except Exception:
        return ''


def element_info(addr: int) -> dict:
    info = {'type': rtti_name(addr)}
    try:
        el = map_struct(addr, nms.cGcNGuiElement)
        data_ptr = ctypes.c_uint64.from_address(addr + 0x48).value
        if ok_ptr(data_ptr):
            data = el.mpElementData.contents
            lay = data.Layout
            info.update({
                'id': str(data.ID).strip('\x00 '),
                'hidden': bool(data.IsHidden),
                'x': round(float(lay.PositionX), 2), 'y': round(float(lay.PositionY), 2),
                'w': round(float(lay.Width), 2), 'h': round(float(lay.Height), 2),
                'w_pct': bool(lay.WidthPercentage), 'h_pct': bool(lay.HeightPercentage),
            })
        if info['type'] == 'cGcNGuiText':
            text_ptr = ctypes.c_uint64.from_address(addr + 0x188).value
            if ok_ptr(text_ptr):
                text = map_struct(addr, nms.cGcNGuiText).mpTextData.contents
                info['text'] = vstring(text.Text)[:120]
                info['image'] = vstring(text.Image)[:120]
    except Exception as e:
        info['error'] = repr(e)[:80]
    return info


def dump_layer(addr: int, budget: list, depth: int = 0, seen=None) -> dict:
    seen = set() if seen is None else seen
    node = element_info(addr)
    if addr in seen or depth > 14 or budget[0] <= 0:
        node['truncated'] = True
        return node
    seen.add(addr)
    budget[0] -= 1
    try:
        layer = map_struct(addr, nms.cGcNGuiLayer)
        layer_data_ptr = ctypes.c_uint64.from_address(addr + 0x148).value
        if ok_ptr(layer_data_ptr):
            node['file'] = vstring(layer.mpLayerData.contents.DataFilename)[:160]
        elements = layer.mapElements
        sublayers = layer.mapLayerElements
        if len(elements) > 1024 or len(sublayers) > 1024:
            node['error'] = 'implausible child count'
            return node
        layer_addrs = {ctypes.cast(p, ctypes.c_void_p).value for p in sublayers}
        children = []
        for p in elements:
            child = ctypes.cast(p, ctypes.c_void_p).value
            if not ok_ptr(child):
                continue
            if child in layer_addrs or rtti_name(child) == 'cGcNGuiLayer':
                children.append(dump_layer(child, budget, depth + 1, seen))
            else:
                budget[0] -= 1
                children.append(element_info(child))
        for child in layer_addrs - {ctypes.cast(p, ctypes.c_void_p).value for p in elements}:
            if ok_ptr(child):
                children.append(dump_layer(child, budget, depth + 1, seen))
        node['children'] = children
    except Exception as e:
        node['error'] = repr(e)[:80]
    return node


PAGES = ('INVENTORYPAGE', 'MAINTENANCEPAGE', 'POPUP_INVENTORY', 'UI/INVENTORY.MBIN')


def layer_id(addr: int) -> str:
    try:
        data_ptr = ctypes.c_uint64.from_address(addr + 0x48).value
        if ok_ptr(data_ptr):
            return str(map_struct(addr, nms.cGcNGuiElement).mpElementData.contents.ID).strip('\x00 ')
    except Exception:
        pass
    return ''


@no_gui
class S3UiDiscovery(Mod):
    __author__ = 'Shadowskeep LLC'
    __description__ = 'Spike S3a: record how the inventory screen is built (read-only)'
    __version__ = '0.3'

    def __init__(self):
        super().__init__()
        self.loads = {}           # "file|in_game" -> how many times the game loaded it
        self.trees = {}           # file -> element tree as built at load time (first load only)
        self.pages = {}           # page file -> (layer address, id) of the latest load, for live dumps
        self.in_game = False
        self._flush = False
        self.started = time.strftime('%Y%m%d-%H%M%S')
        self._saves = 0

    @on_state_change('APPVIEW')
    def entered_game(self):
        self.in_game = True
        logger.info('In game: each layout loaded from now on is recorded once')

    @nms.cGcNGuiLayer.LoadFromMetadata.after
    def after_load(self, this, lpacFilename, lbUseCached, a4):
        try:
            name = str(lpacFilename.contents).strip('\x00 ') if lpacFilename else ''
            if not name:
                return
            key = f'{name}|{"game" if self.in_game else "boot"}'
            self.loads[key] = self.loads.get(key, 0) + 1
            addr = ctypes.cast(this, ctypes.c_void_p).value
            if any(p in name.upper() for p in PAGES):
                self.pages[name] = (addr, layer_id(addr))
            wanted = self.in_game or any(k in name.upper() for k in KEYWORDS)
            if wanted and name not in self.trees and len(self.trees) < MAX_TREES:
                self.trees[name] = {'in_game': self.in_game, 'tree': dump_layer(addr, [MAX_NODES])}
        except Exception:
            logger.exception('after_load failed')

    @on_key_pressed('f8')
    def save_key(self):
        self._flush = True

    def live_pages(self) -> dict:
        """Re-read each page as it is now (tabs and slots the code filled in after loading).
        Only if the object at that address still looks like the same layer."""
        out = {}
        for name, (addr, ident) in self.pages.items():
            if rtti_name(addr) == 'cGcNGuiLayer' and layer_id(addr) == ident:
                out[name] = dump_layer(addr, [MAX_NODES * 2])
            else:
                out[name] = {'error': 'layer no longer valid at its old address; not read'}
        return out

    @main_loop.after
    def on_frame(self):
        if not self._flush:
            return
        self._flush = False
        self._saves += 1          # one file per F8, so a later F8 never overwrites an earlier screen
        path = HERE / f's3-ui-{self.started}-{self._saves:02d}.json'
        try:
            live = self.live_pages()
            path.write_text(json.dumps({'loads': self.loads, 'trees': self.trees, 'live_pages': live},
                                       indent=1), encoding='utf-8')
            text = f'S3: snapshot {self._saves} saved ({len(live)} live pages)'
            logger.info(f'{text} -> {path}')
            game.show_message(text)
        except Exception:
            logger.exception('Could not save the UI dump')
