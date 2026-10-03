import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'NMSTrackerMod'))

from nmstracker.core import Slot, merge_cost, plan_repairs, repair_board, repair_panel  # noqa: E402


def slot(key, *cost, name=None):
    return Slot(key=key, item_id=f'DMG{key}', name=name or f'Slot {key}', cost=tuple(cost))


class PlanRepairsTest(unittest.TestCase):
    def test_empty_ship(self):
        plan = plan_repairs([], {'CARBON': 50})
        self.assertEqual(plan.batch, [])
        self.assertEqual(plan.summary, 'No damaged slots')
        self.assertEqual(plan.uses, {})

    def test_all_ready(self):
        slots = [slot(1, ('WIRE', 1)), slot(2, ('PLATE', 2))]
        plan = plan_repairs(slots, {'WIRE': 3, 'PLATE': 2})
        self.assertEqual(plan.batch, slots)
        self.assertEqual(plan.summary, 'All 2 damaged slots can be repaired now')
        self.assertEqual(plan.uses, {'WIRE': 1, 'PLATE': 2})
        self.assertEqual(plan.missing_for_all, {})

    def test_partly_short(self):
        slots = [slot(1, ('WIRE', 1)), slot(2, ('SEAL', 1)), slot(3, ('WIRE', 1))]
        plan = plan_repairs(slots, {'WIRE': 1})
        self.assertEqual(len(plan.batch), 1)
        self.assertEqual(plan.summary, '1 of 3 damaged slots can be repaired now')
        self.assertEqual(plan.slots[1].short_by, {'SEAL': 1})
        self.assertIsNone(plan.slots[1].order)
        self.assertEqual(plan.missing_for_all, {'WIRE': 1, 'SEAL': 1})

    def test_none_ready(self):
        slots = [slot(1, ('WIRE', 2), ('PLATE', 1))]
        plan = plan_repairs(slots, {'WIRE': 1})
        self.assertEqual(plan.batch, [])
        self.assertEqual(plan.summary, 'Nothing can be repaired yet')
        self.assertFalse(plan.slots[0].affordable)
        self.assertEqual(plan.slots[0].short_by, {'WIRE': 1, 'PLATE': 1})

    def test_shared_material_goes_furthest(self):
        big = slot('A', ('WIRE', 4))
        small1 = slot('B', ('WIRE', 2))
        small2 = slot('C', ('WIRE', 2))
        plan = plan_repairs([big, small1, small2], {'WIRE': 4})
        self.assertEqual(plan.batch, [small1, small2])
        self.assertTrue(plan.slots[0].affordable)       # affordable alone, but left out of the batch
        self.assertIsNone(plan.slots[0].order)
        self.assertEqual([p.order for p in plan.slots[1:]], [0, 1])

    def test_scarce_material_is_saved(self):
        # Repairing the first slot in game order would use up both materials and fix one slot;
        # skipping it fixes two.
        both = slot(1, ('SEAL', 1), ('WIRE', 1))
        seal = slot(2, ('SEAL', 1))
        wire = slot(3, ('WIRE', 1))
        plan = plan_repairs([both, seal, wire], {'SEAL': 1, 'WIRE': 1})
        self.assertEqual(plan.batch, [seal, wire])

    def test_ties_keep_game_order(self):
        slots = [slot(i, ('WIRE', 1)) for i in range(4)]
        plan = plan_repairs(slots, {'WIRE': 2})
        self.assertEqual(plan.batch, slots[:2])

    def test_unknown_cost_is_never_repaired(self):
        mystery = Slot(key=9, item_id='UP_X', name='Upgrade', cost=None)
        plan = plan_repairs([mystery, slot(1, ('WIRE', 1))], {'WIRE': 5})
        self.assertEqual(plan.batch, [plan.slots[1].slot])
        self.assertEqual(plan.unknown, 1)
        self.assertEqual(plan.summary, '1 of 2 damaged slots can be repaired now (1 with unknown cost)')

    def test_free_repair_is_included(self):
        plan = plan_repairs([slot(1)], {})
        self.assertEqual(len(plan.batch), 1)

    def test_inventory_is_not_mutated(self):
        stock = {'WIRE': 3}
        plan_repairs([slot(1, ('WIRE', 2))], stock)
        self.assertEqual(stock, {'WIRE': 3})

    def test_negative_or_bad_stock(self):
        plan = plan_repairs([slot(1, ('WIRE', 1))], {'WIRE': -4})
        self.assertEqual(plan.batch, [])
        self.assertEqual(plan.slots[0].short_by, {'WIRE': 1})


