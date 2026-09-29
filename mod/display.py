"""
Display-only inventory stores for the REPAIRS screen's two grids (damaged parts, materials).

Each DisplayStore is a cGcInventoryStore (0x248 bytes, NMS.py nmspy.data.types) in memory the mod
owns, holding copies of elements. The game only ever gets its address to draw it, locked and with
action mask 0 (see mod/grids.py and work/re/notes_inventory_grid.txt).

- The store is built ONCE by the game's own constructor (RVA 0x4CA2A0, allocates nothing). Its
  hash map at +0x208 needs self-pointers (+0x218 = +0x220 = store+0x230) that the grid reads for
  every technology element; a zero-filled store crashes the game there. `ready` checks them.
- After that only these fields are written, in place, every time the grid is drawn: valid-slot
  bits (only occupied slots), width, height (<= 16 rows), capacity, the element vector, and empty
  history / special-slot vectors. The map, layout and everything else stay as the game built them.
- The element buffer is allocated once and never freed while the mod runs, and nothing in the store
  points into the game's own data.

Layout (cGcInventoryStore; cGcInventoryElement 0x30, same as NMS.py):
  +0x00 16 x u64 valid-slot bits: row y at +y*8, bit x    +0x80 i16 width, +0x82 height, +0x84 capacity
  +0x88 tk_vector<element> {u32 allocated, u32 count @+0x8C, ptr @+0x90}   +0x98 history {.., count @+0x9C, ptr}
  +0xB8 i16 stack size group    +0xC0 special slots {.., count @+0xC4, ptr @+0xC8}    +0x100 i32 class
  +0x208 hash map: +0x210 seed, +0x218/+0x220 -> store+0x230, +0x240 = 0x20 (ctor)
  element: +0x00 TkID16 id, +0x10 i32 x, +0x14 i32 y, +0x18 i32 amount, +0x1C f32 damage,
           +0x20 i32 max amount, +0x24 u32 type, +0x28 added automatically, +0x29 fully installed
"""
import ctypes
from dataclasses import dataclass

STORE_SIZE = 0x248
ELEMENT_SIZE = 0x30
MAX_ROWS = 16                     # the valid-slot bits cover 16 rows
CAPACITY = 16 * 16                # elements the buffer can hold
MAP_SEED = 0xC4CEB9FE1A85EC53     # what the constructor writes at +0x210


@dataclass(frozen=True)
class Item:
    """One slot to show: copied from a real element, or made up for a material."""
    gid: str
    amount: int
    max_amount: int
    damage: float
    kind: int                     # cGcInventoryType: 0 substance, 1 technology, 2 product
    installed: bool = True


def construct_like_game(addr: int):
    """What the game's constructor 0x4CA2A0 leaves in a store (static scan), for offline tests and
    as a fallback if calling the constructor fails. The layout at +0xE0 is left zero."""
    ctypes.memset(addr, 0, STORE_SIZE)
    ctypes.c_int16.from_address(addr + 0x80).value = 1
    ctypes.c_int16.from_address(addr + 0x82).value = 1
    ctypes.c_int16.from_address(addr + 0x84).value = 1
    ctypes.c_uint8.from_address(addr + 0xF8).value = 1
    ctypes.c_uint64.from_address(addr + 0x210).value = MAP_SEED
    ctypes.c_uint64.from_address(addr + 0x218).value = addr + 0x230
    ctypes.c_uint64.from_address(addr + 0x220).value = addr + 0x230
    ctypes.c_uint64.from_address(addr + 0x240).value = 0x20


