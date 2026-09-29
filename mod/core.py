"""
Repair planning for NMS Tracker Mod.

Pure logic with no game imports. The NMS.py layer reads the current ship and the player's
materials from the game and hands plain data to these functions, so everything here can be
tested without No Man's Sky running.

Costs always come from the game itself (whatever its own repair action would charge), never
from a bundled table, so the plan matches the game version being played.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Slot:
    """One damaged slot on the current ship."""
    key: object                 # what the game layer needs to find the slot again, e.g. (inventory, x, y)
    item_id: str                # game id of the damaged tech / damaged-slot marker in that slot
    name: str
    cost: tuple | None          # ((material_id, qty), ...) as the game reports it; None = unknown


@dataclass
class SlotPlan:
    slot: Slot
    affordable: bool            # could be repaired on its own with today's materials
    short_by: dict              # material_id -> how many more this slot needs on its own
    order: int | None = None    # position in the "repair all" batch, None when left out


@dataclass
class RepairPlan:
    slots: list                 # SlotPlan per damaged slot, in the order the game listed them
    batch: list                 # Slots to repair now, in this order
    uses: dict                  # material_id -> total the batch takes
    missing_for_all: dict       # material_id -> still needed to repair every slot with a known cost
    unknown: int = 0            # slots whose cost the game layer could not read
    total: int = field(init=False)

    def __post_init__(self):
        self.total = len(self.slots)

    @property
    def summary(self) -> str:
        if not self.total:
            return 'No damaged slots'
        ready = len(self.batch)
        if ready == self.total:
            text = f'All {self.total} damaged slots can be repaired now'
        elif ready:
            text = f'{ready} of {self.total} damaged slots can be repaired now'
        else:
            text = 'Nothing can be repaired yet'
        if self.unknown:
            text += f' ({self.unknown} with unknown cost)'
        return text


@dataclass
class PanelRow:
    label: str
    value: str
    fill: float                 # 0..100, the bar under the row


@dataclass
class Panel:
    """What the REPAIRS tab shows in the game's right-hand panel (title, one line, five rows)."""
    title: str
    subtitle: str
    rows: list


def repair_panel(board: 'Board', name=lambda gid: gid, max_rows: int = 5) -> Panel:
    """Turn the repair board into the panel text.

    Rows: parts you can repair now, parts waiting on materials, parts that need their own repair
    screen, then the materials you are shortest of for all the damage. Bars show each count's
    share of all damage, or for a material how much of it you already have.
    """
    total = len(board.parts)
    if total == 0:
        return Panel('Ship Repairs', 'No damage', [PanelRow('All parts working', '0', 100.0)])

    def count(how):
        return sum(1 for b in board.parts if b.how == how)

    def share(n):
        return round(100.0 * n / total, 1)

    rows = [PanelRow(label, str(count(how)), share(count(how)))
            for label, how in (('Repair now (F7)', 'now'), ('Need materials', 'materials'),
                               ('Need repair screen', 'screen'))]
    for m in board.materials:
        if len(rows) >= max_rows or m.missing <= 0:
            break
        rows.append(PanelRow(name(m.item_id), have_needed(m),
                             round(100.0 * m.have / m.needed, 1) if m.needed else 0.0))
    subtitle = f'{total} damaged part' + ('' if total == 1 else 's')
    return Panel('Ship Repairs', subtitle, rows[:max_rows])


@dataclass
class BoardPart:
    slot: Slot
    how: str                    # 'now' (F7 repairs it), 'materials' (F7 once you have them), 'screen'
    cost: dict                  # what is left to pay, {} when unknown


@dataclass
class BoardMaterial:
    item_id: str
    needed: int                 # for every damaged part with a known cost
    have: int

    @property
    def missing(self) -> int:
        return max(0, self.needed - self.have)


@dataclass
class Board:
    """The REPAIRS screen's two lists: every damaged part, and every material they need."""
    parts: list                 # BoardPart: repair now, then need materials, then need repair screen
    materials: list             # BoardMaterial: most missing first
    unknown: int = 0            # parts whose remaining cost could not be read


def have_needed(m: BoardMaterial) -> str:
    """'0 / 75': what the player holds / what all the damage needs (have capped at needed, so a
    big stock doesn't crowd the row)."""
    return f'{min(m.have, m.needed):,} / {m.needed:,}'