class RepairBoardTest(unittest.TestCase):
    def test_groups_and_materials(self):
        parts = [Slot('a', 'A', 'A', (('W', 5),)), Slot('b', 'B', 'B', (('W', 5), ('X', 2))),
                 Slot('c', 'C', 'C', None), Slot('d', 'D', 'D', (('X', 1),))]
        board = repair_board(parts, in_place={'a', 'b', 'd'}, inventory={'W': 6, 'X': 1})
        # a and b compete for W: one fits; d fits; c needs its repair screen (and has no known cost)
        self.assertEqual([(b.slot.key, b.how) for b in board.parts],
                         [('a', 'now'), ('d', 'now'), ('b', 'materials'), ('c', 'screen')])
        self.assertEqual([(m.item_id, m.needed, m.have, m.missing) for m in board.materials],
                         [('W', 10, 6, 4), ('X', 3, 1, 2)])
        self.assertEqual(board.unknown, 1)

    def test_empty(self):
        board = repair_board([], set(), {})
        self.assertEqual((board.parts, board.materials, board.unknown), ([], [], 0))


class RepairPanelTest(unittest.TestCase):
    def test_no_damage(self):
        panel = repair_panel(repair_board([], set(), {}))
        self.assertEqual((panel.title, panel.subtitle), ('Ship Repairs', 'No damage'))

    def test_rows_and_bars(self):
        slots = [slot(1, ('WIRE', 1)), slot(2, ('SEAL', 3)), slot(3, ('SEAL', 2), ('CARBON', 50)),
                 slot(4, ('SEAL', 1))]                                   # 4 needs its repair screen
        board = repair_board(slots, {1, 2, 3}, {'WIRE': 1, 'SEAL': 1, 'CARBON': 10})
        panel = repair_panel(board, name={'SEAL': 'Hermetic Seal', 'CARBON': 'Carbon'}.get)
        self.assertEqual(panel.subtitle, '4 damaged parts')
        self.assertEqual([(r.label, r.value) for r in panel.rows], [
            ('Repair now (F7)', '1'),
            ('Need materials', '2'),
            ('Need repair screen', '1'),
            ('Carbon', '10 / 50'),              # has 10, all the damage needs 50
            ('Hermetic Seal', '1 / 6'),         # the repair-screen part counts too
        ])
        self.assertEqual([r.fill for r in panel.rows], [25.0, 50.0, 25.0, 20.0, 16.7])

    def test_have_needed_text(self):
        from nmstracker.core import BoardMaterial, have_needed
        self.assertEqual(have_needed(BoardMaterial('CU', 75, 0)), '0 / 75')
        self.assertEqual(have_needed(BoardMaterial('CU', 1500, 1200)), '1,200 / 1,500')
        self.assertEqual(have_needed(BoardMaterial('CU', 75, 9000)), '75 / 75')    # capped: enough

    def test_board_log_line(self):
        from nmstracker.core import board_text
        board = repair_board([slot(1, ('CU', 75)), Slot(2, 'DMG2', 'Slot 2', None), slot(3, ('SEAL', 1))],
                             {1}, {'SEAL': 1})
        self.assertEqual(board_text(board, lambda g: {'CU': 'Activated Copper'}.get(g, g)),
                         '3 damaged: 0 repair now, 1 need materials, 2 need repair screen, 1 with unknown cost; '
                         'materials (have / needed): Activated Copper 0 / 75, SEAL 1 / 1')
        self.assertEqual(board_text(repair_board([], set(), {})),
                         '0 damaged: 0 repair now, 0 need materials, 0 need repair screen')

    def test_nothing_short_means_no_short_rows(self):
        board = repair_board([slot(1, ('WIRE', 1))], {1}, {'WIRE': 5})
        self.assertEqual([r.label for r in repair_panel(board).rows],
                         ['Repair now (F7)', 'Need materials', 'Need repair screen'])

    def test_one_part_is_singular_and_rows_are_capped(self):
        board = repair_board([slot(1, *[(f'M{i}', 1) for i in range(6)])], {1}, {})
        panel = repair_panel(board, name=lambda g: g)
        self.assertEqual(panel.subtitle, '1 damaged part')
        self.assertEqual(len(panel.rows), 5)


class MergeCostTest(unittest.TestCase):
    def test_adds_repeats_and_drops_zero(self):
        self.assertEqual(merge_cost([('A', 1), ('B', 0), ('A', '2'), ('C', -1)]), {'A': 3})

    def test_none(self):
        self.assertEqual(merge_cost(None), {})


if __name__ == '__main__':
    unittest.main()
