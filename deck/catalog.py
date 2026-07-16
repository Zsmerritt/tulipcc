# catalog.py -- THE single owner of patch/engine/kit naming facts (E-8).
#
# The `0..127 juno6 / 128..255 dx7 / 256 piano` boundary set had drifted
# into four modules (shellmodel, amyparams.engine_of, deckcfg.type_of_patch,
# instrument/rack constants), and the sound-label logic knew nothing about
# GM instruments or synth kits. Pure + import-light at module level (host-
# testable); hardware-adjacent tables (gm/gmbig/drums_kit) import lazily.

JUNO_END = 128
DX_END = 256

# engine type -> the first valid patch number for it (None = no patch)
TYPE_FIRST_PATCH = {'juno6': 0, 'dx7': JUNO_END, 'piano': DX_END,
                    'gm': 0, 'gm2': 0, 'drums': None}


def engine_of(patch):
    """Engine type from a built-in patch number."""
    try:
        p = int(patch)
    except (TypeError, ValueError):
        p = 0
    if p < JUNO_END:
        return 'juno6'
    if p < DX_END:
        return 'dx7'
    return 'piano'


def engine_label(patch):
    """Family display label for a built-in patch: 'Juno-6' / 'DX7' / 'Piano'."""
    return {'juno6': 'Juno-6', 'dx7': 'DX7', 'piano': 'Piano'}[engine_of(patch)]


def gm_program_name(program, big=False):
    """Full GM program name from either bank; falls back to 'GM <n>'."""
    try:
        if big:
            import gmbig
            return gmbig.name(program)
        import gm
        return gm.name(program)
    except Exception:
        return 'GM %s' % program


def kit_display(kit):
    """Display name for a kit id (sampled int or 'synth:<pack>' string)."""
    try:
        import drums_kit
        return drums_kit.kit_name(kit)
    except Exception:
        return str(kit)


def sound_label(instr):
    """The sound an instrument makes, dispatched on its TYPE: kit name for
    drums, GM program name for gm/gm2 (the old patch_name lookup showed the
    JUNO name for a GM program number), else the built-in patch name."""
    t = instr.get('type')
    if t == 'drums':
        return kit_display(instr.get('kit', 384)) + ' kit'
    if t in ('gm', 'gm2'):
        return gm_program_name(instr.get('patch', 0), big=(t == 'gm2'))
    try:
        from patches import patches
        return patches[int(instr.get('patch', 0))]
    except Exception:
        return chip_sound(instr)   # compact tag when the table is missing


def chip_sound(instr):
    """Compact, type-aware sound tag for a top-bar chip."""
    t = instr.get('type')
    if t == 'drums':
        return 'Kit'
    if t in ('gm', 'gm2'):
        return 'GM%s' % instr.get('patch', 0)
    p = instr.get('patch', 0)
    try:
        p = int(p)
    except (TypeError, ValueError):
        p = 0
    if p < JUNO_END:
        return 'Juno%d' % p
    if p < DX_END:
        return 'DX%d' % (p - JUNO_END)
    return 'Piano'