def board_text(board: Board, name=lambda gid: gid) -> str:
    """One log line with what the REPAIRS screen shows, so a test run's report can be checked."""
    counts = {h: sum(1 for b in board.parts if b.how == h) for h in ('now', 'materials', 'screen')}
    text = (f'{len(board.parts)} damaged: {counts["now"]} repair now, {counts["materials"]} need materials, '
            f'{counts["screen"]} need repair screen')
    if board.unknown:
        text += f', {board.unknown} with unknown cost'
    if board.materials:
        text += '; materials (have / needed): ' + ', '.join(
            f'{name(m.item_id)} {have_needed(m)}' for m in board.materials)
    return text


def repair_board(parts, in_place, inventory) -> Board:
    """`parts` are Slots with what is left to pay; `in_place` holds the keys F7 may repair from
    anywhere (the rest need their repair screen). Materials cover every part, F7 or not."""
    plan = plan_repairs([p for p in parts if p.key in in_place], inventory)
    now = {s.key for s in plan.batch}
    rank = {'now': 0, 'materials': 1, 'screen': 2}
    board_parts = []
    for p in parts:
        how = 'now' if p.key in now else 'materials' if p.key in in_place else 'screen'
        board_parts.append(BoardPart(p, how, merge_cost(p.cost)))
    board_parts.sort(key=lambda b: rank[b.how])            # stable: keeps the game's order inside a group
    stock = {m: max(0, int(n)) for m, n in (inventory or {}).items()}
    need = {}
    for b in board_parts:
        for m, q in b.cost.items():
            need[m] = need.get(m, 0) + q
    mats = [BoardMaterial(m, q, stock.get(m, 0)) for m, q in need.items()]
    mats.sort(key=lambda m: (-m.missing, -m.needed, m.item_id))
    return Board(board_parts, mats, unknown=sum(1 for p in parts if p.cost is None))


def merge_cost(cost) -> dict:
    """((id, qty), ...) -> {id: qty}, adding up repeats and dropping zero or negative amounts."""
    out = {}
    for material, qty in cost or ():
        qty = int(qty)
        if qty > 0:
            out[material] = out.get(material, 0) + qty
    return out


def _fits(cost: dict, stock: dict) -> bool:
    return all(stock.get(m, 0) >= q for m, q in cost.items())


def _pressure(cost: dict, stock: dict) -> float:
    """How much of what is left a repair would eat. Cheap-in-scarce-materials goes first."""
    return sum(q / stock[m] for m, q in cost.items())


def plan_repairs(slots, inventory) -> RepairPlan:
    """Work out which damaged slots can be repaired now and in what order.

    `inventory` is {material_id: count} for the places the game's repair draws from.
    Slots compete for shared materials, so the batch is built greedily: each step takes the
    affordable slot that uses the smallest share of the remaining stock, which repairs as
    many slots as possible in practice. Ties keep the game's own slot order.
    """
    stock = {m: max(0, int(n)) for m, n in (inventory or {}).items()}
    costs = [merge_cost(s.cost) if s.cost is not None else None for s in slots]

    plans = []
    need_all = {}
    for slot, cost in zip(slots, costs):
        if cost is None:
            plans.append(SlotPlan(slot, affordable=False, short_by={}))
            continue
        short = {m: q - stock.get(m, 0) for m, q in cost.items() if stock.get(m, 0) < q}
        plans.append(SlotPlan(slot, affordable=not short, short_by=short))
        for m, q in cost.items():
            need_all[m] = need_all.get(m, 0) + q

    left = dict(stock)
    candidates = [i for i, p in enumerate(plans) if p.affordable]
    batch = []
    while True:
        fitting = [i for i in candidates if _fits(costs[i], left)]
        if not fitting:
            break
        pick = min(fitting, key=lambda i: (_pressure(costs[i], left), i))
        for m, q in costs[pick].items():
            left[m] -= q
        plans[pick].order = len(batch)
        batch.append(slots[pick])
        candidates.remove(pick)

    uses = {m: stock[m] - left[m] for m in stock if stock[m] != left[m]}
    missing = {m: q - stock.get(m, 0) for m, q in need_all.items() if q > stock.get(m, 0)}
    unknown = sum(1 for c in costs if c is None)
    return RepairPlan(slots=plans, batch=batch, uses=uses, missing_for_all=missing, unknown=unknown)
