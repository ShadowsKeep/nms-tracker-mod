"""Offline tests for mod/tab.py (the REPAIRS tab), against fake game memory.

A fake frontend manager, tab list (Exosuit, Starship, Multi-Tool) and tab elements are built in
ctypes buffers. Game calls are intercepted before they would reach NMS.exe: ctypes' argument
conversion runs on the exact arguments, then the fake answers like the game. Page switches behave
like the game's (0x8F70F7 / 0x8F7860): OpenPage only starts a switch; the current page changes a
couple of frames later. That delay is what broke A/D in the first in-game run of spike S3c.
"""
import ctypes
import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('PYTEST_VERSION', '1')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from pymhf.core.functions import _get_funcdef
    from pymhf.core.hooking import FunctionHook

    from mod import game, tab
    HAVE_NMSPY = True
except ImportError:                                # pragma: no cover - NMS.py not installed
    HAVE_NMSPY = False

PAGES = [(0, 'SUIT'), (1, 'SHIP'), (4, 'WEAPON')]


@unittest.skipUnless(HAVE_NMSPY, 'NMS.py not installed')
class RepairsTabTest(unittest.TestCase):
    def setUp(self):
        self.keep = []
        L = tab.nms.cGcNGuiLayer
        self._saved = (game.gameData.GcApplication, FunctionHook._call, L.FindLayerRecursive,
                       L.FindTextSpecialRecursive, game.translate_key)
        data = self.buf(tab.MANAGER_OFFSET + tab.LIST_FROM_MANAGER + 0x40)
        game.gameData.GcApplication = SimpleNamespace(mpData=ctypes.c_void_p(data))   # as NMS.py sets it
        self.mgr = data + tab.MANAGER_OFFSET
        self.page = self.mgr + tab.PAGE_FROM_MANAGER
        self.list = self.mgr + tab.LIST_FROM_MANAGER
        entries = self.buf(len(PAGES) * tab.TAB_ENTRY_SIZE)
        for i, (pg, key) in enumerate(PAGES):
            ctypes.c_int32.from_address(entries + i * tab.TAB_ENTRY_SIZE).value = pg
            ctypes.memmove(entries + i * tab.TAB_ENTRY_SIZE + 8, key.encode(), len(key))
        ctypes.c_uint32.from_address(self.list).value = len(PAGES)
        ctypes.c_uint32.from_address(self.list + 4).value = len(PAGES)
        ctypes.c_uint64.from_address(self.list + 8).value = entries
        self.field(tab.PENDING_PAGE).value = -1

        self.texts = {}
        vtable = self.buf(0x100)
        cb = tab.SetTextFn(lambda e, t: self.texts.__setitem__(e, t))
        self.keep.append(cb)
        ctypes.c_uint64.from_address(vtable + tab.SET_TEXT_SLOT).value = ctypes.cast(cb, ctypes.c_void_p).value
        self.E = {}
        for i in range(1, 5):
            for s in ('', '_OFF', '_OFF_S'):
                e, d = self.buf(0x60), self.buf(0x68)
                ctypes.c_uint64.from_address(e).value = vtable
                ctypes.c_uint64.from_address(e + 0x48).value = d
                ctypes.c_uint8.from_address(d + 0x61).value = 1
                self.E[f'OPTION{i}{s}'] = e

        bar = tab.nms.cGcNGuiLayer.from_buffer(bytearray(ctypes.sizeof(tab.nms.cGcNGuiLayer)))
        self.keep.append(bar)
        L.FindLayerRecursive = lambda s, ident: bar if ident == 'PAGESELECTBAR' else None
        L.FindTextSpecialRecursive = lambda s, ident: (
            tab.nms.cGcNGuiElement.from_address(self.E[ident]) if ident in self.E else None)
        game.translate_key = lambda key: {'SHIP': 'STARSHIP'}.get(key, '')
        test = self

        def intercept(hook, *args, **kwargs):     # a plain function, so it binds to the hook like _call does
            return test.fake_call(hook, *args, **kwargs)
        FunctionHook._call = intercept
        self.requests, self.changes = [], []
        self.tab = tab.RepairsTab(logging.getLogger('test'), lambda on, why: self.changes.append(on))
        self.set_page(0)

    def tearDown(self):
        L = tab.nms.cGcNGuiLayer
        (game.gameData.GcApplication, FunctionHook._call, L.FindLayerRecursive, L.FindTextSpecialRecursive,
         game.translate_key) = self._saved

    # ── fake game ────────────────────────────────────────────────────────────
    def buf(self, n):
        b = ctypes.create_string_buffer(n)
        self.keep.append(b)
        return ctypes.addressof(b)

    def field(self, off):
        return ctypes.c_int32.from_address(self.mgr + off)

    def set_page(self, p):
        self.field(tab.CURRENT_PAGE).value = p
        self.field(tab.STATE).value = 4

    def fake_call(self, hook, *args, **kwargs):
        fd = _get_funcdef(hook._func)
        flat = fd.flatten(*args, **kwargs)
        for t, a in zip(fd.arg_types, flat):
            t.from_param(a)
        name = hook._func.__name__
        if name == 'RequestPage':
            self.requests.append(flat[1])
            self.open_page(flat[1])
            return None
        raise AssertionError(name)

    def open_page(self, p, result=True):
        args = self.tab.before_open(self.mgr, p, True) or (self.mgr, p, True)
        final = args[1]
        if result and final != self.field(tab.CURRENT_PAGE).value:
            self.field(tab.STATE).value = tab.SWITCHING
            self.field(tab.TARGET_PAGE).value = final
        self.tab.after_open(self.mgr, p, True, result)
        return final

    def frame(self):
        self.tab.before_draw(self.page, 0x1000, self.list, True)
        for n, e in self.E.items():                                 # the game hides unused slots
            if n.startswith('OPTION4'):
                ctypes.c_uint8.from_address(ctypes.c_uint64.from_address(e + 0x48).value + 0x61).value = 1
        self.tab.after_draw(self.page, 0x1000, self.list, True)
        for e in self.E.values():
            ctypes.c_uint8.from_address(e + 0x50).value = 0         # a click lasts one frame

    def switch(self):
        self.frame(); self.frame()
        if self.field(tab.STATE).value == tab.SWITCHING:
            self.set_page(self.field(tab.TARGET_PAGE).value)
        self.frame()

    def prev_next(self, going_next):
        self.tab.before_prev_next(self.mgr, going_next)
        pages = [p for p, _ in PAGES]
        cur = self.field(tab.CURRENT_PAGE).value
        final = self.open_page(pages[(pages.index(cur) + (1 if going_next else -1)) % len(pages)])
        self.tab.after_prev_next(self.mgr, going_next)
        return final

    def click(self, name):
        ctypes.c_uint8.from_address(self.E[name] + 0x50).value = tab.CLICKED

    def hidden(self, name):
        return ctypes.c_uint8.from_address(ctypes.c_uint64.from_address(self.E[name] + 0x48).value + 0x61).value

    def selected_look(self):
        return (self.hidden('OPTION4') == 0 and self.hidden('OPTION4_OFF') == 1 and self.texts[self.E['OPTION4']] == b'REPAIRS'
                and self.hidden('OPTION2') == 1 and self.hidden('OPTION2_OFF') == 0
                and self.texts[self.E['OPTION2_OFF']] == b'STARSHIP')

    # ── behaviour ────────────────────────────────────────────────────────────
    def test_drawn_not_selected_after_the_last_tab(self):
        self.frame()
        self.assertEqual(self.texts[self.E['OPTION4_OFF']], b'REPAIRS')
        self.assertEqual((self.hidden('OPTION4_OFF'), self.hidden('OPTION4'), self.hidden('OPTION4_OFF_S')), (0, 1, 1))
        self.assertFalse(self.tab.active or self.tab.showing)

    def test_click_from_another_page_requests_starship_once(self):
        self.frame()
        self.click('OPTION4_OFF'); self.frame()
        self.assertEqual(self.requests, [1])
        self.assertTrue(self.tab.active)
        self.click('OPTION4_OFF'); self.frame()                     # again mid-switch: no second request
        self.assertEqual(self.requests, [1])
        self.switch()
        self.assertTrue(self.tab.active and self.tab.showing and self.selected_look())
        self.assertEqual(self.changes, [True])

    def test_click_on_the_starship_page_selects_directly(self):
        self.set_page(1)
        self.click('OPTION4_OFF'); self.frame()
        self.assertTrue(self.tab.active)
        self.assertEqual(self.requests, [])

    def test_a_and_d_onto_and_off_repairs(self):
        self.set_page(4)
        self.assertEqual(self.prev_next(True), 1)                   # D from Multi-Tool -> REPAIRS
        self.switch()
        self.assertTrue(self.tab.active and self.selected_look())   # stays through the switch
        self.assertEqual(self.prev_next(True), 0)                   # D from REPAIRS -> first tab
        self.switch()
        self.assertFalse(self.tab.active)
        self.assertEqual(self.prev_next(False), 1)                  # A from Exosuit -> REPAIRS
        self.switch()
        self.assertTrue(self.tab.active)
        self.assertEqual(self.prev_next(False), 4)                  # A from REPAIRS -> last tab
        self.switch()
        self.assertFalse(self.tab.active)

    def test_middle_tabs_are_left_alone(self):
        self.set_page(0)
        self.assertEqual(self.prev_next(True), 1)                   # Exosuit -> Starship: plain Starship
        self.switch()
        self.assertFalse(self.tab.active)

    def test_click_starship_while_on_repairs(self):
        self.set_page(1)
        self.click('OPTION4_OFF'); self.frame()
        self.click('OPTION2_OFF'); self.frame()
        self.assertFalse(self.tab.active)
        self.assertEqual(ctypes.c_uint8.from_address(self.E['OPTION2_OFF'] + 0x50).value, 0)

    def test_any_other_page_opening_leaves_repairs(self):
        self.set_page(1)
        self.click('OPTION4_OFF'); self.frame()
        self.open_page(0); self.switch()
        self.assertFalse(self.tab.active)

    def test_refused_page_request(self):
        FunctionHook._call = lambda hook, *a, **k: None             # RequestPage: the game defers it
        self.click('OPTION4_OFF')
        self.tab.before_draw(self.page, 0x1000, self.list, True)
        self.tab.after_draw(self.page, 0x1000, self.list, True)
        self.assertTrue(self.tab.entering)                          # asked; the game deferred it
        self.tab.before_open(self.mgr, 1, True)
        self.tab.after_open(self.mgr, 1, True, False)               # then refused
        self.assertFalse(self.tab.active or self.tab.entering)

    def test_pause_menu_tab_list_is_ignored(self):
        self.tab.after_draw(self.page, 0x1000, self.list + 0x10, True)
        self.assertEqual(self.texts, {})

    def test_disabled(self):
        self.tab.set_enabled(False)
        self.frame()
        self.assertEqual(self.texts, {})


if __name__ == '__main__':
    unittest.main()
