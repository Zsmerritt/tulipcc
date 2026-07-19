# dx7algos.py -- DX7 FM algorithm routing, DERIVED from AMY's own algorithm
# table (feature #102). Pure + host-testable (no lvgl/amy import).
#
# DATA FOUNDATION. The 32 rows in _ALGO_BYTES are copied VERBATIM from
# amy/src/algorithms.c `const struct FmAlgorithm algorithms[41]`, indices 1..32
# (index 0 duplicates 1; indices 33..40 are AMY extensions past DX7's 32). Each
# row is six MSFA operator-flag bytes in the column order the C source labels
# `// 6 5 4 3 2 1` -- i.e. [op6, op5, op4, op3, op2, op1].
#
# We do NOT hand-type a DX7 routing chart (which could drift from what the
# engine renders). Instead routing() SIMULATES amy/src/algorithms.c
# render_algo()'s two-bus signal flow using the exact flag semantics, so the
# carriers / modulation edges / feedback the deck draws are whatever AMY
# actually plays. (Verified: this yields the canonical DX7 topologies -- e.g.
# algo 1 = carriers {1,3} with stacks 2->1 and 6->5->4->3, algo 5 = three 2-op
# stacks, algo 32 = six parallel carriers -- so AMY index N == DX7 algorithm N.)
#
# OSC MAPPING. render_algo pairs algo.ops[col] with algo_source[col], and
# fm.py sends algo_source="2,3,4,5,6,7" while ordering operators op6..op1 onto
# oscs 2..7 (fm.py: `for i in range(6,0,-1)` then `osc = 2 + index`). So
# DX7 op N lives on osc (8 - N): op1->osc7 ... op6->osc2. amyparams.FM_OP_OSCS
# encodes this so the editor's "OP N" page edits the operator this table calls
# op N.

# --- MSFA flag bits (amy/src/algorithms.c enum FmOperatorFlags) ---
OUT_BUS_ONE = 0x01
OUT_BUS_TWO = 0x02
OUT_BUS_ADD = 0x04
IN_BUS_ONE = 0x10
IN_BUS_TWO = 0x20
FB_IN = 0x40
FB_OUT = 0x80

NUM_ALGOS = 32

# Column order per row: [op6, op5, op4, op3, op2, op1]  (amy `// 6 5 4 3 2 1`).
_ALGO_BYTES = {
    1:  (0xc1, 0x11, 0x11, 0x14, 0x01, 0x14),
    2:  (0x01, 0x11, 0x11, 0x14, 0xc1, 0x14),
    3:  (0xc1, 0x11, 0x14, 0x01, 0x11, 0x14),
    4:  (0x41, 0x11, 0x94, 0x01, 0x11, 0x14),
    5:  (0xc1, 0x14, 0x01, 0x14, 0x01, 0x14),
    6:  (0x41, 0x94, 0x01, 0x14, 0x01, 0x14),
    7:  (0xc1, 0x11, 0x05, 0x14, 0x01, 0x14),
    8:  (0x01, 0x11, 0xc5, 0x14, 0x01, 0x14),
    9:  (0x01, 0x11, 0x05, 0x14, 0xc1, 0x14),
    10: (0x01, 0x05, 0x14, 0xc1, 0x11, 0x14),
    11: (0xc1, 0x05, 0x14, 0x01, 0x11, 0x14),
    12: (0x01, 0x05, 0x05, 0x14, 0xc1, 0x14),
    13: (0xc1, 0x05, 0x05, 0x14, 0x01, 0x14),
    14: (0xc1, 0x05, 0x11, 0x14, 0x01, 0x14),
    15: (0x01, 0x05, 0x11, 0x14, 0xc1, 0x14),
    16: (0xc1, 0x11, 0x02, 0x25, 0x05, 0x14),
    17: (0x01, 0x11, 0x02, 0x25, 0xc5, 0x14),
    18: (0x01, 0x11, 0x11, 0xc5, 0x05, 0x14),
    19: (0xc1, 0x14, 0x14, 0x01, 0x11, 0x14),
    20: (0x01, 0x05, 0x14, 0xc1, 0x14, 0x14),
    21: (0x01, 0x14, 0x14, 0xc1, 0x14, 0x14),
    22: (0xc1, 0x14, 0x14, 0x14, 0x01, 0x14),
    23: (0xc1, 0x14, 0x14, 0x01, 0x14, 0x04),
    24: (0xc1, 0x14, 0x14, 0x14, 0x04, 0x04),
    25: (0xc1, 0x14, 0x14, 0x04, 0x04, 0x04),
    26: (0xc1, 0x05, 0x14, 0x01, 0x14, 0x04),
    27: (0x01, 0x05, 0x14, 0xc1, 0x14, 0x04),
    28: (0x04, 0xc1, 0x11, 0x14, 0x01, 0x14),
    29: (0xc1, 0x14, 0x01, 0x14, 0x04, 0x04),
    30: (0x04, 0xc1, 0x11, 0x14, 0x04, 0x04),
    31: (0xc1, 0x14, 0x04, 0x04, 0x04, 0x04),
    32: (0xc4, 0x04, 0x04, 0x04, 0x04, 0x04),
}


