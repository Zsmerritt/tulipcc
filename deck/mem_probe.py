# mem_probe.py -- memory-budget snapshot, runs on CURRENT firmware (no reflash).
# Host: python tools/qexec.py deck/mem_probe.py MEM:      (run from PowerShell)
#
# Prints one MEM: line per fact. min_free is the heap's LIFETIME minimum since
# boot, so running this once after exercising the deck (kit load, WiFi, big UI
# screen) captures the worst moment automatically. Do not reboot between steps.
import esp32, os, gc

# Per-region heap info: 4-tuples (total, free, largest_free_block, min_free).
# Regions with total >= 4MB are PSRAM; everything else is internal SRAM.
int_tot = int_free = int_min = 0
int_largest = 0
for r in esp32.idf_heap_info(esp32.HEAP_DATA):
    kind = 'psram' if r[0] >= 4 * 1024 * 1024 else 'internal'
    print('MEM: region %s total=%d free=%d largest=%d min_free=%d' % ((kind,) + r))
    if kind == 'internal':
        int_tot += r[0]
        int_free += r[1]
        int_min += r[3]
        if r[2] > int_largest:
            int_largest = r[2]
print('MEM: INTERNAL_SUM total=%d free=%d largest_block=%d min_free_sum=%d'
      % (int_tot, int_free, int_largest, int_min))
for r in esp32.idf_heap_info(esp32.HEAP_EXEC):
    print('MEM: region exec total=%d free=%d largest=%d min_free=%d' % r)

# MicroPython GC heap (inside the 2MB PSRAM MP heap)
print('MEM: mp_heap alloc=%d free=%d' % (gc.mem_alloc(), gc.mem_free()))

# Filesystem occupancy (bytes): frsize*blocks / frsize*bfree
for mnt in ('/user', '/sys'):
    try:
        s = os.statvfs(mnt)
        print('MEM: fs %s total=%d free=%d used=%d'
              % (mnt, s[1] * s[2], s[1] * s[3], s[1] * (s[2] - s[3])))
    except OSError as e:
        print('MEM: fs %s ERR %s' % (mnt, e))
print('MEM: done')