class DisplayStore:
    def __init__(self, capacity: int = CAPACITY):
        self.capacity = capacity
        self._store = ctypes.create_string_buffer(STORE_SIZE + 0x10)       # + slack; 16-aligned below
        self.address = (ctypes.addressof(self._store) + 0xF) & ~0xF
        self._elements = ctypes.create_string_buffer(ELEMENT_SIZE * capacity)
        self.items = []
        self.built = False

    def build(self, construct) -> bool:
        """Run the game's constructor (or construct_like_game) on the store, once."""
        if not self.built:
            construct(self.address)
            self.built = True
        return self.ready

    @property
    def ready(self) -> bool:
        """The constructor ran: the map's self-pointers are in place (the grid reads them)."""
        a = self.address
        return (self.built and ctypes.c_uint64.from_address(a + 0x218).value == a + 0x230
                and ctypes.c_uint64.from_address(a + 0x220).value == a + 0x230)

    def set_items(self, items):
        self.items = list(items)

    def apply(self, width: int) -> int:
        """Write the items into the store for a grid `width` columns wide (the grid's own column
        count). Called right before every draw, so anything the game changed is put back.
        Returns how many are shown (at most width x 16)."""
        width = max(1, min(int(width), 64))
        items = self.items[:min(self.capacity, width * MAX_ROWS)]
        rows = max(1, -(-len(items) // width))
        s, e0 = self.address, ctypes.addressof(self._elements)
        valid = [0] * 16
        ctypes.memset(e0, 0, ELEMENT_SIZE * len(items))
        for i, it in enumerate(items):
            x, y = i % width, i // width
            valid[y] |= 1 << x
            e = e0 + i * ELEMENT_SIZE
            ctypes.memmove(e, it.gid.encode('ascii', 'replace')[:15].ljust(0x10, b'\0'), 0x10)
            ctypes.c_int32.from_address(e + 0x10).value = x
            ctypes.c_int32.from_address(e + 0x14).value = y
            ctypes.c_int32.from_address(e + 0x18).value = it.amount
            ctypes.c_float.from_address(e + 0x1C).value = it.damage
            ctypes.c_int32.from_address(e + 0x20).value = it.max_amount
            ctypes.c_uint32.from_address(e + 0x24).value = it.kind
            ctypes.c_uint8.from_address(e + 0x29).value = int(it.installed)
        for y in range(16):
            ctypes.c_uint64.from_address(s + y * 8).value = valid[y]
        ctypes.c_int16.from_address(s + 0x80).value = width
        ctypes.c_int16.from_address(s + 0x82).value = rows
        ctypes.c_int16.from_address(s + 0x84).value = width * rows
        ctypes.c_uint32.from_address(s + 0x88).value = self.capacity
        ctypes.c_uint32.from_address(s + 0x8C).value = len(items)
        ctypes.c_uint64.from_address(s + 0x90).value = e0
        ctypes.memset(s + 0x98, 0, 0x10)          # history: empty
        ctypes.memset(s + 0xC0, 0, 0x10)          # special slots: empty
        ctypes.memset(s + 0xD0, 0, 0x10)          # base stats: empty
        return len(items)

    def set_style(self, stack_group: int, inv_class: int):
        ctypes.c_int16.from_address(self.address + 0xB8).value = stack_group
        ctypes.c_int32.from_address(self.address + 0x100).value = inv_class


def part_items(parts, elements_by_key) -> list:
    """The damaged parts as they are in the ship's stores (same id, amount, damage, installed), so
    the grid draws them exactly like the Starship grid does. `elements_by_key` maps a part's key
    (group, x, y) to its row from game.elements()."""
    out = []
    for b in parts:
        r = elements_by_key.get(b.slot.key)
        if r is None:
            continue
        gid, _x, _y, amount, dmg, kind, installed = r
        out.append(Item(gid, amount, 100, dmg, kind, installed))
    return out


def material_items(materials, kind_of) -> list:
    """The materials all the damage needs: amount = what the player has (capped at needed),
    max amount = needed, so the slot reads and fills as have / needed. Materials whose type the
    game's tables don't know are left out (no icon to draw)."""
    out = []
    for m in materials:
        kind = kind_of(m.item_id)
        if kind is None:
            continue
        out.append(Item(m.item_id, min(m.have, m.needed), m.needed, 0.0, kind, True))
    return out
