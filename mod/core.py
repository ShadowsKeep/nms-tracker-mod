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