def clamp(algo):
    """Coerce to a valid 1..32 algorithm number (mirrors render_algo's clamp)."""
    try:
        a = int(algo)
    except (TypeError, ValueError):
        return 1
    if a < 1:
        return 1
    if a > NUM_ALGOS:
        return NUM_ALGOS
    return a


def op_osc(op):
    """The AMY osc that carries DX7 operator `op` (1..6): osc = 8 - op."""
    return 8 - op


def routing(algo):
    """{'carriers','edges','feedback'} for DX7 algorithm `algo`, reconstructed
    by simulating amy render_algo()'s bus flow over the verbatim flag bytes.

    carriers: op numbers whose output reaches the final mix.
    edges:    (modulator_op, target_op) pairs -- op X modulates op Y.
    feedback: op numbers with feedback (the FB_IN bit; AMY self-modulates that
              operator with the voice `feedback` amount).
    All keyed by DX7 op number (1..6).
    """
    row = _ALGO_BYTES[clamp(algo)]
    producers = {1: [], 2: []}          # bus -> ops currently written to it
    carriers = []
    edges = []
    feedback = []
    for col in range(6):
        byte = row[col]
        op = 6 - col                    # column 0 == op6 ... column 5 == op1
        in_bus = (1 if (byte & IN_BUS_ONE) else
                  (2 if (byte & IN_BUS_TWO) else 0))
        if byte & FB_IN:
            feedback.append(op)
        # READ: this op is modulated by whoever currently sits on its input bus
        # (evaluated BEFORE this op writes -- matches render order).
        if in_bus:
            for mod in producers[in_bus]:
                edges.append((mod, op))
        # WRITE target
        if byte & OUT_BUS_ONE:
            out_bus = 1
        elif byte & OUT_BUS_TWO:
            out_bus = 2
        else:
            out_bus = 0                 # -> final mix: a carrier
        if out_bus == 0:
            carriers.append(op)
        elif in_bus == 1 and out_bus == 1:
            # render's SCRATCH case: read old BUS_ONE, then overwrite it (never
            # ADD, per the C comment) -> this op becomes bus one's sole producer
            producers[1] = [op]
        elif byte & OUT_BUS_ADD:
            producers[out_bus].append(op)   # accumulate onto the bus
        else:
            producers[out_bus] = [op]       # zeroed then written -> sole producer
    return {'carriers': carriers, 'edges': edges, 'feedback': feedback}


def role(algo, op):
    """'carrier' or 'modulator' for DX7 operator `op` in `algo`."""
    return 'carrier' if op in routing(algo)['carriers'] else 'modulator'


def has_feedback(algo, op):
    return op in routing(algo)['feedback']


def summary(algo):
    """A compact human summary: carriers + feedback op list, for the modal and
    tests. e.g. {'carriers': [1, 3], 'feedback': [6]}."""
    r = routing(algo)
    return {'carriers': sorted(r['carriers']),
            'feedback': sorted(r['feedback'])}


def layout(algo):
    """{op: (x, depth)} tidy-tree positions for the diagram, plus the routing.

    depth 0 = the carrier row (drawn at the bottom, on the output bar); each
    modulator sits one row ABOVE the operator it modulates. x is a column
    coordinate (may be fractional where a node centers over several children).
    DX7 algorithms are forests rooted at their carriers (each operator writes
    one bus, so it has a single parent), which lays out cleanly.

    Returns (pos, routing_dict, max_x, max_depth)."""
    r = routing(algo)
    children = {}
    for mod, tgt in r['edges']:
        children.setdefault(tgt, []).append(mod)
    pos = {}
    nxt = [0]

    def place(op, depth):
        kids = sorted(children.get(op, []))
        if kids:
            xs = [place(k, depth + 1) for k in kids]
            x = sum(xs) / len(xs)
        else:
            x = float(nxt[0])
            nxt[0] += 1
        pos[op] = (x, depth)
        return x

    for carrier in sorted(r['carriers']):
        place(carrier, 0)
    # Any operator not reached from a carrier (shouldn't happen for valid DX7
    # algos, but be safe) gets parked on its own column so it still draws.
    for op in range(1, 7):
        if op not in pos:
            pos[op] = (float(nxt[0]), 0)
            nxt[0] += 1
    max_x = max(x for x, _ in pos.values())
    max_depth = max(d for _, d in pos.values())
    return pos, r, max_x, max_depth
